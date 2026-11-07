from __future__ import annotations

import json
from pathlib import Path

import pytest

from pinchbowl.recipe_io import (
    export_filename,
    export_schema_bytes,
    import_recipe_file,
    map_recipe_file_data,
)
from pinchbowl.models import Recipe
from pinchbowl.store import Store


def test_export_filename() -> None:
    assert export_filename("Tomato / Soup!!", ".json") == "Tomato Soup.json"
    assert export_filename("", "melarecipe").endswith(".melarecipe")


def test_map_schema_and_mela() -> None:
    schema = {
        "@type": "Recipe",
        "name": "Schema Dish",
        "recipeIngredient": ["salt"],
        "recipeInstructions": ["1. Season"],
    }
    mapped = map_recipe_file_data(schema)
    assert mapped["title"] == "Schema Dish"
    assert "tags" not in mapped or mapped.get("tags") in (None, [])

    mela = {
        "title": "Mela Dish",
        "ingredients": "pepper",
        "instructions": "grind",
        "id": "abc",
    }
    mapped2 = map_recipe_file_data(mela)
    assert mapped2["uid"] == "abc"


def test_map_rejects_empty() -> None:
    with pytest.raises(ValueError):
        map_recipe_file_data({"@type": "Recipe", "name": ""})


def test_import_and_export_schema(store: Store, tmp_path: Path) -> None:
    path = tmp_path / "dish.json"
    path.write_text(
        json.dumps(
            {
                "@type": "Recipe",
                "name": "IO Dish",
                "recipeIngredient": ["rice"],
                "recipeInstructions": ["1. Cook"],
            }
        ),
        encoding="utf-8",
    )
    recipe = import_recipe_file(path, store)
    assert recipe.title == "IO Dish"
    raw = export_schema_bytes(recipe)
    data = json.loads(raw)
    assert data["@type"] == "Recipe"
    assert data["name"] == "IO Dish"
