"""Parse recipe instruction text into headings and steps."""

from __future__ import annotations

import re
from typing import Literal

from pinchbowl.textutil import decode_entities

_STEP_NUM = re.compile(r"^(\d+)([.)]\s*)(.*)$")

InstructionKind = Literal["heading", "plain", "step"]
# ("heading", text) | ("plain", text) | ("step", marker, body)
InstructionItem = tuple


def split_step(line: str) -> tuple[str | None, str]:
    """Split a direction into (step marker, body). Marker is None if unnumbered."""
    match = _STEP_NUM.match(line)
    if not match:
        return None, line
    num, punct, rest = match.group(1), match.group(2), match.group(3)
    return num + punct.rstrip(), rest


def parse_instructions(text: str) -> tuple[list[InstructionItem], int]:
    """Parse instructions into items and the max step-number digit count."""
    parsed: list[InstructionItem] = []
    max_digits = 2  # always reserve room for double-digit steps
    for raw in decode_entities(text or "").splitlines():
        line = raw.rstrip()
        if not line:
            continue
        if line.startswith("# "):
            parsed.append(("heading", line[2:].strip()))
            continue
        if line.startswith("## ") or line.startswith("### "):
            parsed.append(("heading", line.lstrip("#").strip()))
            continue
        marker, body_text = split_step(line)
        if marker is None:
            parsed.append(("plain", body_text))
            continue
        digits = marker.rstrip(".)")
        max_digits = max(max_digits, len(digits))
        parsed.append(("step", marker, body_text))
    return parsed, max_digits
