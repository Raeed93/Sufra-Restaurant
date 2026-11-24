# Sufra — AI Ordering Assistant

A conversational agent that recommends dishes from a restaurant menu and remembers each
user's preferences across conversations. Built for the Foodics AI Engineer take-home.

> Day 1 status: menu, database, memory and agent loop working from the terminal.
> Web UI, login and deployment come next.

## Run locally

```bash
docker compose up -d db                  # Postgres on :5432
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                     # add your GROQ_API_KEY
python cli.py --user raed --debug        # chat in the terminal
pytest                                   # tests use SQLite, no Postgres needed
```

## Layout

```
backend/
  app/data/menu.json   25 items: breakfast, lunch, dinner, snacks
  app/menu.py          load menu + exact-filter search
  app/models.py        users and preferences tables (SQLAlchemy)
  app/memory.py        read / save / update / remove preferences, scoped per user
  app/tools.py         tool schemas the model can call + the code that runs them
  app/agent.py         the tool-calling loop (Groq)
  cli.py               terminal chat
  tests/               menu, memory, user isolation, agent loop
```

## Key decisions (so far)

- **Grounding through tools.** The model never sees the full menu. It must call
  `search_menu`, so every recommendation comes from real menu rows.
- **Filters, not vector search.** 25 items fit in memory, and allergens need exact
  rules. A similarity search can't guarantee "no peanuts"; a filter can.
- **Preferences are versioned, not overwritten.** A change marks the old row as
  superseded and links it to the new one, so history is kept and auditable.
- **Every memory query is scoped by user_id.** Users can't read or edit each
  other's preferences, even with a valid preference id (tested).
- **No silent model fallback.** `GROQ_MODEL` must be set; the app fails loudly otherwise.
- **Hand-written agent loop** instead of a framework: ~100 lines, fully testable.
