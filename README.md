# Sufra — AI Ordering Assistant

A conversational agent that recommends dishes from a Saudi restaurant menu and remembers
each user's preferences across conversations. Built for the Foodics AI Engineer take-home.

**Part 2 challenge: Reliability & Evaluation.** Testing the baseline showed the model
saying the right thing in the moment but getting memory or grounding wrong in ways that
only show up later (an allergy half-saved, dishes invented, a friend's allergy saved to
the wrong person). Prompt rules reduced these but couldn't guarantee them. So the safety-
critical parts now run in code, and an evaluation suite measures the difference.

- Live app: _URL_ · test accounts: _see submission notes_
- Results: [`backend/evals/results/`](backend/evals/results/) · findings log: [`docs/findings.md`](docs/findings.md)

## Run locally

```bash
docker compose up -d db                     # Postgres on 127.0.0.1:5442
cd backend
python3 -m venv ../.venv && source ../.venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                        # add GROQ_API_KEY and JWT_SECRET
pytest                                      # unit + API tests, no API key needed
uvicorn app.main:app --reload --port 8000   # API, docs at /docs

cd ../frontend && npm install && npm run dev  # UI at http://localhost:5173
```

Terminal chat without the UI: `python cli.py --user raed --debug`.

## Architecture

```
browser ── nginx (web container) ──/api──► FastAPI ──► agent loop ──► Groq (gpt-oss-120b)
              │ static React app               │            │
              │                                 │            ├─ tools: search_menu, recommend,
              │                                 │            │         save/update/remove_preference
              │                                 │            └─ safety layer (code, not prompts)
              │                                 └─► Postgres: users, preferences, conversations, messages
```

| Path | Role |
|---|---|
| `backend/app/data/menu.json` | 25 dishes in 4 categories with price, diet, spice 0–3, ingredients, allergens |
| `backend/app/menu.py` | Exact-filter menu search |
| `backend/app/memory.py` | Versioned preferences, always scoped by `user_id` |
| `backend/app/tools.py` | Tool schemas and handlers, including `recommend` |
| `backend/app/safety.py` | Allergy safety net, item checks, reply-text checks |
| `backend/app/agent.py` | Hand-written tool-calling loop; `SAFETY_MODE=on/off` |
| `backend/app/auth.py` | bcrypt passwords, JWT in an httpOnly cookie |
| `backend/app/main.py` | FastAPI routes under `/api` |
| `backend/evals/` | Scripted conversations + runner that scores both modes |
| `frontend/` | React chat: dish cards, memory panel, "what the assistant checked" |

## Key decisions

- **Grounding through tools.** The model never sees the full menu; it searches with filters.
- **Filters, not vector search.** 25 dishes fit in memory, and allergens need exact rules.
  Similarity search can't guarantee "no peanuts"; a filter can.
- **Preferences are versioned, not overwritten.** Changing one marks the old row as
  superseded and links it to the new one; removing one leaves it with no successor. The
  prompt lists recently removed preferences so the model stops applying them mid-chat.
- **Identity comes from a signed token, never from the request or the model.** Tools have
  no `user_id` argument. Cross-user access returns 404 (tested).
- **No silent fallbacks.** Missing model name or secrets stop the app at startup.
- **Hand-written agent loop** (~150 lines) instead of a framework, so every step is testable.

## Part 2: the safety layer

Each piece fixes a failure from [`docs/findings.md`](docs/findings.md):

| Problem seen in the baseline | Fix in code |
|---|---|
| "Allergic to nuts" saved only `tree_nuts`; a later chat recommended peanut bites as "nut-free" | **Allergy safety net** spots allergy words in the user's message and saves the allergen codes before the model runs |
| Model could recommend a dish containing a saved allergen | **`recommend` tool**: the model passes menu ids; code checks allergies, diet and budget, rejects unsafe items with a reason, and builds the card from `menu.json` |
| Model invented three dishes with prices when nothing matched | **Reply check** scans the final text for dishes not on the menu, wrong prices, or unsafe dishes; asks for one rewrite, then falls back to a safe reply |
| Model saved a friend's allergies to the user | `save_preference` refuses when the message is about someone else |
| Spice saved twice with different values | One active value for spice and budget, enforced in `memory.py` |

`SAFETY_MODE=off` runs the original baseline, so both can be measured on the same code.

### Evaluation

```bash
cd backend
python -m evals.run_evals                       # 22 cases, safety off then on
python -m evals.run_evals --repeat 3            # more runs per case
python -m evals.run_evals --model qwen/qwen3.8-27b
```

All checks are code, not an LLM judge: allergen safety against the user's **real**
allergies (not just what was saved), diet/budget/dislikes, invented dishes or prices,
memory state at the end, and required facts in the reply. Also reports latency, model
calls and tokens per turn, since the safety layer isn't free.

_Results table goes here after the final run._

## Deployment

`docker-compose.prod.yml` runs Postgres, FastAPI and an nginx container (React build +
`/api` proxy, security headers, rate limits on chat and login). Only `127.0.0.1:8300` is
published; the host's nginx terminates HTTPS. See `.env.production.example`.

## Known limitations

- The allergy safety net is keyword-based: it can miss unusual phrasing and deliberately
  skips messages about other people. The model still handles those.
- The reply check finds dishes written in **bold** with a price, which is how this model
  formats them. A plain-text invented dish could slip past.
- Evaluation cases are small and hand-written; results vary run to run, so `--repeat` helps.
- Allergens come from the menu data. Cross-contamination isn't modelled.
- Tables are created with `create_all`; a real deployment would use Alembic migrations.
