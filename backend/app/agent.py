"""The agent loop.

One call to run_turn handles one user message:
  1. Build the system prompt with the user's CURRENT active preferences.
  2. Ask the model. If it requests tools, run them and feed back the results.
  3. Repeat until the model replies with plain text (or the step cap is hit).

Written by hand instead of with an agent framework: it's short, and every
step can be explained and tested.
"""
from dataclasses import dataclass, field

import groq
from sqlalchemy.orm import Session

from app import config, memory, menu
from app.models import User
from app.tools import TOOL_SCHEMAS, ToolContext, run_tool

SYSTEM_PROMPT = """You are the ordering assistant for {restaurant}, a Saudi restaurant.
You help {name} decide what to order. Prices are in SAR.

What you remember about this user (from earlier conversations):
{preferences}

Removed by the user (no longer applies, even if mentioned earlier in this chat):
{removed}

This memory is the source of truth. If something said earlier in the chat
contradicts it, follow the memory: the user may have edited it since.

Rules:
1. Only recommend items returned by search_menu. Never invent dishes, ingredients or prices.
2. Allergies are strict. Always pass every remembered allergy in exclude_allergens.
3. Respect diet, dislikes, spice level and budget when you search.
4. When the user mentions a lasting preference (allergy, diet, likes, dislikes, spice, budget),
   save it with save_preference without asking for permission.
5. When the user changes a preference you already remember, use update_preference or
   remove_preference with its id. Do not save a contradicting duplicate.
6. Learn preferences through natural conversation. Ask at most one question at a time.
   Do not interrogate the user with a questionnaire.
7. Recommend 1-3 items. For each, give the name, price and a short reason linked to
   what you know about the user.
8. Keep replies short and friendly. Do not mention tools, ids or the database.
9. If the user says "nuts", that means BOTH tree_nuts and peanuts. Save each allergen
   as a separate allergy preference.
10. Preferences belong only to the person you are talking to. If they mention someone
    else's preferences (a friend, family member), use them for this order only and
    never save them. Say clearly that you can't save preferences for other people."""


@dataclass
class TurnResult:
    reply: str
    tool_calls: list[dict] = field(default_factory=list)  # kept for evaluation in Part 2


class AgentError(RuntimeError):
    pass


_client: groq.Groq | None = None


def _get_client() -> groq.Groq:
    global _client
    if _client is None:
        _client = groq.Groq(api_key=config.groq_api_key())
    return _client


def build_system_prompt(db: Session, user: User) -> str:
    prefs = memory.active_preferences(db, user.id)
    return SYSTEM_PROMPT.format(
        restaurant=menu.load_menu()["restaurant"]["name"],
        name=user.display_name or user.username,
        preferences=memory.format_for_prompt(prefs),
        removed=memory.format_removed(memory.recently_removed(db, user.id)),
    )


def _call_model(messages: list[dict]):
    """Call Groq, retrying once if the model produced a malformed tool call."""
    kwargs = dict(
        model=config.groq_model(),
        messages=messages,
        tools=TOOL_SCHEMAS,
        tool_choice="auto",
        temperature=0.3,
    )
    if config.REASONING_EFFORT:
        kwargs["reasoning_effort"] = config.REASONING_EFFORT
    for attempt in range(2):
        try:
            return _get_client().chat.completions.create(**kwargs)
        except groq.BadRequestError as exc:
            if "tool_use_failed" in str(exc) and attempt == 0:
                continue
            raise AgentError(f"Model request failed: {exc}") from exc
        except groq.APIError as exc:
            raise AgentError(f"Groq error: {exc}") from exc
    raise AgentError("Model request failed twice")

def run_turn(db: Session, user: User, history: list[dict], user_message: str) -> TurnResult:
    """Process one user message. `history` is updated in place (no system message in it)."""
    history.append({"role": "user", "content": user_message})
    ctx = ToolContext(db=db, user_id=user.id, last_user_message=user_message)
    result = TurnResult(reply="")

    for _ in range(config.MAX_AGENT_STEPS):
        # Rebuilt every step so preferences saved mid-turn are visible straight away.
        messages = [{"role": "system", "content": build_system_prompt(db, user)}, *history]
        response = _call_model(messages)
        msg = response.choices[0].message

        if not msg.tool_calls:
            result.reply = msg.content or ""
            history.append({"role": "assistant", "content": result.reply})
            return result

        history.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {"id": c.id, "type": "function",
                 "function": {"name": c.function.name, "arguments": c.function.arguments}}
                for c in msg.tool_calls
            ],
        })
        for call in msg.tool_calls:
            output = run_tool(ctx, call.function.name, call.function.arguments)
            result.tool_calls.append(
                {"name": call.function.name, "arguments": call.function.arguments, "output": output}
            )
            history.append({"role": "tool", "tool_call_id": call.id, "content": output})

    raise AgentError(f"Agent did not finish within {config.MAX_AGENT_STEPS} steps")
