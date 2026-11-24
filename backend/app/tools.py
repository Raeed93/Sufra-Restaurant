"""Tools the model can call, and the code that runs them.

The model never sees the full menu. It must call search_menu, so every
recommendation is backed by real rows from menu.json.
"""
import json
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app import memory, menu
from app.models import PreferenceKind

PREF_KINDS = [k.value for k in PreferenceKind]

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "search_menu",
            "description": (
                "Search the restaurant menu. Returns real items with price (SAR), "
                "ingredients, allergens and spice level. Always call this before "
                "recommending anything. Pass the user's allergies in exclude_allergens."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "enum": list(menu.CATEGORIES)},
                    "query": {
                        "type": "string",
                        "description": "One word or short phrase to match, e.g. 'chicken', 'rice', 'sweet'.",
                    },
                    "max_price": {"type": "number"},
                    "vegetarian": {"type": "boolean"},
                    "vegan": {"type": "boolean"},
                    "max_spice": {"type": "integer", "minimum": 0, "maximum": 3},
                    "min_spice": {"type": "integer", "minimum": 0, "maximum": 3},
                    "exclude_allergens": {
                        "type": "array",
                        "items": {"type": "string", "enum": menu.known_allergens()},
                    },
                    "exclude_ingredients": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Ingredients the user dislikes, e.g. ['mushroom'].",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_preference",
            "description": (
                "Remember a NEW lasting fact about the user's food preferences. "
                "Do not use this to change an existing preference; use update_preference."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": PREF_KINDS},
                    "value": {
                        "type": "string",
                        "description": (
                            "Short value. For allergy use one of: "
                            + ", ".join(menu.known_allergens())
                            + ". For spice use 0-3. For budget use a number in SAR."
                        ),
                    },
                },
                "required": ["kind", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_preference",
            "description": "Replace an existing preference (by its id) when the user changes their mind.",
            "parameters": {
                "type": "object",
                "properties": {
                    "preference_id": {"type": "integer"},
                    "new_value": {"type": "string"},
                    "new_kind": {"type": "string", "enum": PREF_KINDS},
                },
                "required": ["preference_id", "new_value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remove_preference",
            "description": "Forget an existing preference (by its id) that no longer applies.",
            "parameters": {
                "type": "object",
                "properties": {"preference_id": {"type": "integer"}},
                "required": ["preference_id"],
            },
        },
    },
]


@dataclass
class ToolContext:
    db: Session
    user_id: int
    last_user_message: str


def _item_summary(item: dict) -> dict:
    keys = ("id", "name", "category", "price", "vegetarian", "vegan",
            "spice_level", "ingredients", "allergens", "description")
    return {k: item[k] for k in keys}


def run_tool(ctx: ToolContext, name: str, raw_args: str) -> str:
    """Run one tool call and return a JSON string for the model.

    Errors are returned to the model as {"error": ...} so it can correct itself,
    rather than crashing the whole conversation.
    """
    try:
        args = json.loads(raw_args or "{}")
    except json.JSONDecodeError:
        return json.dumps({"error": "arguments were not valid JSON"})

    try:
        if name == "search_menu":
            items = menu.search_menu(**args)
            return json.dumps({"count": len(items), "items": [_item_summary(i) for i in items]})

        if name == "save_preference":
            pref = memory.save_preference(
                ctx.db, ctx.user_id, args["kind"], args["value"], ctx.last_user_message
            )
            return json.dumps({"saved": {"id": pref.id, "kind": pref.kind.value, "value": pref.value}})

        if name == "update_preference":
            pref = memory.update_preference(
                ctx.db, ctx.user_id, int(args["preference_id"]), args["new_value"],
                args.get("new_kind"), ctx.last_user_message,
            )
            return json.dumps({"updated": {"id": pref.id, "kind": pref.kind.value, "value": pref.value}})

        if name == "remove_preference":
            pref = memory.remove_preference(ctx.db, ctx.user_id, int(args["preference_id"]))
            return json.dumps({"removed": {"id": pref.id, "value": pref.value}})

        return json.dumps({"error": f"unknown tool {name}"})
    except (TypeError, KeyError, ValueError, LookupError) as exc:
        ctx.db.rollback()
        return json.dumps({"error": str(exc)})
