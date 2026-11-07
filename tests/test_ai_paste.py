from __future__ import annotations

import json
from pathlib import Path

import pytest

from pinchbowl.ai_paste import (
    AI_SOURCE_LABEL,
    PasteParseError,
    build_ai_prompt,
    extract_json_candidates,
    parse_ai_paste,
)


FIXTURES = Path(__file__).parent / "fixtures"


def test_build_ai_prompt_includes_idea() -> None:
    prompt = build_ai_prompt("lemon cake")
    assert "lemon cake" in prompt
    assert "Schema.org" in prompt


def test_extract_fenced_and_embedded() -> None:
    body = 'Here you go:\n```json\n{"@type":"Recipe","name":"X"}\n```\n'
    cands = extract_json_candidates(body)
    assert any('"@type"' in c for c in cands)


def test_parse_ai_paste_fixture() -> None:
    raw = (FIXTURES / "ai_recipe.json").read_text(encoding="utf-8")
    result = parse_ai_paste(raw)
    assert result.format_name == "schema.org"
    assert result.recipe["title"] == "AI Pasta"
    assert result.recipe["source"] == AI_SOURCE_LABEL
    assert result.recipe["image_path"] == ""
    assert result.recipe["tags"] == []


def test_parse_prose_then_json() -> None:
    payload = {
        "@type": "Recipe",
        "name": "Stew",
        "recipeIngredient": ["beef"],
        "recipeInstructions": ["1. Brown"],
    }
    text = "Sure! Here's a stew.\n\n" + json.dumps(payload)
    result = parse_ai_paste(text)
    assert result.recipe["title"] == "Stew"


def test_parse_errors() -> None:
    with pytest.raises(PasteParseError):
        parse_ai_paste("")
    with pytest.raises(PasteParseError):
        parse_ai_paste("{not json")
    with pytest.raises(PasteParseError):
        parse_ai_paste('{"@type":"Recipe","name":""}')
