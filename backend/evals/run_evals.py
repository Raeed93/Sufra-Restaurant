"""Run the evaluation suite against the real model.

    cd backend
    python -m evals.run_evals                      # both modes, all cases
    python -m evals.run_evals --modes on           # just the safety layer
    python -m evals.run_evals --cases dairy_dinner,vegan_dinner --repeat 3
    python -m evals.run_evals --model qwen/qwen3.8-27b

Uses a throwaway SQLite database, so your real data is never touched.
Writes evals/results/<timestamp>.json (full transcripts) and .md (summary table).

How a case is judged (all checks are code, no LLM judge):
  allergen_safe   no recommended dish contains one of the user's real allergens
  constraints_ok  no recommended dish breaks the user's diet, budget or dislikes
  grounded        no reply names a dish that isn't on the menu, or a wrong price
  memory_ok       the right preferences are saved (and wrong ones aren't) at the end
  text_ok         the final reply contains required facts (e.g. the real price)

"Recommended dishes" = the cards accepted by the recommend tool (safety on) plus
any menu dish written in bold with a price in the reply text (how the baseline
writes recommendations).
"""
import argparse
import json
import os
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
RESULTS.mkdir(exist_ok=True)
DB_FILE = RESULTS / "eval.db"
# Must be set before the app is imported.
os.environ["DATABASE_URL"] = f"sqlite:///{DB_FILE}"
sys.path.insert(0, str(HERE.parent))

from app import agent, config, memory, menu, safety  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from evals.cases import CASES  # noqa: E402

CHECKS = ["allergen_safe", "constraints_ok", "grounded", "memory_ok", "text_ok"]


class QuotaExhausted(RuntimeError):
    """Groq's daily token limit was hit. Results after this point would be fake failures."""


# ---------- judging ----------

def recommended_items(result: agent.TurnResult) -> list[dict]:
    items = {r["id"]: r for r in result.recommendations}
    for d in safety.dish_mentions(result.reply):
        if d["item"] is not None and d["price"] is not None:
            items.setdefault(d["item"]["id"], d["item"])
    return [menu.get_item(i) for i in items]


def violations(item: dict, truth: dict) -> dict[str, list[str]]:
    out = {"allergen": [], "constraint": []}
    hit = set(truth.get("allergies", [])) & set(item["allergens"])
    if hit:
        out["allergen"].append(f"{item['name']} contains {', '.join(sorted(hit))}")
    diet = truth.get("diet")
    if diet == "vegan" and not item["vegan"] or diet == "vegetarian" and not item["vegetarian"]:
        out["constraint"].append(f"{item['name']} is not {diet}")
    if "budget" in truth and item["price"] > truth["budget"]:
        out["constraint"].append(f"{item['name']} costs {item['price']} > {truth['budget']}")
    text = " ".join([item["name"], *item["ingredients"]]).lower()
    for word in truth.get("avoid_ingredients", []):
        if word in text:
            out["constraint"].append(f"{item['name']} contains {word}")
    return out


def judge(case: dict, turns: list[dict], active: list[tuple[str, str]]) -> dict:
    problems = {c: [] for c in CHECKS}
    truth = case.get("truth", {})
    for t in turns:
        if t.get("error"):
            for c in CHECKS:
                problems[c].append(f"agent error: {t['error']}")
            continue
        for item in t["recommended"]:
            v = violations(item, truth)
            problems["allergen_safe"] += v["allergen"]
            problems["constraints_ok"] += v["constraint"]
        problems["grounded"] += safety.find_invented_dishes(t["reply"])

    active_set = set(active)
    for kind, value in case.get("expect_saved", []):
        allowed = value if isinstance(value, list) else [value]
        if not any((kind, v) in active_set for v in allowed):
            problems["memory_ok"].append(f"missing {kind}={value}")
    for kind, value in case.get("expect_not_saved", []):
        if (kind, value) in active_set:
            problems["memory_ok"].append(f"should not have {kind}={value}")

    final = turns[-1]["reply"].lower() if turns else ""
    for s in case.get("expect_text", []):
        if s.lower() not in final:
            problems["text_ok"].append(f"final reply lacks '{s}'")

    applicable = {
        "allergen_safe": bool(truth.get("allergies")),
        "constraints_ok": any(k in truth for k in ("diet", "budget", "avoid_ingredients")),
        "grounded": True,
        "memory_ok": bool(case.get("expect_saved") or case.get("expect_not_saved")),
        "text_ok": bool(case.get("expect_text")),
    }
    return {c: (None if not applicable[c] else not problems[c]) for c in CHECKS} | {"problems": problems}


# ---------- running ----------

class Meter:
    """Wraps the model call to count calls and tokens."""

    def __init__(self):
        self.calls = 0
        self.tokens = 0
        self._real = agent._call_model

    def __call__(self, messages, allow_tools=True):
        response = self._real(messages, allow_tools)
        self.calls += 1
        usage = getattr(response, "usage", None)
        self.tokens += getattr(usage, "total_tokens", 0) or 0
        return response


def run_case(db, case: dict, mode: str, rep: int, meter: Meter) -> dict:
    user, _ = memory.get_or_create_user(db, f"eval_{mode}_{case['id']}_{rep}"[:50])
    for kind, value in case.get("seed", []):
        memory.save_preference(db, user.id, kind, value, source_text="(seeded)")

    turns = []
    for conversation in case["conversations"]:
        history: list[dict] = []
        for message in conversation:
            calls_before, tokens_before = meter.calls, meter.tokens
            start = time.perf_counter()
            try:
                result = agent.run_turn(db, user, history, message)
            except agent.AgentError as exc:
                db.rollback()
                if "rate_limit_exceeded" in str(exc) and "per day" in str(exc):
                    raise QuotaExhausted(str(exc)) from exc
                turns.append({"user": message, "reply": "", "error": str(exc), "recommended": [],
                              "seconds": time.perf_counter() - start})
                continue
            turns.append({
                "user": message,
                "reply": result.reply,
                "recommended": recommended_items(result),
                "tool_calls": [f"{c['name']}({c['arguments']})" for c in result.tool_calls],
                "safety_events": result.safety_events,
                "seconds": round(time.perf_counter() - start, 2),
                "model_calls": meter.calls - calls_before,
                "tokens": meter.tokens - tokens_before,
            })

    active = [(p.kind.value, p.value) for p in memory.active_preferences(db, user.id)]
    verdict = judge(case, turns, active)
    return {"case": case["id"], "finding": case.get("finding"), "mode": mode, "rep": rep,
            "verdict": verdict, "memory": active,
            "turns": [{**t, "recommended": [i["name"] for i in t["recommended"]]} for t in turns]}


def summarise(runs: list[dict], modes: list[str]) -> str:
    lines = []
    lines.append("| Check | " + " | ".join(f"safety {m}" for m in modes) + " |")
    lines.append("|---|" + "---|" * len(modes))
    for check in CHECKS + ["all checks"]:
        row = [check]
        for m in modes:
            rs = [r for r in runs if r["mode"] == m]
            if check == "all checks":
                vals = [all(v is not False for k, v in r["verdict"].items() if k in CHECKS) for r in rs]
            else:
                vals = [r["verdict"][check] for r in rs if r["verdict"][check] is not None]
            row.append(f"{sum(vals)}/{len(vals)} ({100 * sum(vals) / len(vals):.0f}%)" if vals else "-")
        lines.append("| " + " | ".join(row) + " |")

    lines.append("")
    lines.append("| Cost per turn | " + " | ".join(f"safety {m}" for m in modes) + " |")
    lines.append("|---|" + "---|" * len(modes))
    for label, key in [("median seconds", "seconds"), ("median model calls", "model_calls"),
                       ("median tokens", "tokens")]:
        row = [label]
        for m in modes:
            vals = [t[key] for r in runs if r["mode"] == m for t in r["turns"] if key in t]
            row.append(f"{statistics.median(vals):g}" if vals else "-")
        lines.append("| " + " | ".join(row) + " |")
    row = ["slowest turn (s)"]
    for m in modes:
        vals = [t["seconds"] for r in runs if r["mode"] == m for t in r["turns"]]
        row.append(f"{max(vals):.1f}" if vals else "-")
    lines.append("| " + " | ".join(row) + " |")

    lines.append("\n### Failed cases\n")
    for r in runs:
        failed = {k: r["verdict"]["problems"][k] for k in CHECKS if r["verdict"][k] is False}
        if failed:
            detail = "; ".join(f"{k}: {', '.join(v)}" for k, v in failed.items())
            lines.append(f"- **{r['case']}** (safety {r['mode']}, run {r['rep']}): {detail}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--modes", default="off,on")
    parser.add_argument("--cases", default="", help="comma-separated case ids (default: all)")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--model", default="", help="override GROQ_MODEL")
    parser.add_argument("--pause", type=float, default=1.0, help="seconds between cases (rate limits)")
    args = parser.parse_args()

    if args.model:
        os.environ["GROQ_MODEL"] = args.model
    # Never switch models mid-run: results must come from the model being evaluated.
    config.GROQ_FALLBACK_MODEL = None
    modes = [m.strip() for m in args.modes.split(",")]
    wanted = {c.strip() for c in args.cases.split(",") if c.strip()}
    cases = [c for c in CASES if not wanted or c["id"] in wanted]

    meter = Meter()
    agent._call_model = meter
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    runs = []
    stopped = None
    print(f"model={config.groq_model()} cases={len(cases)} repeat={args.repeat} modes={modes}")
    try:
        for mode in modes:
            config.SAFETY_MODE = mode
            for rep in range(1, args.repeat + 1):
                for case in cases:
                    db = SessionLocal()
                    try:
                        run = run_case(db, case, mode, rep, meter)
                    finally:
                        db.close()
                    runs.append(run)
                    ok = all(v is not False for k, v in run["verdict"].items() if k in CHECKS)
                    print(f"  [{mode}] {case['id']:<32} {'PASS' if ok else 'FAIL'}")
                    time.sleep(args.pause)
    except QuotaExhausted:
        stopped = (mode, case["id"])
        done = {r["case"] for r in runs if r["mode"] == mode}
        left = [c["id"] for c in cases if c["id"] not in done]
        print(f"\nSTOPPED: Groq daily token limit reached during [{mode}] {case['id']}.")
        print("The unfinished case is not counted. To finish later:")
        print(f"  python -m evals.run_evals --modes {mode} --cases {','.join(left)}")
        later = modes[modes.index(mode) + 1:]
        if later:
            print(f"  python -m evals.run_evals --modes {','.join(later)}")
    if not runs:
        return

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    model_slug = config.groq_model().replace("/", "_")
    header = (f"# Eval results {stamp}\n\nModel: `{config.groq_model()}` · cases: {len(cases)} · "
              f"repeats: {args.repeat} · total model calls: {meter.calls} · tokens: {meter.tokens}\n\n")
    if stopped:
        header += (f"**Partial run:** stopped at [{stopped[0]}] {stopped[1]} by Groq's daily token "
                   "limit. Cases not run are not counted.\n\n")
    summary = header + summarise(runs, modes)
    (RESULTS / f"{stamp}_{model_slug}.json").write_text(json.dumps(runs, indent=2, default=str))
    (RESULTS / f"{stamp}_{model_slug}.md").write_text(summary)
    print("\n" + summary)
    print(f"\nSaved to evals/results/{stamp}_{model_slug}.(json|md)")


if __name__ == "__main__":
    main()
