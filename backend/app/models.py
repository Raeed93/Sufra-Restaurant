"""Database tables.

users        one row per person who talks to the agent
preferences  one row per remembered fact about a user

A preference is never edited in place. When it changes, the old row gets
`superseded_at` set and points at the new row via `superseded_by_id`.
"Active" preferences are the rows where superseded_at IS NULL. This keeps
a history ("used to like spicy food") and makes changes auditable.
"""
from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PreferenceKind(str, Enum):
    allergy = "allergy"          # hard rule: never recommend items containing it
    diet = "diet"                # e.g. vegetarian, vegan
    like = "like"                # favourite foods, ingredients, cuisines
    dislike = "dislike"          # ingredients or dishes to avoid (soft rule)
    spice = "spice"              # preferred spice tolerance
    budget = "budget"            # typical spend per meal
    other = "other"              # anything else worth remembering


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(100))
    # Filled in on Day 2 when login is added. Nullable for now.
    password_hash: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    preferences: Mapped[list["Preference"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Preference(Base):
    __tablename__ = "preferences"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[PreferenceKind] = mapped_column(SAEnum(PreferenceKind, name="preference_kind"))
    value: Mapped[str] = mapped_column(String(200))
    # What the user actually said, so we can explain where a memory came from.
    source_text: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_by_id: Mapped[int | None] = mapped_column(ForeignKey("preferences.id"))

    user: Mapped[User] = relationship(back_populates="preferences")

    @property
    def is_active(self) -> bool:
        return self.superseded_at is None
