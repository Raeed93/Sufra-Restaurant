"""Safety layer (Part 2): checks done in code instead of trusted to the model.

Three checks, each fixing a failure found while testing the baseline
(see docs/findings.md):

1. allergy_safety_net   The user mentions an allergy but the model forgets to
                        save it (finding 1). Code spots allergy words and saves
                        the allergen codes itself.
2. check_item           The model recommends something unsafe (finding 2).
                        Every recommendation is checked against the user's
                        allergies, diet and budget before it is shown.
3. check_reply_text     The model writes dishes that aren't on the menu, wrong
                        prices (finding 4), or names an unsafe dish in its text
                        instead of using the recommend tool. The final reply is
                        scanned and rewritten if any of these appear.

These are deliberately simple rules. They are cheap, fast, testable and easy
to explain, and they don't depend on the model behaving well.
"""
import re

from app import menu
from app.models import Preference, PreferenceKind

# ---------- 1. Allergy safety net ----------

# Words that mean "this is an allergy or intolerance", not just a dislike.
_ALLERGY_CUES = re.compile(
    r"\b(allerg\w*|intoleran\w*|coeliac|celiac|can'?t (?:eat|have)|cannot (?:eat|have)|"
    r"anaphyla\w*)\b",
    re.I,
)

# Mentions of other people: the allergy probably isn't the user's (finding 5).
_THIRD_PARTY = re.compile(
    r"\b(my (?:friend|wife|husband|son|daughter|kid|child|mother|mom|mum|father|dad|"
    r"brother|sister|colleague|guest)\w*|for (?:him|her|them)|"
    r"(?:he|she|they)(?:'s| is| are| has| have)|\w+ is allergic)\b",
    re.I,
)

# The user is removing an allergy, not adding one. Leave that to the model.
_NEGATION = re.compile(r"\b(not|no longer|n't|anymore|any more)\b[^.]*\ballerg", re.I)

# Word -> allergen codes. "nuts" alone means both kinds (finding 1).
_ALLERGEN_WORDS: list[tuple[re.Pattern, list[str]]] = [
    (re.compile(r"\bpeanuts?\b", re.I), ["peanuts"]),
    (re.compile(r"\b(tree ?nuts?|almonds?|cashews?|pistachios?|walnuts?|hazelnuts?|pecans?)\b", re.I),
     ["tree_nuts"]),
    (re.compile(r"\bnuts?\b", re.I), ["tree_nuts", "peanuts"]),
    (re.compile(r"\b(gluten|wheat|coeliac|celiac)\b", re.I), ["gluten"]),
    (re.compile(r"\b(dairy|lactose|milk|cheese)\b", re.I), ["dairy"]),
    (re.compile(r"\beggs?\b", re.I), ["egg"]),
    (re.compile(r"\b(sesame|tahini)\b", re.I), ["sesame"]),
    (re.compile(r"\b(shellfish|shrimps?|prawns?|crabs?|lobsters?)\b", re.I), ["shellfish"]),
    (re.compile(r"\bfish\b", re.I), ["fish"]),
    (re.compile(r"\b(soy|soya)\b", re.I), ["soy"]),
]


def about_someone_else(message: str) -> bool:
    """True if the message talks about another person's needs (finding 5)."""
    return bool(_THIRD_PARTY.search(message))


def detect_allergies(message: str) -> list[str]:
    """Allergen codes the user says THEY are allergic to, or [] if unclear.

    Errs on the side of not saving when the message is about someone else or
    about removing an allergy. The model still handles those cases.
    """
    if not _ALLERGY_CUES.search(message):
        return []
    if _THIRD_PARTY.search(message) or _NEGATION.search(message):
        return []
    found: list[str] = []
    for pattern, codes in _ALLERGEN_WORDS:
        if pattern.search(message):
            for code in codes:
                if code not in found:
                    found.append(code)
    # "shellfish" also matches the plain "fish" rule; only keep fish if said on its own.
    if "shellfish" in found and "fish" in found and not re.search(r"(?<!shell)\bfish\b", message, re.I):
        found.remove("fish")
    return found


# ---------- 2. Recommendation check ----------

def check_item(item: dict, prefs: list[Preference]) -> list[str]:
    """Reasons this item must not be recommended to this user. Empty = safe."""
    reasons = []
    allergies = {p.value for p in prefs if p.kind == PreferenceKind.allergy}
    hit = allergies & set(item["allergens"])
    if hit:
        reasons.append(f"contains {', '.join(sorted(hit))}, which the user is allergic to")

    diets = {p.value for p in prefs if p.kind == PreferenceKind.diet}
    if "vegan" in diets and not item["vegan"]:
        reasons.append("not vegan, and the user is vegan")
    elif "vegetarian" in diets and not item["vegetarian"]:
        reasons.append("not vegetarian, and the user is vegetarian")

    for p in prefs:
        if p.kind == PreferenceKind.budget:
            try:
                budget = float(p.value)
            except ValueError:
                continue
            if item["price"] > budget:
                reasons.append(f"costs {item['price']} SAR, over the user's {p.value} SAR budget")
    return reasons


# ---------- 3. Invented dishes and prices in the reply text ----------

_BOLD = re.compile(r"\*\*(.+?)\*\*")
_PRICE = re.compile(r"(?:SAR\s*(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*SAR)", re.I)


def _normalise(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def _menu_by_name() -> dict[str, dict]:
    return {_normalise(i["name"]): i for i in menu.all_items()}


def dish_mentions(text: str) -> list[dict]:
    """Bold dish names in a reply, with any price written next to them.

    The model writes recommendations as '**Name** – 42 SAR' or '**Name – 42 SAR**'.
    Returns [{"name", "price" (or None), "item" (menu item or None)}].
    """
    by_name = _menu_by_name()
    out = []
    for m in _BOLD.finditer(text):
        span = m.group(1)
        after = text[m.end(): m.end() + 20]
        price_match = _PRICE.search(span) or _PRICE.match(after.lstrip(" –—-:(|"))
        name = _PRICE.sub("", span).strip(" –—-:(|,.")
        key = _normalise(name)
        if not key:
            continue
        item = by_name.get(key)
        price = None
        if price_match:
            price = float(price_match.group(1) or price_match.group(2))
        # Only treat a bold span as a dish if it's a menu name or has a price next to it.
        # Bold words like "**not**" or "**Allergies**" are ignored.
        if item or price is not None:
            out.append({"name": name, "price": price, "item": item})
    return out


def find_invented_dishes(text: str) -> list[str]:
    """Problems in the reply text: dishes not on the menu, or wrong prices."""
    problems = []
    for d in dish_mentions(text):
        if d["item"] is None:
            problems.append(f'"{d["name"]}" is not on the menu')
        elif d["price"] is not None and abs(d["price"] - d["item"]["price"]) > 0.01:
            problems.append(f'{d["item"]["name"]} costs {d["item"]["price"]} SAR, not {d["price"]:g}')
    return problems


def check_reply_text(text: str, prefs: list[Preference]) -> list[str]:
    """Everything wrong with a reply's text: invented dishes, wrong prices, unsafe dishes.

    Without the last check the model could skip the recommend tool and write an
    unsafe dish straight into the text.
    """
    problems = find_invented_dishes(text)
    seen = set()
    for d in dish_mentions(text):
        item = d["item"]
        if item is None or item["id"] in seen:
            continue
        seen.add(item["id"])
        reasons = check_item(item, prefs)
        if reasons:
            problems.append(f"{item['name']}: {'; '.join(reasons)}")
    return problems
