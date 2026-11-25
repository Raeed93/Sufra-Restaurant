"""Storing and loading chat threads. Every query is scoped by user_id."""
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config, menu
from app.models import Conversation, Message
from app.tools import recommendation_card


def create(db: Session, user_id: int, first_message: str) -> Conversation:
    title = first_message.strip().replace("\n", " ")[:60] or "New chat"
    conv = Conversation(user_id=user_id, title=title)
    db.add(conv)
    db.commit()
    return conv


def get_owned(db: Session, user_id: int, conversation_id: int) -> Conversation | None:
    """None if it doesn't exist OR belongs to someone else (callers return 404 either way)."""
    conv = db.get(Conversation, conversation_id)
    if conv is None or conv.user_id != user_id:
        return None
    return conv


def list_for_user(db: Session, user_id: int) -> list[Conversation]:
    stmt = (
        select(Conversation)
        .where(Conversation.user_id == user_id)
        .order_by(Conversation.created_at.desc())
    )
    return list(db.scalars(stmt))


def load_history(conv: Conversation) -> list[dict]:
    """Messages in the model's format, trimmed to the most recent ones.

    Trimming always starts at a user message, so we never send a tool result
    without the assistant message that asked for it (the API would reject that).
    """
    history = [m.data for m in conv.messages]
    limit = config.MAX_HISTORY_MESSAGES
    if len(history) <= limit:
        return history
    start = len(history) - limit
    while start < len(history) and history[start]["role"] != "user":
        start += 1
    return history[start:]


def append(db: Session, conv: Conversation, new_messages: list[dict]) -> None:
    for data in new_messages:
        db.add(Message(conversation_id=conv.id, role=data["role"], data=data))
    db.commit()


def visible_messages(conv: Conversation) -> list[dict]:
    """What the chat UI shows: user messages and final replies, with their dish cards.

    Cards aren't stored separately: they're rebuilt from the recommend tool calls
    in the history, using menu.json for names and prices.
    """
    out = []
    reasons: dict[str, dict[str, str]] = {}   # tool_call_id -> {item_id: reason}
    cards: list[dict] = []
    for m in conv.messages:
        d = m.data
        if m.role == "user":
            cards = []
            out.append({"role": "user", "content": d.get("content", ""),
                        "created_at": m.created_at.isoformat(), "recommendations": []})
        elif m.role == "assistant" and d.get("tool_calls"):
            for call in d["tool_calls"]:
                if call["function"]["name"] == "recommend":
                    try:
                        args = json.loads(call["function"]["arguments"] or "{}")
                        reasons[call["id"]] = {str(i.get("id")): str(i.get("reason", ""))
                                               for i in args.get("items", [])}
                    except (json.JSONDecodeError, AttributeError):
                        pass
        elif m.role == "tool" and d.get("tool_call_id") in reasons:
            try:
                accepted = json.loads(d.get("content") or "{}").get("accepted", [])
            except json.JSONDecodeError:
                accepted = []
            for a in accepted:
                item = menu.get_item(a.get("id", ""))
                if item:
                    cards = [c for c in cards if c["id"] != item["id"]]
                    cards.append(recommendation_card(item, reasons[d["tool_call_id"]].get(item["id"], "")))
        elif m.role == "assistant":
            out.append({"role": "assistant", "content": d.get("content", ""),
                        "created_at": m.created_at.isoformat(), "recommendations": cards})
            cards = []
    return out
