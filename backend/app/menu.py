"""Menu loading and search.

The menu is a JSON file loaded once into memory. With ~25 items there is no
need for a database table or vector search: filters over a list are instant,
and allergen/diet filters must be exact rules, which a similarity search
cannot guarantee.
"""
import json
from functools import lru_cache
from pathlib import Path

MENU_PATH = Path(__file__).parent / "data" / "menu.json"
CATEGORIES = ("breakfast", "lunch", "dinner", "snacks")


@lru_cache
def load_menu() -> dict:
    with MENU_PATH.open(encoding="utf-8") as f:
        menu = json.load(f)
    missing = set(CATEGORIES) - set(menu["categories"])
    if missing:
        raise ValueError(f"menu.json is missing categories: {missing}")
    return menu


@lru_cache
def all_items() -> tuple[dict, ...]:
    """Every item as a flat list, each tagged with its category."""
    items = []
    for category, entries in load_menu()["categories"].items():
        for item in entries:
            items.append({**item, "category": category})
    return tuple(items)


def get_item(item_id: str) -> dict | None:
    return next((i for i in all_items() if i["id"] == item_id), None)


def known_allergens() -> list[str]:
    return load_menu()["restaurant"]["allergen_list"]


def _contains_text(item: dict, word: str) -> bool:
    """True if the word appears in the name, description, ingredients or tags."""
    word = word.lower().strip()
    haystack = " ".join(
        [item["name"], item["description"], *item["ingredients"], *item["tags"]]
    ).lower()
    return word in haystack


def search_menu(
    category: str | None = None,
    query: str | None = None,
    max_price: float | None = None,
    vegetarian: bool | None = None,
    vegan: bool | None = None,
    max_spice: int | None = None,
    min_spice: int | None = None,
    exclude_allergens: list[str] | None = None,
    exclude_ingredients: list[str] | None = None,
) -> list[dict]:
    """Return menu items matching every filter that was given."""
    results = []
    for item in all_items():
        if category and item["category"] != category:
            continue
        if query and not _contains_text(item, query):
            continue
        if max_price is not None and item["price"] > max_price:
            continue
        if vegetarian and not item["vegetarian"]:
            continue
        if vegan and not item["vegan"]:
            continue
        if max_spice is not None and item["spice_level"] > max_spice:
            continue
        if min_spice is not None and item["spice_level"] < min_spice:
            continue
        if exclude_allergens and set(exclude_allergens) & set(item["allergens"]):
            continue
        if exclude_ingredients and any(_contains_text(item, w) for w in exclude_ingredients):
            continue
        results.append(item)
    return sorted(results, key=lambda i: i["price"])
