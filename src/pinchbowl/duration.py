from __future__ import annotations

import re

_ISO = re.compile(
    r"^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?$",
    re.IGNORECASE,
)
_CLOCK = re.compile(r"^(\d+):([0-5]?\d)$")
_TOKEN = re.compile(
    r"(\d+(?:\.\d+)?)\s*(hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)\b",
    re.IGNORECASE,
)
_BARE_NUMBER = re.compile(r"^(\d+(?:\.\d+)?)$")


def parse_minutes(text: str) -> int | None:
    """Parse a freeform duration into whole minutes, or None if unknown."""
    raw = (text or "").strip()
    if not raw:
        return None

    iso = _ISO.fullmatch(raw.upper().replace(" ", ""))
    if iso and raw.upper().startswith("P"):
        days = int(iso.group("days") or 0)
        hours = int(iso.group("hours") or 0)
        minutes = int(iso.group("minutes") or 0)
        seconds = int(iso.group("seconds") or 0)
        total = days * 24 * 60 + hours * 60 + minutes + (1 if seconds else 0)
        return total if (days or hours or minutes or seconds) else None

    clock = _CLOCK.fullmatch(raw)
    if clock:
        return int(clock.group(1)) * 60 + int(clock.group(2))

    cleaned = raw.lower().replace(",", " ")
    total = 0.0
    matched = False
    for amount, unit in _TOKEN.findall(cleaned):
        matched = True
        value = float(amount)
        initial = unit[0]
        if initial == "h":
            total += value * 60
        elif initial == "m":
            total += value
        else:
            total += value / 60

    if matched:
        return max(0, int(round(total)))

    # "90mins" without a separator, or a bare number treated as minutes.
    compact = re.sub(r"\s+", "", cleaned)
    glued = re.fullmatch(
        r"(\d+(?:\.\d+)?)(hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)",
        compact,
    )
    if glued:
        return parse_minutes(f"{glued.group(1)} {glued.group(2)}")

    bare = _BARE_NUMBER.fullmatch(cleaned)
    if bare:
        return max(0, int(round(float(bare.group(1)))))

    return None


def format_duration(minutes: int) -> str:
    """Canonical display: ``45min``, ``1hr``, ``1hr 30min``."""
    minutes = max(0, int(minutes))
    hours, mins = divmod(minutes, 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours}hr")
    if mins or not hours:
        parts.append(f"{mins}min")
    return " ".join(parts)


def format_duration_text(text: str) -> str:
    """Format a stored duration string; leave unparseable text unchanged."""
    raw = (text or "").strip()
    if not raw:
        return ""
    minutes = parse_minutes(raw)
    if minutes is None:
        return raw
    return format_duration(minutes)


def to_iso8601_duration(text: str) -> str:
    """Convert a stored duration to Schema.org ISO-8601, or \"\" if empty/unknown."""
    raw = (text or "").strip()
    if not raw:
        return ""
    minutes = parse_minutes(raw)
    if minutes is None:
        return raw if raw.upper().startswith("P") else ""
    hours, mins = divmod(max(0, minutes), 60)
    if hours and mins:
        return f"PT{hours}H{mins}M"
    if hours:
        return f"PT{hours}H"
    return f"PT{mins}M"
