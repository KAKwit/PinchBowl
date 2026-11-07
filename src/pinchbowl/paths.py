from __future__ import annotations

import os
from pathlib import Path

APPLICATION_ID = "dev.kakwit.PinchBowl"
APP_NAME = "Pinch Bowl"
APP_TAGLINE = "mise en place"


def version() -> str:
    return os.environ.get("PINCHBOWL_VERSION") or "1.0.23"


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    env = os.environ.get("PINCHBOWL_DATA_DIR")
    if env:
        return Path(env)
    return project_root() / "data"


def user_data_dir() -> Path:
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        path = Path(xdg) / "pinch-bowl"
    else:
        path = Path.home() / ".local" / "share" / "pinch-bowl"
    path.mkdir(parents=True, exist_ok=True)
    return path


def recipes_db_path() -> Path:
    return user_data_dir() / "recipes.db"


def images_dir() -> Path:
    path = user_data_dir() / "images"
    path.mkdir(parents=True, exist_ok=True)
    return path


def thumbs_dir() -> Path:
    """Disk cache of small list thumbnails (not full recipe photos)."""
    path = user_data_dir() / "thumbs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def user_config_dir() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        path = Path(xdg) / "pinch-bowl"
    else:
        path = Path.home() / ".config" / "pinch-bowl"
    path.mkdir(parents=True, exist_ok=True)
    return path


def settings_path() -> Path:
    return user_config_dir() / "settings.json"
