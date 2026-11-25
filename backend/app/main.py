"""HTTP API.

All routes live under /api so nginx (and the Vite dev server) can send
/api/* to this service and everything else to the frontend, on one origin.
One origin means no CORS setup and the login cookie just works.

Run locally:  uvicorn app.main:app --reload --port 8000
"""
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Response, status
from sqlalchemy.orm import Session

from app import auth, config, conversations, memory, schemas
from app.agent import AgentError, run_turn
from app.config import groq_model, jwt_secret
from app.db import get_db, init_db
from app.models import User


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fail at startup, not on the first request, if required settings are missing.
    jwt_secret()
    groq_model()
    init_db()
    yield


app = FastAPI(title="Sufra Ordering Assistant", lifespan=lifespan)


def _prefs_out(db: Session, user_id: int) -> list[schemas.PreferenceOut]:
    return [
        schemas.PreferenceOut(id=p.id, kind=p.kind.value, value=p.value)
        for p in memory.active_preferences(db, user_id)
    ]


@app.get("/api/health")
def health():
    return {"status": "ok", "safety_mode": config.SAFETY_MODE}


# ---------- auth ----------

@app.post("/api/auth/register", response_model=schemas.UserOut, status_code=201)
def register(body: schemas.Credentials, response: Response, db: Session = Depends(get_db)):
    try:
        user = auth.create_user(db, body.username, body.password)
    except ValueError:
        raise HTTPException(status.HTTP_409_CONFLICT, "Username already taken")
    auth.set_login_cookie(response, user.id)
    return schemas.UserOut(id=user.id, username=user.username)


@app.post("/api/auth/login", response_model=schemas.UserOut)
def login(body: schemas.Credentials, response: Response, db: Session = Depends(get_db)):
    user = auth.authenticate(db, body.username, body.password)
    if user is None:
        # Same message for unknown user and wrong password.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username or password")
    auth.set_login_cookie(response, user.id)
    return schemas.UserOut(id=user.id, username=user.username)


@app.post("/api/auth/logout", status_code=204)
def logout(response: Response):
    auth.clear_login_cookie(response)


# ---------- user data ----------

@app.get("/api/me", response_model=schemas.MeOut)
def me(user: User = Depends(auth.current_user), db: Session = Depends(get_db)):
    return schemas.MeOut(
        user=schemas.UserOut(id=user.id, username=user.username),
        preferences=_prefs_out(db, user.id),
    )


@app.delete("/api/me/preferences/{preference_id}", status_code=204)
def forget_preference(
    preference_id: int, user: User = Depends(auth.current_user), db: Session = Depends(get_db)
):
    try:
        memory.remove_preference(db, user.id, preference_id)
    except LookupError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Preference not found")


@app.get("/api/conversations", response_model=list[schemas.ConversationOut])
def list_conversations(user: User = Depends(auth.current_user), db: Session = Depends(get_db)):
    return [
        schemas.ConversationOut(id=c.id, title=c.title, created_at=c.created_at.isoformat())
        for c in conversations.list_for_user(db, user.id)
    ]


@app.get("/api/conversations/{conversation_id}/messages", response_model=list[schemas.MessageOut])
def get_messages(
    conversation_id: int, user: User = Depends(auth.current_user), db: Session = Depends(get_db)
):
    conv = conversations.get_owned(db, user.id, conversation_id)
    if conv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    return conversations.visible_messages(conv)


# ---------- chat ----------

@app.post("/api/chat", response_model=schemas.ChatOut)
def chat(body: schemas.ChatIn, user: User = Depends(auth.current_user), db: Session = Depends(get_db)):
    if body.conversation_id is None:
        conv = conversations.create(db, user.id, body.message)
    else:
        conv = conversations.get_owned(db, user.id, body.conversation_id)
        if conv is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")

    history = conversations.load_history(conv)
    already_stored = len(history)
    try:
        result = run_turn(db, user, history, body.message)
    except AgentError as exc:
        # Loud in the logs, generic to the user (don't leak provider details).
        print(f"[agent error] user={user.id} conv={conv.id}: {exc}")
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "The assistant is unavailable, please try again.")

    # run_turn appended this turn's messages to `history`; store only those.
    conversations.append(db, conv, history[already_stored:])

    return schemas.ChatOut(
        conversation_id=conv.id,
        reply=result.reply,
        recommendations=[schemas.RecommendationOut(**r) for r in result.recommendations],
        safety_events=result.safety_events,
        tool_calls=[schemas.ToolCallOut(**c) for c in result.tool_calls],
        preferences=_prefs_out(db, user.id),
    )
