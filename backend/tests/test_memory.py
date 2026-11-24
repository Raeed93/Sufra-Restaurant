import json

import pytest

from app import memory
from app.tools import ToolContext, run_tool


def test_user_lookup_is_case_insensitive(db):
    user, created = memory.get_or_create_user(db, "Raed")
    again, created_again = memory.get_or_create_user(db, "  raed ")
    assert created and not created_again
    assert user.id == again.id


def test_save_skips_duplicates(db):
    user, _ = memory.get_or_create_user(db, "a")
    memory.save_preference(db, user.id, "allergy", "Peanuts")
    memory.save_preference(db, user.id, "allergy", "peanuts")
    assert len(memory.active_preferences(db, user.id)) == 1


def test_update_keeps_history(db):
    user, _ = memory.get_or_create_user(db, "a")
    old = memory.save_preference(db, user.id, "spice", "3")
    new = memory.update_preference(db, user.id, old.id, "1")
    active = memory.active_preferences(db, user.id)
    assert [p.value for p in active] == ["1"]
    db.refresh(old)
    assert old.superseded_by_id == new.id and old.superseded_at is not None


def test_remove_preference(db):
    user, _ = memory.get_or_create_user(db, "a")
    pref = memory.save_preference(db, user.id, "diet", "vegetarian")
    memory.remove_preference(db, user.id, pref.id)
    assert memory.active_preferences(db, user.id) == []


def test_users_are_isolated(db):
    alice, _ = memory.get_or_create_user(db, "alice")
    bob, _ = memory.get_or_create_user(db, "bob")
    pref = memory.save_preference(db, alice.id, "allergy", "dairy")

    assert memory.active_preferences(db, bob.id) == []
    # Bob cannot change or delete Alice's preference, even with the right id.
    with pytest.raises(LookupError):
        memory.update_preference(db, bob.id, pref.id, "none")
    with pytest.raises(LookupError):
        memory.remove_preference(db, bob.id, pref.id)
    assert len(memory.active_preferences(db, alice.id)) == 1


def test_unknown_kind_is_rejected(db):
    user, _ = memory.get_or_create_user(db, "a")
    with pytest.raises(ValueError):
        memory.save_preference(db, user.id, "mood", "happy")


def test_tool_errors_go_back_to_the_model(db):
    user, _ = memory.get_or_create_user(db, "a")
    ctx = ToolContext(db=db, user_id=user.id, last_user_message="hi")
    out = json.loads(run_tool(ctx, "update_preference", '{"preference_id": 999, "new_value": "x"}'))
    assert "error" in out
    out = json.loads(run_tool(ctx, "search_menu", '{"category": "snacks", "vegan": true}'))
    assert out["count"] > 0 and all(i["vegan"] for i in out["items"])
