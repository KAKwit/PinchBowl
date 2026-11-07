from __future__ import annotations

from pinchbowl.duration import (
    format_duration,
    format_duration_text,
    parse_minutes,
    to_iso8601_duration,
)


def test_iso8601() -> None:
    assert parse_minutes("PT1H30M") == 90
    assert parse_minutes("PT45M") == 45
    assert parse_minutes("P1D") == 24 * 60


def test_clock_and_glued() -> None:
    assert parse_minutes("1:30") == 90
    assert parse_minutes("90mins") == 90
    assert parse_minutes("90") == 90


def test_format_roundtrip() -> None:
    assert format_duration(90) == "1hr 30min"
    assert format_duration(45) == "45min"
    assert format_duration_text("PT1H") == "1hr"
    assert format_duration_text("until fragrant") == "until fragrant"


def test_to_iso8601() -> None:
    assert to_iso8601_duration("1hr 30min") == "PT1H30M"
    assert to_iso8601_duration("45min") == "PT45M"
    assert to_iso8601_duration("") == ""
