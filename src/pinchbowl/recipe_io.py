"""Single-recipe file import/export helpers."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pinchbowl.images import apply_mapped_image
from pinchbowl.mela import parse_melarecipe, recipe_to_melarecipe
from pinchbowl.models import Recipe
from pinchbowl.schema_recipe import (
    _walk_for_recipe,
    is_schema_recipe,
    recipe_from_schema,
    recipe_to_schema,
)
from pinchbowl.store import Store

_SAFE_FILENAME = re.compile(r"[^\w\s.-]+", re.UNICODE)


def export_filename(title: str, suffix: str) -> str:
    """Build a safe default filename from a recipe title."""
    stem = _SAFE_FILENAME.sub("", (title or "").strip())
    stem = re.sub(r"\s+", " ", stem).strip(" .") or "recipe"
    stem = stem[:80]
    if not suffix.startswith("."):
        suffix = f".{suffix}"
    return f"{stem}{suffix}"


def recipe_json_bytes(data: dict[str, Any]) -> bytes:
    return (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def export_melarecipe_bytes(recipe: Recipe) -> bytes:
    return recipe_json_bytes(recipe_to_melarecipe(recipe))


def export_schema_bytes(recipe: Recipe) -> bytes:
    return recipe_json_bytes(recipe_to_schema(recipe))


def _is_mela_recipe(data: dict[str, Any]) -> bool:
    if is_schema_recipe(data):
        return False
    if data.get("recipeIngredient") or data.get("recipeInstructions"):
        return False
    if data.get("@type"):
        return False
    # Mela files use string ingredients/instructions and often id/categories/images.
    ingredients = data.get("ingredients")
    instructions = data.get("instructions")
    if isinstance(ingredients, str) or isinstance(instructions, str):
        return True
    if data.get("id") or data.get("categories") is not None or data.get("images") is not None:
        return bool(data.get("title") or ingredients or instructions)
    return bool(data.get("title") and (ingredients is not None or instructions is not None))


def map_recipe_file_data(data: Any) -> dict[str, Any]:
    """Map loaded JSON to ``store.upsert_recipe`` kwargs.

    Raises ``ValueError`` with a short message when the payload is not a recipe.
    """
    if isinstance(data, (list, dict)):
        schema_node = _walk_for_recipe(data)
        if schema_node is None and isinstance(data, dict) and is_schema_recipe(data):
            schema_node = data
        if schema_node is not None:
            mapped = recipe_from_schema(schema_node)
            if not str(mapped.get("title") or "").strip():
                raise ValueError("Recipe JSON is missing a title")
            if not str(mapped.get("ingredients") or "").strip() and not str(
                mapped.get("instructions") or ""
            ).strip():
                raise ValueError("Recipe JSON has no ingredients or instructions")
            out = apply_mapped_image(mapped)
            out.pop("tags", None)  # Schema.org file import does not assign tags
            return out

    if not isinstance(data, dict):
        raise ValueError("Recipe file must be a JSON object")
    if not _is_mela_recipe(data):
        raise ValueError("Unrecognized recipe format")
    mapped = parse_melarecipe(data)
    if not str(mapped.get("title") or "").strip():
        raise ValueError("Recipe JSON is missing a title")
    return mapped


def import_recipe_file(path: Path, store: Store) -> Recipe:
    """Import one ``.melarecipe`` or Schema.org Recipe JSON file."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON: {exc.msg}") from exc
    mapped = map_recipe_file_data(data)
    return store.upsert_recipe(**mapped)
