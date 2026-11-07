from __future__ import annotations

import html
import re

# Catch numeric / named entities even when the trailing semicolon was dropped.
_ENTITY_NO_SEMI = re.compile(
    r"&(#[0-9]+|#x[0-9a-fA-F]+|[a-zA-Z][a-zA-Z0-9]+);?"
)

# Sites often join HTML paragraphs into Schema.org description without a space.
_MISSING_SENTENCE_SPACE = re.compile(r"([a-z0-9])([.!?])([A-Z])")

# RecipeTin Eats (and similar) prefix the blurb with in-page video chrome.
_RECIPE_VIDEO_ABOVE = re.compile(
    r"^(?:Recipe\s+video\s+above\.?\s*)+",
    re.IGNORECASE,
)


def decode_entities(text: str) -> str:
    """Decode HTML entities like ``&amp;`` / ``&#38;`` / ``&#38`` in recipe text."""
    raw = text or ""
    if "&" not in raw:
        return raw

    def _one(match: re.Match[str]) -> str:
        token = match.group(0)
        if not token.endswith(";"):
            token = token + ";"
        return html.unescape(token)

    return _ENTITY_NO_SEMI.sub(_one, raw)


def clean_description(text: str) -> str:
    """Tidy imported recipe descriptions from noisy Schema.org / HTML prose."""
    raw = decode_entities(text or "").strip()
    if not raw:
        return ""
    raw = _RECIPE_VIDEO_ABOVE.sub("", raw).strip()
    return _MISSING_SENTENCE_SPACE.sub(r"\1\2 \3", raw)
