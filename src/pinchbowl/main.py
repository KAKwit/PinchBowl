from __future__ import annotations

import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, Gtk

from pinchbowl.glib_log import install_benign_gdk_filters
from pinchbowl.paths import APPLICATION_ID, data_dir, recipes_db_path
from pinchbowl.settings import Settings
from pinchbowl.store import Store
from pinchbowl.timers import stop_timer_alarm
from pinchbowl.window import PinchBowlWindow


class Application(Adw.Application):
    def __init__(self) -> None:
        super().__init__(
            application_id=APPLICATION_ID,
            flags=Gio.ApplicationFlags.FLAGS_NONE,
        )
        self.store: Store | None = None
        self.window: PinchBowlWindow | None = None
        self.settings = Settings()
        self._base_css = Gtk.CssProvider()

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        self._load_base_css()
        display = Gdk.Display.get_default()
        icons = data_dir() / "icons"
        if display is not None and icons.is_dir():
            Gtk.IconTheme.get_for_display(display).add_search_path(str(icons))
        self.settings.subscribe(self._apply_appearance)
        Adw.StyleManager.get_default().connect(
            "notify::dark", lambda *_: self._apply_appearance()
        )
        self._apply_appearance()
        self.store = Store(recipes_db_path())
        self.set_accels_for_action("win.find", ["<Ctrl>f"])
        self.set_accels_for_action("win.preferences", ["<Ctrl>comma"])
        self.set_accels_for_action("win.add-url", ["<Ctrl>n"])
        self.set_accels_for_action("app.quit", ["<Ctrl>q"])
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_: self.quit())
        self.add_action(quit_action)
        prefs = Gio.SimpleAction.new("preferences", None)
        prefs.connect("activate", lambda *_: self._show_preferences())
        self.add_action(prefs)
        stop_alarm = Gio.SimpleAction.new("stop-timer-alarm", None)
        stop_alarm.connect("activate", lambda *_: stop_timer_alarm())
        self.add_action(stop_alarm)

    def do_activate(self) -> None:
        if self.window is None:
            assert self.store is not None
            self.window = PinchBowlWindow(self, self.store, self.settings)
        self.window.present()

    def do_shutdown(self) -> None:
        if self.window is not None and self.window.cook_mode_active():
            self.window.cook_view.clear_timers()
        stop_timer_alarm()
        if self.store is not None:
            self.store.close()
        Adw.Application.do_shutdown(self)

    def _show_preferences(self) -> None:
        if self.window is not None:
            self.window.show_preferences()

    def _load_base_css(self) -> None:
        css_path = data_dir() / "style.css"
        if not css_path.exists():
            return
        self._base_css.load_from_path(str(css_path))
        display = Gdk.Display.get_default()
        if display is None:
            return
        Gtk.StyleContext.add_provider_for_display(
            display,
            self._base_css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def apply_appearance(self) -> None:
        """Apply color scheme + recipe typography.

        While cook mode is active, use the dedicated cook light/dark preference
        instead of the library appearance setting.
        """
        if self.window is not None and self.window.cook_mode_active():
            key = self.settings.cook_color_scheme
            scheme = {
                "light": Adw.ColorScheme.FORCE_LIGHT,
                "dark": Adw.ColorScheme.FORCE_DARK,
            }.get(key, Adw.ColorScheme.FORCE_DARK)
        else:
            scheme = {
                "system": Adw.ColorScheme.DEFAULT,
                "light": Adw.ColorScheme.FORCE_LIGHT,
                "dark": Adw.ColorScheme.FORCE_DARK,
            }.get(self.settings.color_scheme, Adw.ColorScheme.DEFAULT)
        Adw.StyleManager.get_default().set_color_scheme(scheme)
        if self.window is not None:
            self.window.recipe_view.apply_appearance()
            self.window.cook_view.apply_appearance()
            if not self.window.cook_mode_active():
                self.window.apply_layout_from_settings()

    def _apply_appearance(self) -> None:
        self.apply_appearance()


def main(argv: list[str] | None = None) -> int:
    install_benign_gdk_filters()
    Adw.init()
    app = Application()
    return app.run(argv or sys.argv)
