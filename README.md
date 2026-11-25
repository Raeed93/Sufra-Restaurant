# Sufra — AI Ordering Assistant

A conversational agent that recommends dishes from a Saudi restaurant menu and remembers
each user's preferences across conversations. Built for the Foodics AI Engineer take-home.

**Live app: https://sufra.pulsemd.live** — create an account, or use the test accounts in
the submission notes.

**Part 2 challenge: Reliability & Evaluation.** Testing the baseline showed the model
saying the right thing in the moment but getting memory or grounding wrong in ways that
only surface later: an allergy half-saved, dishes and prices invented, a friend's allergy
saved to the wrong person. Prompt rules reduced these but couldn't guarantee them. So the
safety-critical parts now run in code, and an evaluation suite measures the difference on
the same code with the safety layer switched off and on.

- Findings log (what broke, in order): [`docs/findings.md`](docs/findings.md)
- Eval results: [`backend/evals/results/`](backend/evals/results/) · first run: [`docs/eval-run1-baseline.md`](docs/eval-run1-baseline.md)

---

## Try it

1. Create an account and say *"I'm allergic to nuts and I don't like very spicy food."*
   The **What I remember** panel fills in. Allergies show in red.
2. Ask *"Something to share as a snack?"* Recommendations appear as cards (name, price,
   allergens from the menu data). Open **What the assistant checked** to see every tool call.
3. Click **New conversation** and ask for dinner. Nuts are still avoided: memory comes from
   the database, not the chat history.
4. Say *"Actually I love spicy food now."* The spice level changes in the panel.
5. Sign out and create a second account. Its memory is empty.

Notes under a reply show what the safety layer did: yellow when it saved something
(e.g. *Safety net saved allergy: peanuts*), red when it blocked something
(e.g. *Blocked Peanut Chili Chicken Bites: contains peanuts*).

## Run locally

```bash
docker compose up -d db                     # Postgres on 127.0.0.1:5442
cd backend
python3 -m venv ../.venv && source ../.venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                        # add GROQ_API_KEY and JWT_SECRET
pytest                                      # 64 tests, no API key needed
uvicorn app.main:app --reload --port 8000   # API, docs at /docs

cd ../frontend && npm install && npm run dev  # UI at http://localhost:5173
```

Terminal chat without the UI: `python cli.py --user raed --debug`.

**Requirements:** Python 3.12, Node 20, Docker, a [Groq](https://console.groq.com) API key.

## Architecture

```
browser ─HTTPS─► host nginx ─► web container (nginx: React build, /api proxy, rate limits)
                                   │
                                   └─/api─► FastAPI ─► agent loop ─► Groq (gpt-oss-120b, fallback gpt-oss-20b)
                                              │           ├─ tools: search_menu, recommend,
                                              │           │         save / update / remove_preference
                                              │           └─ safety layer (code, not prompts)
                                              └─► Postgres: users, preferences, conversations, messages
```

| Path | Role |
|---|---|
| `backend/app/data/menu.json` | 25 dishes in 4 categories: price, diet, spice 0–3, ingredients, allergens |
| `backend/app/menu.py` | Exact-filter menu search |
| `backend/app/memory.py` | Versioned preferences, always scoped by `user_id` |
| `backend/app/tools.py` | Tool schemas and handlers, including `recommend` |
| `backend/app/safety.py` | Safety net, item checks, reply-text checks |
| `backend/app/agent.py` | Hand-written tool-calling loop; `SAFETY_MODE=on/off`; fallback model |
| `backend/app/auth.py` | bcrypt passwords, JWT in an httpOnly cookie |
| `backend/app/main.py` | FastAPI routes under `/api` |
| `backend/evals/` | 22 scripted conversations + runner that scores both modes |
| `frontend/` | React chat: dish cards, memory panel, "what the assistant checked" |

## Key decisions

- **Grounding through tools.** The model never sees the whole menu; it searches with filters,
  and with the safety layer on it recommends by menu id. Names and prices on the cards come
  from `menu.json`, never from the model's text.
- **Filters, not vector search.** 25 dishes fit in memory, and allergens need exact rules.
  Similarity search can't guarantee "no peanuts"; a filter can.
- **Preferences are versioned, not overwritten.** A change marks the old row as superseded and
  links it to the new one; a removal leaves it with no successor. The prompt lists recently
  removed preferences so the model stops applying them mid-chat. Spice and budget can only
  have one active value, enforced in code.
- **Identity comes from a signed token, never from the request or the model.** Tools have no
  `user_id` argument. Cross-user access returns 404 (tested).
- **Hand-written agent loop** (~250 lines with the safety layer) instead of a framework, so
  every step is testable and explainable.
- **Fail loudly.** Missing model name or secrets stop the app at startup. Provider errors
  return a clear 502, never invented fallback text. The fallback model is labelled in the UI.
- **Groq + gpt-oss-120b** for fast, cheap tool calling. Groq's free tier caps tokens per day
  per model, so the app falls back to gpt-oss-20b on a rate limit (see finding 8).

## Part 2: the safety layer

Each piece fixes a failure found by manual testing or the eval suite
([`docs/findings.md`](docs/findings.md)):

| Problem seen | Fix in code |
|---|---|
| "Allergic to nuts" saved only `tree_nuts`; a later chat recommended peanut bites as "nut-free" | **Safety net** spots allergy words, diets and disliked menu ingredients in the user's message and saves them before the model runs |
| The model could recommend a dish containing a saved allergen | **`recommend` tool**: the model passes menu ids; code checks allergies, diet and budget, rejects unsafe items with a reason, and builds each card from `menu.json` |
| The model invented three dishes with prices when nothing matched; another run wrote a wrong price | **Reply check** scans the final text for dishes not on the menu, wrong prices, or unsafe dishes; asks for one rewrite, then falls back to a safe reply |
| A friend's allergies were saved to the user | `save_preference` refuses when the message is about someone else |
| ~1 in 4 turns crashed: the model sent `null` for unused filters and Groq rejected the call | Optional tool parameters accept `null` (both modes); unparseable output is retried once |

`SAFETY_MODE=off` runs the original baseline, so both can be measured on the same code.

### Results

Eval suite, 22 cases, one run each, `openai/gpt-oss-20b` (the 120b model's free daily budget
was reserved for the live app). All checks are code, not an LLM judge.

| Check | Safety off | Safety on |
|---|---|---|
| Allergen safe (no dish with the user's **real** allergens) | 12/12 | 12/12 |
| Constraints (diet, budget, dislikes) | 7/7 | 7/7 |
| Grounded (no invented dishes or prices) | 22/22 | 22/22 |
| Memory correct at the end | 10/14 (71%) | **14/14 (100%)** |
| All checks | 18/22 (82%) | **22/22 (100%)** |
| Median model calls / tokens per turn | 2 / 2.6K | 3 / 5.0K |

"Safety on" includes the two cases that failed in the full run and passed after fixes
(a crash now retried, and the dislike safety net), re-run individually.

What this does and doesn't show:

- **Memory is where the safety net earns its keep.** With it off, the model lost `peanuts`
  twice, `gluten` once and a dislike once, the same bug as finding 1 on a second model.
- **Allergen safety was 100% in both modes in this run**: the model happened not to recommend
  an unsafe dish. One run of 22 cases is too small to measure a rare event. The blocking
  itself is proven by unit tests, and the first run (gpt-oss-120b) showed the baseline
  inventing a dish and a price.
- **The first full run was mostly crashes**: 10 of 44 runs failed on the `null` argument bug
  above, which never appeared in manual testing. That is the strongest argument for the
  eval suite.
- **The safety layer roughly doubles cost per turn** (the `recommend` call is an extra round
  trip). Latency numbers in the result files are inflated by free-tier rate limiting;
  manual turns take 1.5–3 s.

```bash
cd backend
python -m evals.run_evals                                  # 22 cases, safety off then on
python -m evals.run_evals --model openai/gpt-oss-20b --modes on --cases dislikes_chicken
python -m evals.run_evals --repeat 3                       # more runs per case
```

The runner stops cleanly if Groq's daily limit is hit and prints the command to resume.

## Deployment

Runs on an existing EC2 instance next to another app, fully separated:

- Own Compose project (`-p sufra`), network and database volume. Only `127.0.0.1:8300` is
  published.
- `docker-compose.pulse.yml` joins just the web container to the host's existing nginx
  network, as `sufra-web`. That nginx terminates HTTPS (Let's Encrypt) and resolves
  `sufra-web` per request, so the other site still starts if Sufra is down.
- The web container sets security headers (CSP, HSTS, frame and sniffing protection) and
  rate-limits chat (12/min per visitor) and login (10/min), using the real client IP passed
  by the front proxy.

```bash
cp .env.production.example .env.production   # fill in secrets, chmod 600
docker compose -f docker-compose.prod.yml -f docker-compose.pulse.yml \
  --env-file .env.production -p sufra up -d --build
```

Without a front proxy, use `docker-compose.prod.yml` alone and put any TLS proxy in front of
port 8300.

## What I left out, and why

- **Voice ordering in Arabic.** Groq offers Whisper (speech to text) and a Saudi-dialect
  text-to-speech model, so it's a natural next step for a Saudi restaurant platform, but it's
  a different challenge from reliability.
- **Multiple restaurants or POS integration.** The assignment allows one menu; reliability
  mattered more for a food-allergy use case.
- **An LLM judge in the evals.** Code checks are cheaper, deterministic and explainable.
  A judge would help for tone and helpfulness, which these checks don't measure.
- **Phone-number login.** Real restaurant customers identify by phone (OTP); username and
  password is enough to demonstrate returning users and isolation.

## Known limitations

- The safety net is keyword-based: it can miss unusual phrasing and deliberately skips
  messages about other people. The model still handles those.
- The reply check finds dishes written in **bold** or with a price, which is how these models
  format them. A plain-text invented dish could slip past.
- Eval cases are small and hand-written, and results vary between runs; `--repeat` helps.
- Allergens come from the menu data. Cross-contamination isn't modelled.
- Removing an allergy is one click; a confirmation step would be safer.
- Tables are created with `create_all`; a production system would use Alembic migrations.
- Groq's free tier limits tokens per day per model. Heavy use can exhaust both models.
