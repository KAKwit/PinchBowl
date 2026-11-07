from __future__ import annotations

from pathlib import Path

from pinchbowl.settings import DEFAULTS, Settings


def test_default_sort_is_title_ascending(tmp_xdg: Path) -> None:
    assert DEFAULTS["list_sort"] == "title"
    assert DEFAULTS["list_sort_ascending"] is True
    settings = Settings()
    assert settings.list_sort == "title"
    assert settings.list_sort_ascending is True


def test_reset_to_defaults_restores_title_ascending(tmp_xdg: Path) -> None:
    settings = Settings()
    settings.update(list_sort="created", list_sort_ascending=False, notify=False)
    settings.reset_to_defaults()
    assert settings.list_sort == "title"
    assert settings.list_sort_ascending is True


def test_welcome_seen_defaults_false(tmp_xdg: Path) -> None:
    settings = Settings()
    assert settings.welcome_seen is False


def test_welcome_seen_round_trip(tmp_xdg: Path) -> None:
    settings = Settings()
    settings.update(welcome_seen=True, notify=False)
    reloaded = Settings()
    assert reloaded.welcome_seen is True


def test_reset_to_defaults_preserves_welcome_seen(tmp_xdg: Path) -> None:
    settings = Settings()
    settings.update(welcome_seen=True, notify=False)
    settings.update(font_size=18, notify=False)
    settings.reset_to_defaults()
    assert settings.welcome_seen is True
    assert settings.font_size == 15
