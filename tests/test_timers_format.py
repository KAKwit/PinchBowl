from __future__ import annotations

from pinchbowl.timers import format_remaining, label_for_seconds


def test_format_remaining() -> None:
    assert format_remaining(65) == "1:05"
    assert format_remaining(3661) == "1:01:01"
    assert format_remaining(0) == "0:00"


def test_label_for_seconds() -> None:
    assert label_for_seconds(45) == "45 sec"
    assert label_for_seconds(120) == "2 min"
    assert label_for_seconds(90) == "1 min 30 sec"
