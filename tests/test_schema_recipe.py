from __future__ import annotations

from pathlib import Path

from pinchbowl.schema_recipe import (
    extract_recipe_from_html,
    is_schema_recipe,
    normalize_yield_text,
    recipe_from_schema,
    recipe_to_schema,
)
from pinchbowl.models import Recipe


FIXTURES = Path(__file__).parent / "fixtures"


def test_normalize_yield_prefers_longest() -> None:
    assert normalize_yield_text(["6", "6 people"]) == "6 people"


def test_is_schema_recipe() -> None:
    assert is_schema_recipe({"@type": "Recipe", "name": "X"})
    assert is_schema_recipe({"recipeIngredient": ["a"]})
    assert not is_schema_recipe({"title": "Mela style", "ingredients": "x"})


def test_extract_from_html_fixture() -> None:
    html = (FIXTURES / "recipe_ld.html").read_text(encoding="utf-8")
    mapped = extract_recipe_from_html(html, page_url="https://example.test/soup")
    assert mapped is not None
    assert mapped["title"] == "Test Soup"
    assert "stock" in mapped["ingredients"]
    assert "# Finish" in mapped["instructions"]
    assert mapped["yield_text"] == "4 servings"
    assert mapped["prep_time"] == "10min"
    assert "utm_source" not in mapped["source_url"]
    assert "Recipe video" not in mapped["description"]


def test_howto_section_and_roundtrip() -> None:
    data = {
        "@type": "Recipe",
        "name": "Bread",
        "recipeIngredient": ["flour"],
        "recipeInstructions": [
            {
                "@type": "HowToSection",
                "name": "Mix",
                "itemListElement": [{"@type": "HowToStep", "text": "Combine"}],
            }
        ],
        "totalTime": "PT1H",
    }
    mapped = recipe_from_schema(data)
    assert "# Mix" in mapped["instructions"]
    recipe = Recipe(
        id=1,
        uid="u1",
        title=mapped["title"],
        ingredients=mapped["ingredients"],
        instructions=mapped["instructions"],
        total_time=mapped["total_time"],
    )
    out = recipe_to_schema(recipe)
    assert out["@type"] == "Recipe"
    assert out["name"] == "Bread"
