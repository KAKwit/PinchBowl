from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from pinchbowl.fonts import FONT_BY_ID
from pinchbowl.paths import settings_path

Listener = Callable[[], None]

# Nav column — room for the 64px mark + “Pinch Bowl” / “mise en place”.
# Detail stack breakpoints are independent of this range.
NAV_MIN_SP = 240
NAV_MAX_SP = 300

DEFAULTS = {
    "color_scheme": "system",
    # Cook mode uses an explicit light/dark preference (kitchen-friendly default).
    "cook_color_scheme": "dark",
    "font_size": 15,
    "recipe_font": "default",
    # Sidebar widths in sp (Adw splits are not drag-resizable).
    "nav_width": 260,
    # Matches Newsflash article-list sidebar range (360–600sp).
    "list_width": 480,
    "list_sort": "title",
    "list_sort_ascending": True,
    "grocery_sort": "created",
    "grocery_sort_ascending": True,
    "sidebar_tags_expanded": True,
    # One-way Mela drop-in: path to a watched .melarecipe(s) file + last fingerprint.
    "mela_watch_path": "",
    "mela_watch_mtime": 0.0,
    "mela_watch_size": 0,
    # First-run welcome carousel; not cleared by Restore defaults.
    "welcome_seen": False,
}

SORT_KEYS = frozenset({"created", "title", "time"})
GROCERY_SORT_KEYS = frozenset({"created", "text"})

WINDOW_DEFAULTS = {
    # Comfortable for nav + list + 400px ingredients beside directions.
    "window_width": 1480,
    "window_height": 800,
    "window_maximized": False,
}


class Settings:
    def __init__(self) -> None:
        self.color_scheme = DEFAULTS["color_scheme"]
        self.cook_color_scheme = DEFAULTS["cook_color_scheme"]
        self.font_size = DEFAULTS["font_size"]
        self.recipe_font = DEFAULTS["recipe_font"]
        self.nav_width = DEFAULTS["nav_width"]
        self.list_width = DEFAULTS["list_width"]
        self.list_sort = DEFAULTS["list_sort"]
        self.list_sort_ascending = DEFAULTS["list_sort_ascending"]
        self.grocery_sort = DEFAULTS["grocery_sort"]
        self.grocery_sort_ascending = DEFAULTS["grocery_sort_ascending"]
        self.sidebar_tags_expanded = DEFAULTS["sidebar_tags_expanded"]
        self.mela_watch_path = DEFAULTS["mela_watch_path"]
        self.mela_watch_mtime = DEFAULTS["mela_watch_mtime"]
        self.mela_watch_size = DEFAULTS["mela_watch_size"]
        self.welcome_seen = DEFAULTS["welcome_seen"]
        self.window_width = WINDOW_DEFAULTS["window_width"]
        self.window_height = WINDOW_DEFAULTS["window_height"]
        self.window_maximized = WINDOW_DEFAULTS["window_maximized"]
        self._listeners: list[Listener] = []
        self._path = settings_path()
        self.load()

    def subscribe(self, listener: Listener) -> None:
        self._listeners.append(listener)

    def load(self) -> None:
        path = self._path
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        scheme = str(data.get("color_scheme", self.color_scheme))
        if scheme in {"system", "light", "dark"}:
            self.color_scheme = scheme
        cook_scheme = str(data.get("cook_color_scheme", self.cook_color_scheme))
        if cook_scheme in {"light", "dark"}:
            self.cook_color_scheme = cook_scheme
        try:
            self.font_size = max(12, min(22, int(data.get("font_size", self.font_size))))
        except (TypeError, ValueError):
            pass
        font_id = str(data.get("recipe_font", self.recipe_font))
        if font_id in FONT_BY_ID:
            self.recipe_font = font_id
        try:
            self.nav_width = max(
                NAV_MIN_SP, min(NAV_MAX_SP, int(data.get("nav_width", self.nav_width)))
            )
        except (TypeError, ValueError):
            pass
        try:
            raw_list = int(data.get("list_width", self.list_width))
            # Old default was 400 (max 520); prefer Newsflash-like width.
            if raw_list == 400:
                raw_list = 480
            self.list_width = max(360, min(600, raw_list))
        except (TypeError, ValueError):
            pass
        sort_key = str(data.get("list_sort", self.list_sort))
        if sort_key in SORT_KEYS:
            self.list_sort = sort_key
        if "list_sort_ascending" in data:
            self.list_sort_ascending = bool(data.get("list_sort_ascending"))
        grocery_sort_key = str(data.get("grocery_sort", self.grocery_sort))
        if grocery_sort_key in GROCERY_SORT_KEYS:
            self.grocery_sort = grocery_sort_key
        if "grocery_sort_ascending" in data:
            self.grocery_sort_ascending = bool(data.get("grocery_sort_ascending"))
        if "sidebar_tags_expanded" in data:
            self.sidebar_tags_expanded = bool(data.get("sidebar_tags_expanded"))
        self.mela_watch_path = str(data.get("mela_watch_path", self.mela_watch_path) or "")
        try:
            self.mela_watch_mtime = float(data.get("mela_watch_mtime", self.mela_watch_mtime))
        except (TypeError, ValueError):
            pass
        try:
            self.mela_watch_size = max(0, int(data.get("mela_watch_size", self.mela_watch_size)))
        except (TypeError, ValueError):
            pass
        if "welcome_seen" in data:
            self.welcome_seen = bool(data.get("welcome_seen"))
        try:
            self.window_width = max(360, min(10000, int(data.get("window_width", self.window_width))))
        except (TypeError, ValueError):
            pass
        try:
            self.window_height = max(480, min(10000, int(data.get("window_height", self.window_height))))
        except (TypeError, ValueError):
            pass
        if "window_maximized" in data:
            self.window_maximized = bool(data.get("window_maximized"))

    def save(self) -> None:
        payload = {
            "color_scheme": self.color_scheme,
            "cook_color_scheme": self.cook_color_scheme,
            "font_size": self.font_size,
            "recipe_font": self.recipe_font,
            "nav_width": self.nav_width,
            "list_width": self.list_width,
            "list_sort": self.list_sort,
            "list_sort_ascending": self.list_sort_ascending,
            "grocery_sort": self.grocery_sort,
            "grocery_sort_ascending": self.grocery_sort_ascending,
            "sidebar_tags_expanded": self.sidebar_tags_expanded,
            "mela_watch_path": self.mela_watch_path,
            "mela_watch_mtime": self.mela_watch_mtime,
            "mela_watch_size": self.mela_watch_size,
            "welcome_seen": self.welcome_seen,
            "window_width": self.window_width,
            "window_height": self.window_height,
            "window_maximized": self.window_maximized,
        }
        path: Path = self._path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def update(self, notify: bool = True, **values: object) -> None:
        changed = False
        for key, value in values.items():
            if not hasattr(self, key):
                continue
            if getattr(self, key) != value:
                setattr(self, key, value)
                changed = True
        if not changed:
            return
        self.save()
        if notify:
            for listener in list(self._listeners):
                listener()

    def reset_to_defaults(self) -> None:
        values = {**DEFAULTS, **WINDOW_DEFAULTS}
        # Tags belong with the library; leave the sidebar section as-is.
        values["sidebar_tags_expanded"] = self.sidebar_tags_expanded
        # Keep the welcome carousel dismissed after Restore defaults.
        values["welcome_seen"] = self.welcome_seen
        self.update(**values)
