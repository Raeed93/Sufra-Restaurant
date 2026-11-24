"""Long-term memory: reading and changing a user's preferences.

Every function takes a user_id and filters by it, so one user can never read
or change another user's preferences (the same IDOR lesson as PulseMD).
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Preference, PreferenceKind, User, utcnow


def get_or_create_user(db: Session, username: str) -> tuple[User, bool]:
    """Return (user, created). Usernames are case-insensitive."""
    username = username.strip().lower()
    user = db.scalar(select(User).where(User.username == username))
    if user:
        return user, False
    user = User(username=username)
    db.add(user)
    db.commit()
    return user, True


def active_preferences(db: Session, user_id: int) -> list[Preference]:
    stmt = (
        select(Preference)
        .where(Preference.user_id == user_id, Preference.superseded_at.is_(None))
        .order_by(Preference.kind, Preference.created_at)
    )
    return list(db.scalars(stmt))


def save_preference(
    db: Session, user_id: int, kind: str, value: str, source_text: str | None = None
) -> Preference:
    """Add a preference. Skips exact duplicates of an active preference."""
    kind_enum = PreferenceKind(kind)  # raises ValueError on an unknown kind
    value = value.strip().lower()
    for existing in active_preferences(db, user_id):
        if existing.kind == kind_enum and existing.value == value:
            return existing
    pref = Preference(user_id=user_id, kind=kind_enum, value=value, source_text=source_text)
    db.add(pref)
    db.commit()
    return pref


def _get_owned_active(db: Session, user_id: int, preference_id: int) -> Preference:
    pref = db.get(Preference, preference_id)
    # Same error whether it doesn't exist or belongs to someone else,
    # so the response doesn't leak which IDs exist.
    if pref is None or pref.user_id != user_id or pref.superseded_at is not None:
        raise LookupError(f"No active preference with id {preference_id}")
    return pref


def update_preference(
    db: Session,
    user_id: int,
    preference_id: int,
    new_value: str,
    new_kind: str | None = None,
    source_text: str | None = None,
) -> Preference:
    """Replace a preference with a new one, keeping the old row as history."""
    old = _get_owned_active(db, user_id, preference_id)
    new = Preference(
        user_id=user_id,
        kind=PreferenceKind(new_kind) if new_kind else old.kind,
        value=new_value.strip().lower(),
        source_text=source_text,
    )
    db.add(new)
    db.flush()  # assigns new.id
    old.superseded_at = utcnow()
    old.superseded_by_id = new.id
    db.commit()
    return new


def remove_preference(db: Session, user_id: int, preference_id: int) -> Preference:
    """Retire a preference with no replacement (e.g. 'I'm not vegetarian any more')."""
    pref = _get_owned_active(db, user_id, preference_id)
    pref.superseded_at = utcnow()
    db.commit()
    return pref


def format_for_prompt(prefs: list[Preference]) -> str:
    """Render active preferences as lines the model can read and refer to by id."""
    if not prefs:
        return "(nothing remembered yet)"
    return "\n".join(f"- [id={p.id}] {p.kind.value}: {p.value}" for p in prefs)
