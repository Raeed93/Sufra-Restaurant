"""Long-term memory: reading and changing a user's preferences.

Every function takes a user_id and filters by it, so one user can never read
or change another user's preferences (the same IDOR lesson as PulseMD).
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import menu
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


# A user has only one spice level and one budget at a time. Saving a new one
# replaces the old one in code, instead of trusting the model to call update.
SINGLE_VALUE_KINDS = {PreferenceKind.spice, PreferenceKind.budget}


def save_preference(
    db: Session, user_id: int, kind: str, value: str, source_text: str | None = None
) -> Preference:
    """Add a preference.

    - Exact duplicates of an active preference are skipped.
    - Allergies must be a known allergen code, so they can be enforced as filters.
    - For single-value kinds (spice, budget), an existing value is replaced.
    """
    kind_enum = PreferenceKind(kind)  # raises ValueError on an unknown kind
    value = value.strip().lower()
    if kind_enum == PreferenceKind.allergy and value not in menu.known_allergens():
        raise ValueError(
            f"allergy must be one of {menu.known_allergens()}; "
            "save one row per allergen (e.g. 'nuts' = tree_nuts and peanuts)"
        )
    for existing in active_preferences(db, user_id):
        if existing.kind != kind_enum:
            continue
        if existing.value == value:
            return existing
        if kind_enum in SINGLE_VALUE_KINDS:
            return update_preference(db, user_id, existing.id, value, source_text=source_text)
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


def recently_removed(db: Session, user_id: int, limit: int = 10) -> list[Preference]:
    """Preferences the user removed (not replaced) and hasn't re-added since.

    The model needs this list: the chat history may still contain the user
    saying "I'm allergic to nuts", and without being told otherwise the model
    keeps acting on it after the user deleted it.
    """
    stmt = (
        select(Preference)
        .where(
            Preference.user_id == user_id,
            Preference.superseded_at.is_not(None),
            Preference.superseded_by_id.is_(None),
        )
        .order_by(Preference.superseded_at.desc())
        .limit(limit)
    )
    active = {(p.kind, p.value) for p in active_preferences(db, user_id)}
    seen, out = set(), []
    for p in db.scalars(stmt):
        key = (p.kind, p.value)
        if key not in active and key not in seen:
            seen.add(key)
            out.append(p)
    return out


def format_for_prompt(prefs: list[Preference]) -> str:
    """Render active preferences as lines the model can read and refer to by id."""
    if not prefs:
        return "(nothing remembered yet)"
    return "\n".join(f"- [id={p.id}] {p.kind.value}: {p.value}" for p in prefs)


def format_removed(prefs: list[Preference]) -> str:
    if not prefs:
        return "(none)"
    return "\n".join(f"- {p.kind.value}: {p.value}" for p in prefs)
