"""Part 2 safety layer: each test maps to a finding in docs/findings.md."""
import json
from types import SimpleNamespace as NS

import pytest

from app import agent, conversations, memory, menu, safety
from app.models import Conversation
from app.tools import ToolContext, run_tool


# ---------- 1. Allergy safety net (finding 1) ----------

@pytest.mark.parametrize("message, expected", [
    ("I'm allergic to nuts", ["peanuts", "tree_nuts"]),
    ("I have a peanut allergy", ["peanuts"]),
    ("lunch please, oh and I'm coeliac", ["gluten"]),
    ("I'm lactose intolerant", ["dairy"]),
    ("I can't eat shellfish", ["shellfish"]),
    ("I'm allergic to fish and sesame", ["fish", "sesame"]),
    ("Severe almond allergy here", ["tree_nuts"]),
])
def test_detects_the_users_allergies(message, expected):
    assert sorted(safety.detect_allergies(message)) == sorted(expected)


@pytest.mark.parametrize("message", [
    "I love nuts",                                        # no allergy cue
    "my friend Sara is allergic to soy",                  # someone else (finding 5)
    "she's allergic to gluten, what can she eat?",
    "I'm not allergic to nuts anymore",                   # a removal, left to the model
    "What dishes have peanuts?",
])
def test_ignores_non_allergies_and_other_people(message):
    assert safety.detect_allergies(message) == []


# ---------- 2. Item checks (finding 2) ----------

def _prefs(db, *pairs):
    user, _ = memory.get_or_create_user(db, "u")
    for kind, value in pairs:
        memory.save_preference(db, user.id, kind, value)
    return user, memory.active_preferences(db, user.id)


def test_check_item_blocks_allergens_diet_and_budget(db):
    _, prefs = _prefs(db, ("allergy", "peanuts"), ("diet", "vegetarian"), ("budget", "30"))
    assert safety.check_item(menu.get_item("s04"), prefs) == []            # luqaimat: fine
    peanut_bites = safety.check_item(menu.get_item("s07"), prefs)
    assert any("peanuts" in r for r in peanut_bites)
    assert any("vegetarian" in r for r in peanut_bites)
    assert any("budget" in r for r in safety.check_item(menu.get_item("d04"), prefs))   # 42 SAR


def test_recommend_tool_rejects_unsafe_and_unknown_items(db):
    user, _ = _prefs(db, ("allergy", "peanuts"))
    ctx = ToolContext(db=db, user_id=user.id, last_user_message="snack?")
    out = json.loads(run_tool(ctx, "recommend", json.dumps({"items": [
        {"id": "s07", "reason": "spicy and crunchy"},
        {"id": "x99", "reason": "made up"},
        {"id": "s01", "reason": "safe dip"},
    ]})))
    assert [a["id"] for a in out["accepted"]] == ["s01"]
    assert {r["id"] for r in out["rejected"]} == {"s07", "x99"}
    # Cards use menu.json for name and price, never the model's text.
    assert ctx.recommendations[0]["name"] == "Hummus with Bread"
    assert ctx.recommendations[0]["price"] == 18


# ---------- 3. Reply check (finding 4) ----------

def test_finds_invented_dishes_and_wrong_prices():
    text = (
        "1. **Spicy Chicken Shawarma Bites – 22 SAR** tasty\n"
        "2. **Luqaimat** – 15 SAR\n"
        "3. **Hummus with Bread** – SAR 25\n"
        "Note: this is **not** spicy."
    )
    problems = safety.find_invented_dishes(text)
    assert len(problems) == 2
    assert "Spicy Chicken Shawarma Bites" in problems[0]
    assert "18" in problems[1]


def _reply(text):
    return NS(choices=[NS(message=NS(content=text, tool_calls=None))])


def test_invented_reply_is_rewritten(db, monkeypatch):
    user, _ = memory.get_or_create_user(db, "u")
    queue = [_reply("Try the **Lamb Kofta Skewers – 24 SAR**!"), _reply("Nothing spicy in snacks, sorry.")]
    monkeypatch.setattr(agent, "_call_model", lambda m, allow_tools=True: queue.pop(0))
    result = agent.run_turn(db, user, [], "spicy snack?")
    assert result.reply == "Nothing spicy in snacks, sorry."
    assert any("not on the menu" in e for e in result.safety_events)


def test_falls_back_when_rewrite_still_invents(db, monkeypatch):
    user, _ = memory.get_or_create_user(db, "u")
    bad = _reply("Try the **Lamb Kofta Skewers – 24 SAR**!")
    queue = [bad, bad]
    monkeypatch.setattr(agent, "_call_model", lambda m, allow_tools=True: queue.pop(0))
    history = []
    result = agent.run_turn(db, user, history, "spicy snack?")
    assert result.reply == agent.FALLBACK_NO_CARDS
    assert "Kofta Skewers" not in history[-1]["content"]


def test_safety_off_runs_the_baseline(db, monkeypatch):
    monkeypatch.setattr(agent.config, "SAFETY_MODE", "off")
    user, _ = memory.get_or_create_user(db, "u")
    monkeypatch.setattr(agent, "_call_model", lambda m, allow_tools=True: _reply("**Fake Dish – 5 SAR**"))
    result = agent.run_turn(db, user, [], "I'm allergic to nuts")
    assert result.reply == "**Fake Dish – 5 SAR**"       # nothing checked
    assert memory.active_preferences(db, user.id) == []    # nothing auto-saved


# ---------- Cards survive reopening a conversation ----------

def test_cards_rebuilt_from_history(db):
    user, _ = memory.get_or_create_user(db, "u")
    conv = Conversation(user_id=user.id, title="t")
    db.add(conv)
    db.commit()
    conversations.append(db, conv, [
        {"role": "user", "content": "dinner?"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {
            "name": "recommend", "arguments": json.dumps({"items": [{"id": "d04", "reason": "mild and vegan"}]})}}]},
        {"role": "tool", "tool_call_id": "c1", "content": json.dumps({"accepted": [{"id": "d04"}], "rejected": []})},
        {"role": "assistant", "content": "Here you go."},
    ])
    db.refresh(conv)
    msgs = conversations.visible_messages(conv)
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[1]["recommendations"][0]["name"] == "Vegetable Maqluba"
    assert msgs[1]["recommendations"][0]["reason"] == "mild and vegan"


def test_unsafe_dish_written_in_text_is_caught(db, monkeypatch):
    """The model can't bypass the card check by writing an unsafe dish in its text."""
    user, _ = _prefs(db, ("allergy", "peanuts"))
    queue = [_reply("Try the **Peanut Chili Chicken Bites** – 26 SAR!"), _reply("How about hummus?")]
    monkeypatch.setattr(agent, "_call_model", lambda m, allow_tools=True: queue.pop(0))
    result = agent.run_turn(db, user, [], "snack?")
    assert result.reply == "How about hummus?"
    assert any("Peanut Chili Chicken Bites: contains peanuts" in e for e in result.safety_events)


def test_model_cannot_save_someone_elses_allergy(db):
    user, _ = memory.get_or_create_user(db, "u")
    ctx = ToolContext(db=db, user_id=user.id,
                      last_user_message="My friend Sara is allergic to soy, remember that for her")
    out = json.loads(run_tool(ctx, "save_preference", '{"kind": "allergy", "value": "soy"}'))
    assert "someone else" in out["error"]
    assert memory.active_preferences(db, user.id) == []


@pytest.mark.parametrize("message, expected", [
    ("I'm vegan, any dinner ideas?", "vegan"),
    ("I am vegetarian.", "vegetarian"),
    ("I don't eat meat", "vegetarian"),
    ("I'm not vegetarian anymore", None),
    ("my wife is vegan", None),
    ("Is the maqluba vegan?", None),
])
def test_detects_the_users_diet(message, expected):
    assert safety.detect_diet(message) == expected


def test_null_tool_arguments_are_accepted(db):
    """Finding 7: the model sends null for unused filters."""
    from app.tools import TOOL_SCHEMAS
    props = TOOL_SCHEMAS[0]["function"]["parameters"]["properties"]
    assert props["max_price"]["type"] == ["number", "null"]
    assert None in props["category"]["enum"]
    user, _ = memory.get_or_create_user(db, "u")
    ctx = ToolContext(db=db, user_id=user.id, last_user_message="x")
    out = json.loads(run_tool(ctx, "search_menu", json.dumps(
        {"category": "dinner", "vegan": True, "max_price": None, "query": None, "max_spice": None})))
    assert out["count"] == 1


def test_bracketed_notes_are_not_invented_dishes():
    assert safety.find_invented_dishes("**Falafel Wrap (vegetarian)** – 24 SAR") == []


# ---------- Fallback model ----------

class _FakeCompletions:
    def __init__(self, fail_models):
        self.fail_models = fail_models
        self.models_called = []

    def create(self, **kwargs):
        import groq
        import httpx
        self.models_called.append(kwargs["model"])
        if kwargs["model"] in self.fail_models:
            response = httpx.Response(429, request=httpx.Request("POST", "https://api.groq.com"))
            raise groq.RateLimitError("tokens per day", response=response, body=None)
        return NS(model=kwargs["model"], choices=[NS(message=NS(content="Hello!", tool_calls=None))])


def _fake_client(monkeypatch, fail_models):
    completions = _FakeCompletions(fail_models)
    monkeypatch.setattr(agent, "_get_client", lambda: NS(chat=NS(completions=completions)))
    return completions


def test_rate_limited_main_model_falls_back_and_says_so(db, monkeypatch):
    monkeypatch.setattr(agent.config, "GROQ_FALLBACK_MODEL", "backup-model")
    fake = _fake_client(monkeypatch, fail_models={"fake-model"})
    user, _ = memory.get_or_create_user(db, "u")
    result = agent.run_turn(db, user, [], "hi")
    assert fake.models_called == ["fake-model", "backup-model"]
    assert result.reply == "Hello!"
    assert agent.FALLBACK_EVENT in result.safety_events


def test_no_fallback_configured_fails_loudly(db, monkeypatch):
    monkeypatch.setattr(agent.config, "GROQ_FALLBACK_MODEL", None)
    _fake_client(monkeypatch, fail_models={"fake-model"})
    user, _ = memory.get_or_create_user(db, "u")
    with pytest.raises(agent.AgentError):
        agent.run_turn(db, user, [], "hi")


def test_unparseable_output_is_retried_once(db, monkeypatch):
    """Eval run 3: Groq returned output_parse_failed; one retry should recover."""
    import groq
    import httpx
    calls = []

    class Flaky:
        def create(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                response = httpx.Response(400, request=httpx.Request("POST", "https://api.groq.com"))
                raise groq.BadRequestError("output_parse_failed", response=response, body=None)
            return NS(model=kwargs["model"], choices=[NS(message=NS(content="Hi", tool_calls=None))])

    monkeypatch.setattr(agent, "_get_client", lambda: NS(chat=NS(completions=Flaky())))
    user, _ = memory.get_or_create_user(db, "u")
    assert agent.run_turn(db, user, [], "hello").reply == "Hi"
    assert len(calls) == 2


def test_reasoning_effort_only_sent_to_gpt_oss(monkeypatch):
    sent = []
    monkeypatch.setattr(agent.config, "REASONING_EFFORT", "low")
    monkeypatch.setattr(agent, "_get_client", lambda: NS(chat=NS(completions=NS(
        create=lambda **kw: sent.append(kw) or NS(model=kw["model"], choices=[])))))
    monkeypatch.setenv("GROQ_MODEL", "qwen/qwen3.8-27b")
    agent._call_model([])
    monkeypatch.setenv("GROQ_MODEL", "openai/gpt-oss-120b")
    agent._call_model([])
    assert "reasoning_effort" not in sent[0]
    assert sent[1]["reasoning_effort"] == "low"


@pytest.mark.parametrize("message, expected", [
    ("I don't like chicken. Dinner ideas?", ["chicken"]),      # eval run 3
    ("I hate eggs", ["egg"]),
    ("I really don't eat lamb", ["lamb"]),
    ("I don't like very spicy food", []),                       # not an ingredient
    ("I don't eat meat", []),                                   # a diet, handled separately
    ("my son doesn't like chicken", []),
])
def test_detects_disliked_ingredients(message, expected):
    assert safety.detect_dislikes(message) == expected
