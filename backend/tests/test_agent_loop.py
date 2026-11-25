"""Tests the agent loop with a fake model, so no Groq key is needed."""
from types import SimpleNamespace as NS

from app import agent, config, memory


def _tool_call(call_id, name, args):
    return NS(id=call_id, function=NS(name=name, arguments=args))


def _response(content=None, tool_calls=None):
    return NS(choices=[NS(message=NS(content=content, tool_calls=tool_calls))])


def test_loop_runs_tools_then_replies(db, monkeypatch):
    """The Part 1 baseline loop, with the safety layer off."""
    monkeypatch.setattr(config, "SAFETY_MODE", "off")
    user, _ = memory.get_or_create_user(db, "raed")
    scripted = iter([
        _response(tool_calls=[
            _tool_call("c1", "save_preference", '{"kind": "allergy", "value": "peanuts"}'),
            _tool_call("c2", "search_menu", '{"category": "snacks", "exclude_allergens": ["peanuts"]}'),
        ]),
        _response(content="Try the Hummus with Bread (18 SAR)."),
    ])
    prompts = []

    def fake_call(messages):
        prompts.append(messages[0]["content"])
        return next(scripted)

    monkeypatch.setattr(agent, "_call_model", fake_call)

    history = []
    result = agent.run_turn(db, user, history, "I'm allergic to peanuts, any snacks?")

    assert result.reply.startswith("Try the Hummus")
    assert [c["name"] for c in result.tool_calls] == ["save_preference", "search_menu"]
    # The preference saved in step 1 shows up in the system prompt for step 2.
    assert "allergy: peanuts" not in prompts[0]
    assert "allergy: peanuts" in prompts[1]
    # user, assistant(tool_calls), tool, tool, assistant
    assert [m["role"] for m in history] == ["user", "assistant", "tool", "tool", "assistant"]
