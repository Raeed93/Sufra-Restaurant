"""Passwords and login tokens.

- Passwords are hashed with bcrypt (slow on purpose, salted per user).
- After login the server signs a JWT holding only the user id, and sends it
  in an httpOnly cookie. JavaScript can't read httpOnly cookies, so an XSS
  bug in the page can't steal the token.
- Every protected route gets the user from the token, never from the request
  body, so a client can't claim to be someone else.
"""
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.db import get_db
from app.models import User

COOKIE_NAME = "sufra_token"
ALGORITHM = "HS256"

# Used when a username doesn't exist, so a failed login takes the same time
# either way and doesn't reveal which usernames are registered.
_DUMMY_HASH = bcrypt.hashpw(b"not-a-real-password", bcrypt.gensalt())


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        bcrypt.checkpw(password.encode(), _DUMMY_HASH)
        return False
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def create_user(db: Session, username: str, password: str) -> User:
    username = username.strip().lower()
    if db.scalar(select(User).where(User.username == username)):
        raise ValueError("username already taken")
    user = User(username=username, password_hash=hash_password(password))
    db.add(user)
    db.commit()
    return user


def authenticate(db: Session, username: str, password: str) -> User | None:
    user = db.scalar(select(User).where(User.username == username.strip().lower()))
    if not verify_password(password, user.password_hash if user else None):
        return None
    return user


def create_token(user_id: int) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(hours=config.JWT_EXPIRE_HOURS),
    }
    return jwt.encode(payload, config.jwt_secret(), algorithm=ALGORITHM)


def set_login_cookie(response: Response, user_id: int) -> None:
    response.set_cookie(
        COOKIE_NAME,
        create_token(user_id),
        httponly=True,
        secure=config.COOKIE_SECURE,
        samesite="lax",
        max_age=config.JWT_EXPIRE_HOURS * 3600,
        path="/",
    )


def clear_login_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """FastAPI dependency: the logged-in user, or 401."""
    unauthorized = HTTPException(status.HTTP_401_UNAUTHORIZED, "Not logged in")
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise unauthorized
    try:
        payload = jwt.decode(token, config.jwt_secret(), algorithms=[ALGORITHM])
        user_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise unauthorized
    user = db.get(User, user_id)
    if user is None:
        raise unauthorized
    return user