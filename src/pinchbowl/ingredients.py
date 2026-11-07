from __future__ import annotations

import re

from pinchbowl.textutil import decode_entities

_UNITS = (
    r"g|kg|mg|ml|l|oz|lb|lbs|tsp|tbsp|cups?|tablespoons?|teaspoons?|"
    r"cloves?|sheets?|cans?|bunch(?:es)?|slices?|pinch(?:es)?|"
    r"handfuls?|sticks?|packets?|pkg|packages?"
)

# Leading quantity + unit(s), including dual metrics like "300g/10oz" or "60g / 2/3 cup".
_AMOUNT = re.compile(
    rf"""
    ^
    (
      (?:(?:about|approx\.?|approximately)\s+)?
      (?:
          \d+\s*/\s*\d+
        | \d+[./]\d+
        | \d+
        | [¼½¾⅓⅔⅛⅜⅝⅞]
      )
      (?:
          \s*(?:-|–|to)\s*
          (?:
              \d+\s*/\s*\d+
            | \d+[./]\d+
            | \d+
            | [¼½¾⅓⅔⅛⅜⅝⅞]
          )
      )?
      (?:\s*(?:{_UNITS}))?
      (?:
          \s*/\s*
          (?:
              \d+\s*/\s*\d+
            | \d+[./]\d+
            | \d+
            | [¼½¾⅓⅔⅛⅜⅝⅞]
          )?
          \s*
          (?:{_UNITS})?
      )*
      (?:
          \s+
          (?:large|medium|small)
      )?
    )
    \s+
    (.+)
    $
    """,
    re.IGNORECASE | re.VERBOSE,
)


def clean_ingredient_line(line: str) -> str:
    """Fix common Schema.org / WPRM oddities like '(, finely sliced)'."""
    text = " ".join(decode_entities(line or "").split())
    if not text:
        return ""
    text = re.sub(r"\(\s*,\s*", "(", text)
    text = re.sub(r"\(\s+", "(", text)
    text = re.sub(r"\s+\)", ")", text)
    text = re.sub(r"\s+,", ",", text)
    # WPRM double-wraps notes: ((or more black)) / ((Note 2))
    while True:
        nxt = re.sub(r"\(\(([^()]*)\)\)", r"(\1)", text)
        if nxt == text:
            break
        text = nxt
    # Nested notes that look like double closing brackets:
    # (finely grated (sub parmesan, Note 3)) → (finely grated, sub parmesan, Note 3)
    text = re.sub(
        r"\(([^()\n]+)\(([^()\n]+)\)\)",
        lambda m: f"({m.group(1).rstrip(', ')}, {m.group(2)})",
        text,
    )
    return text


def clean_ingredients(text: str) -> str:
    return "\n".join(clean_ingredient_line(line) for line in (text or "").splitlines())


def strip_parenthetical_notes(line: str) -> str:
    """Drop (...) notes from an ingredient line (for shopping lists)."""
    text = line or ""
    while True:
        nxt = re.sub(r"\s*\([^()]*\)", "", text)
        if nxt == text:
            break
        text = nxt
    text = re.sub(r"\s+,", ",", text)
    text = re.sub(r",\s*$", "", text)
    return " ".join(text.split())


def grocery_candidate_lines(
    ingredients: str, factor: float = 1.0, units: str = "original"
) -> list[str]:
    """Ingredient lines suitable for groceries (adjusted; headings/notes skipped)."""
    from pinchbowl.adjust import adjust_ingredients

    adjusted = adjust_ingredients(ingredients or "", factor, units)
    lines: list[str] = []
    for raw in adjusted.splitlines():
        cleaned = clean_ingredient_line(raw)
        if not cleaned or cleaned.startswith("#"):
            continue
        stripped = strip_parenthetical_notes(cleaned)
        if stripped:
            lines.append(stripped)
    return lines


def ingredient_line_markup(raw: str) -> str | None:
    """Markup for one ingredient line, or None if the line is empty."""
    from gi.repository import GLib

    line = clean_ingredient_line(raw)
    if not line:
        return None
    if line.startswith("# "):
        esc = GLib.markup_escape_text(line[2:].strip())
        return f'<span weight="bold" size="large">{esc}</span>'
    if line.startswith("## ") or line.startswith("### "):
        esc = GLib.markup_escape_text(line.lstrip("#").strip())
        return f'<span weight="bold">{esc}</span>'
    match = _AMOUNT.match(line)
    if match:
        amount = GLib.markup_escape_text(match.group(1).strip())
        rest = GLib.markup_escape_text(match.group(2).strip())
        return f'<span weight="bold">{amount}</span> {rest}'
    return GLib.markup_escape_text(line)
