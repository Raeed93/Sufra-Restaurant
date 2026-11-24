from app import menu


def test_menu_meets_assignment_minimums():
    items = menu.all_items()
    assert len(items) >= 12
    assert {i["category"] for i in items} == set(menu.CATEGORIES)
    assert any(i["vegetarian"] for i in items) and any(not i["vegetarian"] for i in items)
    assert len({i["spice_level"] for i in items}) >= 3
    assert len({i["price"] for i in items}) > 5


def test_item_ids_are_unique_and_allergens_are_known():
    items = menu.all_items()
    assert len({i["id"] for i in items}) == len(items)
    known = set(menu.known_allergens())
    for item in items:
        assert set(item["allergens"]) <= known, item["id"]


def test_vegan_items_are_also_vegetarian():
    for item in menu.all_items():
        if item["vegan"]:
            assert item["vegetarian"], item["id"]


def test_allergen_filter_excludes_every_match():
    results = menu.search_menu(exclude_allergens=["gluten", "tree_nuts"])
    assert results
    for item in results:
        assert "gluten" not in item["allergens"]
        assert "tree_nuts" not in item["allergens"]


def test_combined_filters():
    results = menu.search_menu(category="dinner", vegetarian=True, max_price=45)
    assert [i["id"] for i in results] == ["d04"]


def test_spice_and_ingredient_filters():
    hot = menu.search_menu(min_spice=3)
    assert {i["id"] for i in hot} == {"l06", "d05"}
    no_chicken = menu.search_menu(exclude_ingredients=["chicken"])
    assert all("chicken" not in " ".join(i["ingredients"]) for i in no_chicken)


def test_results_sorted_by_price():
    prices = [i["price"] for i in menu.search_menu(category="lunch")]
    assert prices == sorted(prices)
