"""Ad-hoc recipe scaling and unit conversion (view-only, not persisted)."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from pinchbowl.ingredients import clean_ingredient_line

UnitSystem = str  # "original" | "metric" | "us"

SCALE_FACTORS: tuple[float, ...] = (0.5, 1.0, 1.5, 2.0, 3.0, 4.0)

_UNICODE_FRAC = {
    "¼": 0.25,
    "½": 0.5,
    "¾": 0.75,
    "⅓": 1 / 3,
    "⅔": 2 / 3,
    "⅛": 0.125,
    "⅜": 0.375,
    "⅝": 0.625,
    "⅞": 0.875,
}

_FRAC_DISPLAY = [
    (0.125, "⅛"),
    (0.25, "¼"),
    (1 / 3, "⅓"),
    (0.375, "⅜"),
    (0.5, "½"),
    (0.625, "⅝"),
    (2 / 3, "⅔"),
    (0.75, "¾"),
    (0.875, "⅞"),
]

# Mass base = grams; volume base = millilitres.
_UNIT_INFO: dict[str, tuple[str, float]] = {
    "mg": ("mass", 0.001),
    "g": ("mass", 1.0),
    "kg": ("mass", 1000.0),
    "oz": ("mass", 28.3495),
    "lb": ("mass", 453.592),
    "lbs": ("mass", 453.592),
    "ml": ("volume", 1.0),
    "l": ("volume", 1000.0),
    "tsp": ("volume", 5.0),
    "teaspoon": ("volume", 5.0),
    "teaspoons": ("volume", 5.0),
    "tbsp": ("volume", 15.0),
    "tablespoon": ("volume", 15.0),
    "tablespoons": ("volume", 15.0),
    "cup": ("volume", 240.0),
    "cups": ("volume", 240.0),
    "floz": ("volume", 29.5735),
}

_METRIC_UNITS = frozenset({"mg", "g", "kg", "ml", "l"})
_US_UNITS = frozenset(
    {
        "oz",
        "lb",
        "lbs",
        "tsp",
        "teaspoon",
        "teaspoons",
        "tbsp",
        "tablespoon",
        "tablespoons",
        "cup",
        "cups",
        "floz",
    }
)

_COUNT_UNITS = frozenset(
    {
        "clove",
        "cloves",
        "sheet",
        "sheets",
        "can",
        "cans",
        "bunch",
        "bunches",
        "slice",
        "slices",
        "pinch",
        "pinches",
        "handful",
        "handfuls",
        "stick",
        "sticks",
        "packet",
        "packets",
        "pkg",
        "package",
        "packages",
    }
)

_UNIT_ALT = {
    "ounce": "oz",
    "ounces": "oz",
    "pound": "lb",
    "pounds": "lb",
    "fl oz": "floz",
    "fl. oz": "floz",
    "fl.oz": "floz",
    "fluid ounce": "floz",
    "fluid ounces": "floz",
    "litre": "l",
    "litres": "l",
    "liter": "l",
    "liters": "l",
    "gram": "g",
    "grams": "g",
    "kilogram": "kg",
    "kilograms": "kg",
}

_UNIT_TOKEN = (
    r"fl\.?\s*oz|fluid\s+ounces?|ounces?|pounds?|"
    r"tablespoons?|teaspoons?|cups?|"
    r"kilograms?|grams?|litres?|liters?|"
    r"mg|kg|ml|tsp|tbsp|oz|lbs?|g|l|"
    r"cloves?|sheets?|cans?|bunches?|bunch|slices?|"
    r"pinches?|pinch|handfuls?|sticks?|packets?|pkg|packages?"
)

_UNICODE_CLASS = "¼½¾⅓⅔⅛⅜⅝⅞"

_QTY = (
    rf"(?:"
    rf"\d+\s+\d+\s*/\s*\d+"  # 1 1/2
    rf"|\d+[{_UNICODE_CLASS}]"  # 1½
    rf"|\d+\s*/\s*\d+"
    rf"|\d+[.,]\d+"
    rf"|\d+"
    rf"|[{_UNICODE_CLASS}]"
    rf")"
)

_PREFIX = r"(?:(?:about|approx\.?|approximately)\s+)?"
_SIZE = r"(?:\s+(?:large|medium|small))?"

_PART = re.compile(
    rf"(?P<qty>{_QTY})(?:\s*(?P<unit>{_UNIT_TOKEN}))?",
    re.IGNORECASE,
)

_LINE = re.compile(
    rf"""
    ^
    (?P<prefix>{_PREFIX})
    (?P<head>
        {_QTY}(?:\s*(?:{_UNIT_TOKEN}))?
        (?:
            \s*(?:-|–|to)\s*
            {_QTY}(?:\s*(?:{_UNIT_TOKEN}))?
        )?
        (?:
            \s*/\s*
            {_QTY}(?:\s*(?:{_UNIT_TOKEN}))?
        )*
    )
    (?P<size>{_SIZE})
    \s+
    (?P<rest>.+)
    $
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Slash after a unit letter separates dual amounts (300g/10oz), not fractions (1/2).
_DUAL_SPLIT = re.compile(r"(?<=[A-Za-z])\s*/\s*")

_SERVINGS = re.compile(r"(\d+(?:[.,]\d+)?)")
_RANGE_SPLIT = re.compile(r"\s*(?:-|–|to)\s*")


@dataclass(frozen=True)
class Quantity:
    value: float
    unit: str  # canonical lowercase, or "" for bare number


def parse_servings(yield_text: str) -> int | None:
    """Extract a whole serving count from yield text, if any."""
    match = _SERVINGS.search(yield_text or "")
    if not match:
        return None
    raw = match.group(1).replace(",", ".")
    try:
        value = float(raw)
    except ValueError:
        return None
    if value <= 0:
        return None
    return max(1, int(round(value)))


def scale_label(factor: float) -> str:
    """Human label for a scale factor (½, 1, 1½, …)."""
    for candidate, glyph in (
        (0.5, "½"),
        (1.0, "1"),
        (1.5, "1½"),
        (2.0, "2"),
        (3.0, "3"),
        (4.0, "4"),
    ):
        if abs(factor - candidate) < 1e-6:
            return f"{glyph}×"
    if abs(factor - round(factor)) < 1e-6:
        return f"{int(round(factor))}×"
    # Prefer a simple cooking fraction when the ratio is tidy (e.g. 5/6).
    for den in range(2, 9):
        num = round(factor * den)
        if num < 1:
            continue
        g = math.gcd(num, den)
        n, d = num // g, den // g
        if d > 6 and d != 8:
            continue
        if abs(factor - n / d) < 0.002:
            return f"{n}/{d}×"
    text = f"{factor:.2f}".rstrip("0").rstrip(".")
    return f"{text}×"


def nearest_scale_factor(factor: float) -> float:
    """Snap to the closest predefined scale factor."""
    return min(SCALE_FACTORS, key=lambda candidate: abs(candidate - factor))


def _parse_number(token: str) -> float | None:
    text = (token or "").strip()
    if not text:
        return None
    if text in _UNICODE_FRAC:
        return _UNICODE_FRAC[text]
    mixed_unicode = re.fullmatch(rf"(\d+)([{_UNICODE_CLASS}])", text)
    if mixed_unicode:
        return int(mixed_unicode.group(1)) + _UNICODE_FRAC[mixed_unicode.group(2)]
    mixed = re.fullmatch(r"(\d+)\s+(\d+)\s*/\s*(\d+)", text)
    if mixed:
        whole, num, den = int(mixed.group(1)), int(mixed.group(2)), int(mixed.group(3))
        if den == 0:
            return None
        return whole + num / den
    frac = re.fullmatch(r"(\d+)\s*/\s*(\d+)", text)
    if frac:
        num, den = int(frac.group(1)), int(frac.group(2))
        if den == 0:
            return None
        return num / den
    try:
        return float(text.replace(",", "."))
    except ValueError:
        return None


def _canonical_unit(raw: str | None) -> str:
    if not raw:
        return ""
    key = re.sub(r"\s+", " ", raw.strip().lower())
    key = _UNIT_ALT.get(key, key)
    if key == "cups":
        return "cup"
    if key in {"teaspoon", "teaspoons"}:
        return "tsp"
    if key in {"tablespoon", "tablespoons"}:
        return "tbsp"
    if key == "lbs":
        return "lb"
    return key


def _unit_kind(unit: str) -> str | None:
    if not unit or unit in _COUNT_UNITS:
        return "count"
    info = _UNIT_INFO.get(unit)
    return info[0] if info else None


def _parse_one_chunk(chunk: str) -> list[Quantity]:
    chunk = chunk.strip()
    if not chunk:
        return []
    range_bits = _RANGE_SPLIT.split(chunk, maxsplit=1)
    if len(range_bits) == 2:
        left = _parse_one_chunk(range_bits[0])
        right = _parse_one_chunk(range_bits[1])
        if left and right:
            # Propagate unit from either side.
            lq, rq = left[0], right[0]
            if not rq.unit and lq.unit:
                rq = Quantity(rq.value, lq.unit)
            if not lq.unit and rq.unit:
                lq = Quantity(lq.value, rq.unit)
            return [lq, rq]

    match = _PART.match(chunk)
    if not match:
        return []
    value = _parse_number(match.group("qty"))
    if value is None:
        return []
    return [Quantity(value, _canonical_unit(match.group("unit")))]


def _parse_quantities(head: str) -> list[Quantity]:
    """Parse one or more quantity+unit parts from the amount head."""
    parts: list[Quantity] = []
    for chunk in _DUAL_SPLIT.split(head.strip()):
        parts.extend(_parse_one_chunk(chunk))
    return parts


def _to_base(qty: Quantity) -> tuple[str, float] | None:
    kind = _unit_kind(qty.unit)
    if kind not in ("mass", "volume"):
        return None
    factor = _UNIT_INFO[qty.unit][1]
    return kind, qty.value * factor


def _from_base(kind: str, base: float, system: UnitSystem) -> Quantity:
    if kind == "mass":
        if system == "us":
            if base >= 453.592:
                return Quantity(base / 453.592, "lb")
            return Quantity(base / 28.3495, "oz")
        if base >= 1000:
            return Quantity(base / 1000.0, "kg")
        return Quantity(base, "g")
    if system == "us":
        if base >= 240:
            return Quantity(base / 240.0, "cup")
        if base >= 15:
            return Quantity(base / 15.0, "tbsp")
        if base >= 4.5:
            return Quantity(base / 5.0, "tsp")
        if base > 0:
            return Quantity(base / 5.0, "tsp")
        return Quantity(0.0, "tsp")
    if base >= 1000:
        return Quantity(base / 1000.0, "l")
    return Quantity(base, "ml")


def _normalize_metric(qty: Quantity) -> Quantity:
    converted = _to_base(qty)
    if converted is None:
        return qty
    kind, base = converted
    return _from_base(kind, base, "metric")


def _convert_quantity(qty: Quantity, system: UnitSystem) -> Quantity:
    if system == "original":
        return qty
    kind = _unit_kind(qty.unit)
    if kind == "count" or not qty.unit:
        return qty
    if kind not in ("mass", "volume"):
        return qty
    if system == "metric":
        if qty.unit in _METRIC_UNITS:
            return _normalize_metric(qty)
        converted = _to_base(qty)
        if converted is None:
            return qty
        return _from_base(converted[0], converted[1], "metric")
    # US
    if qty.unit in _US_UNITS:
        return qty
    converted = _to_base(qty)
    if converted is None:
        return qty
    return _from_base(converted[0], converted[1], "us")


def _pick_dual(quantities: list[Quantity], system: UnitSystem) -> list[Quantity]:
    """For dual metric/US amounts, keep the side matching the unit system."""
    if system == "original" or len(quantities) < 2:
        return quantities
    # Ranges use two quantities of the same kind — keep both.
    if (
        len(quantities) == 2
        and quantities[0].unit == quantities[1].unit
        and _unit_kind(quantities[0].unit) == "count"
    ):
        return quantities
    if len(quantities) == 2 and quantities[0].unit == quantities[1].unit:
        # Numeric range with same unit (e.g. 2–3 cups after parse) — keep both.
        # Dual amounts usually differ in unit.
        pass

    metricish: list[Quantity] = []
    usish: list[Quantity] = []
    other: list[Quantity] = []
    for q in quantities:
        kind = _unit_kind(q.unit)
        if kind not in ("mass", "volume"):
            other.append(q)
            continue
        if q.unit in _METRIC_UNITS:
            metricish.append(q)
        elif q.unit in _US_UNITS:
            usish.append(q)
        else:
            other.append(q)
    # Same-unit pair → range, not dual system.
    if len(quantities) == 2 and quantities[0].unit and quantities[0].unit == quantities[1].unit:
        return quantities
    if system == "metric" and metricish:
        return metricish + other
    if system == "us" and usish:
        return usish + other
    return quantities


def _format_number(value: float, *, prefer_fraction: bool) -> str:
    if value < 0:
        value = abs(value)
    whole = int(value)
    frac = value - whole
    if prefer_fraction and frac > 1e-6:
        best = None
        best_err = 0.04
        for target, glyph in _FRAC_DISPLAY:
            err = abs(frac - target)
            if err < best_err:
                best_err = err
                best = glyph
        if best is not None:
            if whole == 0:
                return best
            return f"{whole}{best}"
    if abs(value - round(value)) < 1e-6:
        return str(int(round(value)))
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text


def _display_unit(unit: str, value: float) -> str:
    if unit == "floz":
        return "fl oz"
    if unit == "cup":
        return "cup" if abs(value - 1.0) < 1e-6 else "cups"
    if unit in {"tsp", "tbsp", "lb", "oz", "g", "kg", "mg", "ml", "l"}:
        return unit
    if unit in _COUNT_UNITS:
        # crude plural
        if abs(value - 1.0) < 1e-6:
            singular = {
                "cloves": "clove",
                "sheets": "sheet",
                "cans": "can",
                "bunches": "bunch",
                "slices": "slice",
                "pinches": "pinch",
                "handfuls": "handful",
                "sticks": "stick",
                "packets": "packet",
                "packages": "package",
            }
            return singular.get(unit, unit.rstrip("s") if unit.endswith("s") else unit)
        plural = {
            "clove": "cloves",
            "sheet": "sheets",
            "can": "cans",
            "bunch": "bunches",
            "slice": "slices",
            "pinch": "pinches",
            "handful": "handfuls",
            "stick": "sticks",
            "packet": "packets",
            "package": "packages",
            "pkg": "pkg",
        }
        return plural.get(unit, unit)
    return unit


def _format_quantity(qty: Quantity, *, volume_fractions: bool) -> str:
    prefer = volume_fractions and _unit_kind(qty.unit) == "volume"
    value = qty.value
    if qty.unit == "g" and value >= 10:
        value = round(value)
    elif qty.unit == "ml" and value >= 10:
        value = round(value)
    elif qty.unit in {"oz", "lb"} and value >= 1:
        value = round(value * 10) / 10
    num = _format_number(value, prefer_fraction=prefer)
    if not qty.unit:
        return num
    unit = _display_unit(qty.unit, value)
    if unit in {"g", "kg", "mg", "ml", "l", "oz", "lb"}:
        return f"{num}{unit}"
    return f"{num} {unit}"


def _scale_quantity(qty: Quantity, factor: float) -> Quantity:
    return Quantity(qty.value * factor, qty.unit)


def _format_amount(
    quantities: list[Quantity],
    *,
    is_range: bool,
    units: UnitSystem,
) -> str:
    prefer_frac = units in ("us", "original")
    if is_range and len(quantities) >= 2:
        left_q, right_q = quantities[0], quantities[1]
        unit = left_q.unit or right_q.unit
        left_n = _format_number(
            left_q.value,
            prefer_fraction=prefer_frac and _unit_kind(unit) == "volume",
        )
        right_n = _format_number(
            right_q.value,
            prefer_fraction=prefer_frac and _unit_kind(unit) == "volume",
        )
        if not unit:
            return f"{left_n}–{right_n}"
        disp = _display_unit(unit, right_q.value)
        if disp in {"g", "kg", "mg", "ml", "l", "oz", "lb"}:
            return f"{left_n}–{right_n}{disp}"
        return f"{left_n}–{right_n} {disp}"
    if len(quantities) > 1 and units == "original":
        return "/".join(
            _format_quantity(q, volume_fractions=prefer_frac) for q in quantities
        )
    return _format_quantity(quantities[0], volume_fractions=prefer_frac)


def adjust_ingredient_line(
    line: str,
    factor: float,
    units: UnitSystem = "original",
) -> str:
    """Scale/convert one ingredient line; headings and non-amounts pass through."""
    cleaned = clean_ingredient_line(line)
    if not cleaned:
        return ""
    if cleaned.startswith("#"):
        return cleaned
    if abs(factor - 1.0) < 1e-9 and units == "original":
        return cleaned

    match = _LINE.match(cleaned)
    if not match:
        return cleaned

    head = match.group("head")
    prefix = match.group("prefix") or ""
    size = match.group("size") or ""
    rest = match.group("rest")
    is_range = bool(_RANGE_SPLIT.search(head)) and not _DUAL_SPLIT.search(head)

    quantities = _parse_quantities(head)
    if not quantities:
        return cleaned

    quantities = [_scale_quantity(q, factor) for q in quantities]
    if not is_range:
        quantities = _pick_dual(quantities, units)
    quantities = [_convert_quantity(q, units) for q in quantities]
    amount = _format_amount(quantities, is_range=is_range, units=units)
    return f"{prefix}{amount}{size} {rest}".strip()


def adjust_ingredients(
    text: str,
    factor: float = 1.0,
    units: UnitSystem = "original",
) -> str:
    """Adjust every ingredient line; preserve blank lines and headings."""
    if abs(factor - 1.0) < 1e-9 and units == "original":
        return text or ""
    return "\n".join(
        adjust_ingredient_line(raw, factor, units) for raw in (text or "").splitlines()
    )


def scale_yield_text(yield_text: str, factor: float) -> str:
    """Scale numeric servings in yield text (including simple ranges)."""
    raw = (yield_text or "").strip()
    if not raw or abs(factor - 1.0) < 1e-9:
        return raw
    if parse_servings(raw) is None:
        return raw

    def _replace(match: re.Match[str]) -> str:
        value = float(match.group(1).replace(",", "."))
        scaled = max(1, int(round(value * factor)))
        return str(scaled)

    return _SERVINGS.sub(_replace, raw)
