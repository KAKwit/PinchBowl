from __future__ import annotations

from pathlib import Path

import pytest

from pinchbowl.store import Store


@pytest.fixture
def tmp_xdg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point XDG data/config at a temp tree so Store/images never touch ~/.local."""
    data = tmp_path / "xdg-data"
    config = tmp_path / "xdg-config"
    data.mkdir()
    config.mkdir()
    monkeypatch.setenv("XDG_DATA_HOME", str(data))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    return tmp_path


@pytest.fixture
def store(tmp_xdg: Path) -> Store:
    """Empty Store on a temp DB."""
    from pinchbowl.paths import recipes_db_path

    return Store(recipes_db_path())
