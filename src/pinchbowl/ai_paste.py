"""Parse AI-pasted Schema.org Recipe or Mela JSON into upsert kwargs."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from pinchbowl.schema_recipe import (
    _walk_for_recipe,
    is_schema_recipe,
    recipe_from_schema,
)

AI_SOURCE_LABEL = "AI generated"

_FENCE_RE = re.compile(
    r"```(?:json|JSON)?\s*\n?(.*?)\n?```",
    re.DOTALL,
)


@dataclass(frozen=True)
class PasteParseResult:
    """Successful parse ready for ``Store.upsert_recipe``."""

    recipe: dict[str, Any]
    format_name: str  # "schema.org"


class PasteParseError(ValueError):
    """User-facing parse failure."""


def build_ai_prompt(idea: str = "") -> str:
    """Clipboard prompt: readable recipe plus Schema.org Recipe JSON."""
    idea = (idea or "").strip()
    if idea:
        lead = (
            f"Create one complete home-cooking recipe for this idea:\n{idea}\n\n"
        )
    else:
        lead = "Create one complete home-cooking recipe of your choice.\n\n"
    return (
        lead
        + "Write the recipe in a clear, friendly format I can read "
        "(title, short intro, ingredients, step-by-step directions, "
        "servings, and prep/cook times).\n\n"
        "After that, add a fenced JSON code block with the same recipe as a "
        "Schema.org Recipe object so I can import it. "
        "Keep the readable recipe first; put the JSON last."
    )


def _balanced_json_slice_any(text: str, start: int) -> str | None:
    """Balanced slice that tracks both ``{}`` and ``[]`` from an opening brace/bracket."""
    if start < 0 or start >= len(text) or text[start] not in "{[":
        return None
    depth_obj = 0
    depth_arr = 0
    started = text[start]
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == "{":
            depth_obj += 1
        elif ch == "}":
            depth_obj -= 1
        elif ch == "[":
            depth_arr += 1
        elif ch == "]":
            depth_arr -= 1
        if depth_obj < 0 or depth_arr < 0:
            return None
        if depth_obj == 0 and depth_arr == 0:
            if started == "{" and text[i] != "}":
                continue
            if started == "[" and text[i] != "]":
                continue
            return text[start : i + 1]
    return None


def extract_json_candidates(text: str) -> list[str]:
    """Pull likely JSON payloads from AI output (fences, whole text, embedded objects)."""
    raw = (text or "").strip()
    if not raw:
        return []
    candidates: list[str] = []
    seen: set[str] = set()

    def add(chunk: str) -> None:
        chunk = chunk.strip()
        if not chunk or chunk in seen:
            return
        seen.add(chunk)
        candidates.append(chunk)

    for match in _FENCE_RE.finditer(raw):
        add(match.group(1))

    add(raw)

    # Embedded object/array after prose (e.g. readable recipe then raw JSON).
    for i, ch in enumerate(raw):
        if ch not in "{[":
            continue
        # Prefer object starts that look like JSON keys nearby.
        snippet = raw[i : i + 40]
        if ch == "{" and not re.search(r'"\s*[@a-zA-Z]', snippet):
            continue
        sliced = _balanced_json_slice_any(raw, i)
        if sliced:
            add(sliced)

    return candidates


def _apply_ai_provenance(mapped: dict[str, Any]) -> dict[str, Any]:
    """Normalize upsert kwargs for an AI-imported recipe."""
    out = dict(mapped)
    out.pop("_page_url", None)
    out.pop("_image_url", None)
    out["image_path"] = ""
    out["source"] = AI_SOURCE_LABEL
    out["source_url"] = ""
    out["tags"] = []
    out["favorite"] = False
    out["want_to_cook"] = False
    out["uid"] = None
    return out


def _map_loaded_json(data: Any) -> PasteParseResult | None:
    recipe_node: dict[str, Any] | None = None

    if isinstance(data, (list, dict)):
        found = _walk_for_recipe(data)
        if found is not None:
            recipe_node = found
        elif isinstance(data, dict) and is_schema_recipe(data):
            recipe_node = data
    else:
        return None

    if recipe_node is None:
        return None

    mapped = recipe_from_schema(recipe_node)
    if not str(mapped.get("title") or "").strip():
        return None
    if not str(mapped.get("ingredients") or "").strip() and not str(
        mapped.get("instructions") or ""
    ).strip():
        return None

    return PasteParseResult(
        recipe=_apply_ai_provenance(mapped),
        format_name="schema.org",
    )


def parse_ai_paste(text: str) -> PasteParseResult:
    """Parse pasted AI output into recipe upsert kwargs.

    Accepts JSON alone or a readable reply that includes a JSON block.
    Raises ``PasteParseError`` with a short user-facing message on failure.
    """
    candidates = extract_json_candidates(text)
    if not candidates:
        raise PasteParseError(
            "Paste the AI’s reply (readable recipe plus Schema.org JSON). "
            "Copy the prompt above if you need the format."
        )

    last_json_error = False
    for chunk in candidates:
        try:
            data = json.loads(chunk)
        except json.JSONDecodeError:
            last_json_error = True
            continue
        result = _map_loaded_json(data)
        if result is not None:
            return result

    if last_json_error and len(candidates) == 1:
        raise PasteParseError(
            "Couldn’t find valid recipe JSON in that paste. Ask the AI to include a "
            "Schema.org Recipe JSON block at the end of its reply, then paste everything."
        )
    raise PasteParseError(
        "No recipe found in that paste. Ask the AI for a readable recipe plus a "
        "Schema.org Recipe JSON block (name, recipeIngredient, recipeInstructions)."
    )
