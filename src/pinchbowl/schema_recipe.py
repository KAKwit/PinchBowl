from __future__ import annotations

import json
from typing import Any
from html.parser import HTMLParser

from pinchbowl.duration import format_duration_text, to_iso8601_duration
from pinchbowl.images import image_data_uri
from pinchbowl.ingredients import clean_ingredients
from pinchbowl.models import Recipe
from pinchbowl.textutil import clean_description, decode_entities
from pinchbowl.urls import clean_url


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return decode_entities(value.strip())
    if isinstance(value, list):
        parts = [_as_text(v) for v in value]
        return "\n".join(p for p in parts if p)
    if isinstance(value, dict):
        if "@type" in value and "text" in value:
            return _as_text(value.get("text"))
        if "name" in value:
            return _as_text(value.get("name"))
    return decode_entities(str(value).strip())


def normalize_yield_text(value: Any) -> str:
    """Pick a single yield label from Schema.org ``recipeYield``.

    Sites often publish both a bare number and a phrase (``["6", "6 people"]``).
    Prefer the most descriptive distinct part instead of joining with newlines.
    """
    if value is None:
        return ""
    if isinstance(value, list):
        parts = [_as_text(v) for v in value]
    else:
        parts = _as_text(value).splitlines()
    seen: list[str] = []
    for part in parts:
        text = part.strip()
        if text and text not in seen:
            seen.append(text)
    if not seen:
        return ""
    return max(seen, key=len)


def _duration(value: Any) -> str:
    """Normalize Schema.org durations to the app's display form."""
    return format_duration_text(_as_text(value))


def recipe_from_schema(data: dict[str, Any], page_url: str = "") -> dict[str, Any]:
    """Map a Schema.org Recipe object to Pinch Bowl upsert kwargs."""
    ingredients = data.get("recipeIngredient") or data.get("ingredients") or []
    instructions = data.get("recipeInstructions") or data.get("instructions") or ""
    if isinstance(instructions, list):
        lines: list[str] = []
        for i, step in enumerate(instructions, start=1):
            if isinstance(step, dict) and step.get("@type") == "HowToSection":
                name = _as_text(step.get("name"))
                if name:
                    lines.append(f"# {name}")
                for j, item in enumerate(step.get("itemListElement") or [], start=1):
                    lines.append(f"{j}. {_as_text(item)}")
            else:
                lines.append(f"{i}. {_as_text(step)}")
        instructions_text = "\n".join(lines)
    else:
        instructions_text = _as_text(instructions)

    if isinstance(ingredients, list):
        ingredients_text = "\n".join(_as_text(x) for x in ingredients if _as_text(x))
    else:
        ingredients_text = _as_text(ingredients)
    ingredients_text = clean_ingredients(ingredients_text)

    image = data.get("image")
    image_url = ""
    if isinstance(image, list) and image:
        image = image[0]
    if isinstance(image, dict):
        image_url = _as_text(image.get("url") or image.get("contentUrl"))
    elif isinstance(image, str):
        image_url = image

    url = clean_url(_as_text(data.get("url")) or page_url)
    return {
        "uid": None,
        "title": _as_text(data.get("name") or data.get("headline")) or "Untitled recipe",
        "description": clean_description(_as_text(data.get("description"))),
        "ingredients": ingredients_text,
        "instructions": instructions_text,
        "notes": "",
        "nutrition": _as_text(data.get("nutrition")),
        "yield_text": normalize_yield_text(data.get("recipeYield")),
        "prep_time": _duration(data.get("prepTime")),
        "cook_time": _duration(data.get("cookTime")),
        "total_time": _duration(data.get("totalTime")),
        "source": url.replace("https://", "").replace("http://", "") if url else "",
        "source_url": url,
        "image_path": "",
        # Tags are manual in Pinch Bowl — never infer from Schema.org keywords/cuisine.
        "_image_url": image_url,
        "_page_url": page_url or url,
    }


def recipe_to_schema(recipe: Recipe) -> dict[str, Any]:
    """Serialize a recipe to a Schema.org Recipe JSON object."""
    ingredients = [
        line.strip() for line in (recipe.ingredients or "").splitlines() if line.strip()
    ]
    instructions = (recipe.instructions or "").strip()
    out: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "Recipe",
        "name": recipe.title,
        "description": recipe.description,
        "recipeIngredient": ingredients,
        "recipeInstructions": instructions,
    }
    if recipe.yield_text:
        out["recipeYield"] = recipe.yield_text
    prep = to_iso8601_duration(recipe.prep_time)
    cook = to_iso8601_duration(recipe.cook_time)
    total = to_iso8601_duration(recipe.total_time)
    if prep:
        out["prepTime"] = prep
    if cook:
        out["cookTime"] = cook
    if total:
        out["totalTime"] = total
    if recipe.nutrition:
        out["nutrition"] = recipe.nutrition
    if recipe.source_url:
        out["url"] = recipe.source_url
    if recipe.image_path:
        uri = image_data_uri(recipe.image_path)
        if uri:
            out["image"] = uri
    return out


def is_schema_recipe(data: dict[str, Any]) -> bool:
    """True if *data* looks like a Schema.org Recipe object."""
    types = data.get("@type")
    type_list = types if isinstance(types, list) else [types]
    if any(str(t).lower() == "recipe" for t in type_list if t):
        return True
    return bool(data.get("recipeIngredient") or data.get("recipeInstructions"))


class _JsonLdCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._in_ld = False
        self._chunks: list[str] = []
        self.blocks: list[Any] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "script":
            return
        attr = {k.lower(): (v or "") for k, v in attrs}
        if attr.get("type", "").lower() == "application/ld+json":
            self._in_ld = True
            self._chunks = []

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self._in_ld:
            self._in_ld = False
            raw = "".join(self._chunks).strip()
            if not raw:
                return
            try:
                self.blocks.append(json.loads(raw))
            except json.JSONDecodeError:
                # Some sites concatenate objects; ignore bad blocks for now.
                pass

    def handle_data(self, data: str) -> None:
        if self._in_ld:
            self._chunks.append(data)


def _walk_for_recipe(node: Any) -> dict[str, Any] | None:
    if isinstance(node, dict):
        types = node.get("@type")
        type_list = types if isinstance(types, list) else [types]
        type_names = {str(t).lower() for t in type_list if t}
        if "recipe" in type_names:
            return node
        if "@graph" in node:
            found = _walk_for_recipe(node["@graph"])
            if found:
                return found
        for value in node.values():
            found = _walk_for_recipe(value)
            if found:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _walk_for_recipe(item)
            if found:
                return found
    return None


def extract_recipe_from_html(html: str, page_url: str = "") -> dict[str, Any] | None:
    """Find the first Schema.org Recipe in JSON-LD and map it."""
    parser = _JsonLdCollector()
    parser.feed(html)
    for block in parser.blocks:
        recipe = _walk_for_recipe(block)
        if recipe:
            return recipe_from_schema(recipe, page_url=page_url)
    return None
