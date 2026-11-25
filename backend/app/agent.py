"""The agent loop.

One call to run_turn handles one user message:
  1. (safety on) Allergy safety net: save allergens the user just mentioned.
  2. Build the system prompt with the user's CURRENT active preferences.
  3. Ask the model. If it requests tools, run them and feed back the results.
  4. Repeat until the model replies with plain text (or the step cap is hit).
  5. (safety on) Reply check: if the text names dishes that don't exist or
     wrong prices, ask the model to rewrite once, then fall back to a safe reply.

SAFETY_MODE=off runs the Part 1 baseline (steps 2-4 only), so the evals can
compare both on the same code.

Written by hand instead of with an agent framework: it's short, and every
step can be explained and tested.
"""
import json
import logging
from dataclasses import dataclass, field

import groq
from sqlalchemy.orm import Session

from app import config, memory, menu, safety
from app.models import User
from app.tools import ToolContext, run_tool, tool_schemas

log = logging.getLogger("sufra.agent")

SYSTEM_PROMPT = """You are the ordering assistant for {restaurant}, a Saudi restaurant.
You help {name} decide what to order. Prices are in SAR.
The menu has four categories: breakfast, lunch, dinner and snacks.

What you remember about this user (from earlier conversations):
{preferences}

Removed by the user (no longer applies, even if mentioned earlier in this chat):
{removed}

This memory is the source of truth. If something said earlier in the chat
contradicts it, follow the memory: the user may have edited it since.

Rules:
1. Only recommend items returned by search_menu. Never invent dishes, ingredients or prices.
2. Allergies are strict. Always pass every remembered allergy in exclude_allergens.
3. Respect diet, dislikes, spice level and budget when you search.
4. When the user mentions a lasting preference (allergy, diet, likes, dislikes, spice, budget),
   save it with save_preference without asking for permission.
5. When the user changes a preference you already remember, use update_preference or
   remove_preference with its id. Do not save a contradicting duplicate.
6. Learn preferences through natural conversation. Ask at most one question at a time.
   Do not interrogate the user with a questionnaire.
7. Recommend 1-3 items. For each, give the name, price and a short reason linked to
   what you know about the user.
8. Keep replies short and friendly. Do not mention tools, ids or the database.
9. If the user says "nuts", that means BOTH tree_nuts and peanuts. Save each allergen
   as a separate allergy preference.
10. Preferences belong only to the person you are talking to. If they mention someone
    else's preferences (a friend, family member), use them for this order only and
    never save them. Say clearly that you can't save preferences for other people."""

# Replaces rule 7 when the safety layer is on.
SAFETY_RULES = """
11. To recommend dishes, call the recommend tool with item ids from search_menu and a
    short reason for each. The app shows each accepted dish as a card with its name,
    price and allergens. In your text, write one or two friendly sentences; you may
    refer to dishes by their menu names but do not write prices. If an item is
    rejected, pick another one or say honestly that nothing on the menu fits.
12. If nothing matches, say so. Never suggest dishes or categories that are not on the menu."""

REWRITE_INSTRUCTION = (
    "Automatic check found problems in your reply: {problems}. Rewrite your reply without "
    "these dishes. Only mention dishes you recommended with the recommend tool, and do not "
    "write prices."
)
FALLBACK_WITH_CARDS = "Here's what I found for you on the menu."
FALLBACK_NO_CARDS = (
    "I couldn't find a dish on our menu that matches that. Could you tell me a bit more "
    "about what you'd like?"
)


@dataclass
class TurnResult:
    reply: str
    tool_calls: list[dict] = field(default_factory=list)
    # Cards accepted by the safety check (safety mode only).
    recommendations: list[dict] = field(default_factory=list)
    # What the safety layer did this turn, in plain words (shown in the UI and the evals).
    safety_events: list[str] = field(default_factory=list)


class AgentError(RuntimeError):
    pass


_client: groq.Groq | None = None


def _get_client() -> groq.Groq:
    global _client
    if _client is None:
        # Retries 429 rate limits and 5xx errors with backoff.
        _client = groq.Groq(api_key=config.groq_api_key(), max_retries=4)
    return _client


def safety_on() -> bool:
    return config.SAFETY_MODE == "on"


def build_system_prompt(db: Session, user: User) -> str:
    prefs = memory.active_preferences(db, user.id)
    prompt = SYSTEM_PROMPT.format(
        restaurant=menu.load_menu()["restaurant"]["name"],
        name=user.display_name or user.username,
        preferences=memory.format_for_prompt(prefs),
        removed=memory.format_removed(memory.recently_removed(db, user.id)),
    )
    if safety_on():
        prompt = prompt.replace(
            "7. Recommend 1-3 items. For each, give the name, price and a short reason linked to\n"
            "   what you know about the user.",
            "7. Recommend 1-3 items, each with a short reason linked to what you know about the user.",
        ) + SAFETY_RULES
    return prompt


def _call_model(messages: list[dict], allow_tools: bool = True):
    """Call Groq, retrying once if the model produced a malformed tool call."""
    kwargs = dict(
        model=config.groq_model(),
        messages=messages,
        tools=tool_schemas(safety_on()),
        tool_choice="auto" if allow_tools else "none",
        temperature=0.3,
    )
    if config.REASONING_EFFORT:
        kwargs["reasoning_effort"] = config.REASONING_EFFORT
    for attempt in range(2):
        try:
            return _get_client().chat.completions.create(**kwargs)
        except groq.BadRequestError as exc:
            if "tool_use_failed" in str(exc) and attempt == 0:
                continue
            raise AgentError(f"Model request failed: {exc}") from exc
        except groq.APIError as exc:
            # Wrong model name, bad key, rate limit, network: fail loudly, but don't crash the app.
            raise AgentError(f"Groq error: {exc}") from exc
    raise AgentError("Model request failed twice")


def _allergy_safety_net(db: Session, user: User, message: str, result: TurnResult) -> None:
    for code in safety.detect_allergies(message):
        active = {(p.kind.value, p.value) for p in memory.active_preferences(db, user.id)}
        if ("allergy", code) in active:
            continue
        memory.save_preference(db, user.id, "allergy", code, source_text=message)
        event = f"Safety net saved allergy: {code.replace('_', ' ')}"
        result.safety_events.append(event)
        result.tool_calls.append({
            "name": "safety_net",
            "arguments": json.dumps({"allergy": code}),
            "output": json.dumps({"saved": {"kind": "allergy", "value": code}}),
        })
        log.info("user=%s %s", user.id, event)


def _check_reply(db: Session, user: User, history: list[dict], system_prompt: str, reply: str,
                 result: TurnResult) -> str:
    """Make sure the final text doesn't name invented dishes, wrong prices or unsafe dishes."""
    prefs = memory.active_preferences(db, user.id)
    problems = safety.check_reply_text(reply, prefs)
    if not problems:
        return reply
    result.safety_events.append("Reply check: " + "; ".join(problems))
    log.warning("reply check failed: %s", problems)

    # One rewrite. The correction is sent to the model but not stored in the history.
    messages = [
        {"role": "system", "content": system_prompt},
        *history,
        {"role": "assistant", "content": reply},
        {"role": "user", "content": REWRITE_INSTRUCTION.format(problems="; ".join(problems))},
    ]
    rewritten = _call_model(messages, allow_tools=False).choices[0].message.content or ""
    if rewritten and not safety.check_reply_text(rewritten, prefs):
        result.safety_events.append("Reply rewritten")
        return rewritten

    result.safety_events.append("Rewrite still failed: replaced with a safe reply")
    return FALLBACK_WITH_CARDS if result.recommendations else FALLBACK_NO_CARDS


def run_turn(db: Session, user: User, history: list[dict], user_message: str) -> TurnResult:
    """Process one user message. `history` is updated in place (no system message in it)."""
    history.append({"role": "user", "content": user_message})
    ctx = ToolContext(db=db, user_id=user.id, last_user_message=user_message)
    result = TurnResult(reply="")

    if safety_on():
        _allergy_safety_net(db, user, user_message, result)

    for _ in range(config.MAX_AGENT_STEPS):
        # Rebuilt every step so preferences saved mid-turn are visible straight away.
        system_prompt = build_system_prompt(db, user)
        messages = [{"role": "system", "content": system_prompt}, *history]
        response = _call_model(messages)
        msg = response.choices[0].message

        if not msg.tool_calls:
            reply = msg.content or ""
            if safety_on():
                result.recommendations = ctx.recommendations
                reply = _check_reply(db, user, history, system_prompt, reply, result)
            result.reply = reply
            history.append({"role": "assistant", "content": reply})
            return result

        history.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {"id": c.id, "type": "function",
                 "function": {"name": c.function.name, "arguments": c.function.arguments}}
                for c in msg.tool_calls
            ],
        })
        for call in msg.tool_calls:
            output = run_tool(ctx, call.function.name, call.function.arguments)
            result.tool_calls.append(
                {"name": call.function.name, "arguments": call.function.arguments, "output": output}
            )
            if call.function.name == "recommend":
                for r in json.loads(output).get("rejected", []):
                    result.safety_events.append(
                        f"Blocked {r.get('name') or r.get('id')}: {'; '.join(r['reasons'])}"
                    )
            history.append({"role": "tool", "tool_call_id": call.id, "content": output})

    raise AgentError(f"Agent did not finish within {config.MAX_AGENT_STEPS} steps")
