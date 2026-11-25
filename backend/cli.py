"""Terminal chat for testing the agent before the web UI exists.

Usage:
    python cli.py --user raed
    python cli.py --user raed --debug     # also prints every tool call

Commands while chatting:
    /prefs   show what the agent remembers about you
    /new     start a new conversation (memory is kept, chat history is cleared)
    /quit    exit
"""
import argparse
import time

from app import memory
from app.agent import AgentError, run_turn
from app.db import SessionLocal, init_db


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", required=True, help="username to chat as")
    parser.add_argument("--debug", action="store_true", help="print tool calls and timing")
    args = parser.parse_args()

    init_db()
    db = SessionLocal()
    user, created = memory.get_or_create_user(db, args.user)
    print(f"{'New user' if created else 'Welcome back'}: {user.username}  (/prefs /new /quit)\n")

    history: list[dict] = []
    try:
        while True:
            text = input("you > ").strip()
            if not text:
                continue
            if text == "/quit":
                break
            if text == "/new":
                history = []
                print("-- new conversation --\n")
                continue
            if text == "/prefs":
                print(memory.format_for_prompt(memory.active_preferences(db, user.id)), "\n")
                continue

            start = time.perf_counter()
            try:
                result = run_turn(db, user, history, text)
            except AgentError as exc:
                print(f"[error] {exc}\n")
                continue
            elapsed = time.perf_counter() - start

            if args.debug:
                for call in result.tool_calls:
                    print(f"  [tool] {call['name']}({call['arguments']})")
                    print(f"         -> {call['output'][:200]}")
            print(f"agent > {result.reply}\n")
            if args.debug:
                print(f"  [{elapsed:.1f}s, {len(result.tool_calls)} tool calls]\n")
    finally:
        db.close()


if __name__ == "__main__":
    main()
