from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk

SLIDES: tuple[tuple[str, str, str], ...] = (
    (
        "dev.kakwit.PinchBowl",
        "Welcome to Pinch Bowl",
        "A focused GNOME recipe app for browsing, cooking, and keeping "
        "your recipes local. Here’s a quick tour of the main features.",
    ),
    (
        "insert-link-symbolic",
        "Add recipes",
        "Add from a website URL, import a recipe file, or use the menu "
        "actions. Tracking parameters are stripped, and duplicates update "
        "the existing recipe.",
    ),
    (
        "emblem-documents-symbolic",
        "Mela",
        "Import .melarecipe or .melarecipes exports, and export back out. "
        "In Preferences you can watch a Mela export path to quietly import "
        "new recipes only.",
    ),
    (
        "emoji-food-symbolic",
        "Cook mode",
        "Open a recipe and start cooking for a fullscreen, distraction-free "
        "view with step focus. Cook mode remembers its own light or dark look.",
    ),
    (
        "alarm-symbolic",
        "Timers",
        "Start in-app kitchen timers while you cook. They last for this "
        "session only and clear when you leave cook mode.",
    ),
    (
        "accessories-calculator-symbolic",
        "Adjust",
        "Scale servings and switch between original, metric, and US units. "
        "Adjustments are for the current view and are not saved to the recipe.",
    ),
    (
        "view-list-bullet-symbolic",
        "Groceries",
        "Send ingredients from a recipe to a shared checklist, or type free "
        "items on the Groceries page in the sidebar. Tick them off as you shop.",
    ),
    (
        "emoji-objects-symbolic",
        "AI Generate",
        "Copy a prompt for an AI assistant, paste Schema.org Recipe JSON "
        "back in, preview, and add it to your library. Pinch Bowl never "
        "calls an AI itself.",
    ),
)


class WelcomeDialog(Adw.Dialog):
    """First-run / replayable intro carousel."""

    _CONTENT_WIDTH = 360
    _CONTENT_HEIGHT = 520

    def __init__(self, *, on_closed: Callable[[], None] | None = None) -> None:
        super().__init__()
        self.set_title("Welcome")
        # Stay within the adaptive window floor (360px) so AdwDialog does not
        # warn when the library window is already narrow.
        self.set_content_width(self._CONTENT_WIDTH)
        self.set_content_height(self._CONTENT_HEIGHT)
        self._on_closed = on_closed
        self._updating = False

        self.carousel = Adw.Carousel()
        self.carousel.set_hexpand(True)
        self.carousel.set_vexpand(True)
        # Wheel scrolling nudges fractional pages and fights vertical overflow.
        self.carousel.set_allow_scroll_wheel(False)
        self.carousel.set_allow_mouse_drag(True)
        self.carousel.set_allow_long_swipes(True)
        self.carousel.add_css_class("pb-welcome-carousel")

        for index, (icon_name, title, description) in enumerate(SLIDES):
            hero = index == 0
            self.carousel.append(
                self._make_page(icon_name, title, description, hero=hero)
            )

        dots = Adw.CarouselIndicatorDots()
        dots.set_carousel(self.carousel)
        dots.add_css_class("pb-welcome-dots")

        self.skip_btn = Gtk.Button(label="Skip")
        self.skip_btn.add_css_class("flat")
        self.skip_btn.connect("clicked", lambda *_: self.close())

        self.next_btn = Gtk.Button(label="Next")
        self.next_btn.add_css_class("suggested-action")
        self.next_btn.connect("clicked", self._on_next)

        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        footer.add_css_class("pb-welcome-footer")
        footer.set_halign(Gtk.Align.FILL)
        footer.append(self.skip_btn)
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        footer.append(spacer)
        footer.append(self.next_btn)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        body.set_hexpand(True)
        body.set_vexpand(True)
        body.append(self.carousel)
        body.append(dots)
        body.append(footer)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        toolbar.set_content(body)
        self.set_child(toolbar)

        self.carousel.connect("notify::position", self._on_position)
        self.connect("closed", self._emit_closed)
        self._sync_buttons()

    def _make_page(
        self,
        icon_name: str,
        title: str,
        description: str,
        *,
        hero: bool = False,
    ) -> Gtk.Widget:
        # Custom page (not Adw.StatusPage): StatusPage embeds a ScrolledWindow
        # that clips copy and steals scroll-wheel events from the carousel.
        icon = Gtk.Image.new_from_icon_name(icon_name)
        icon.set_pixel_size(192 if hero else 96)
        icon.set_halign(Gtk.Align.CENTER)
        icon.add_css_class("pb-welcome-icon")
        if hero:
            icon.add_css_class("pb-welcome-icon-hero")

        title_label = Gtk.Label(label=title)
        title_label.add_css_class("title-1")
        title_label.set_wrap(True)
        title_label.set_justify(Gtk.Justification.CENTER)
        title_label.set_halign(Gtk.Align.CENTER)
        title_label.set_max_width_chars(28)

        body_label = Gtk.Label(label=description)
        body_label.add_css_class("body")
        body_label.add_css_class("dim-label")
        body_label.set_wrap(True)
        body_label.set_justify(Gtk.Justification.CENTER)
        body_label.set_halign(Gtk.Align.CENTER)
        body_label.set_max_width_chars(32)
        body_label.set_selectable(False)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.add_css_class("pb-welcome-page")
        content.set_halign(Gtk.Align.CENTER)
        content.set_valign(Gtk.Align.CENTER)
        content.set_hexpand(True)
        content.set_vexpand(True)
        content.append(icon)
        content.append(title_label)
        content.append(body_label)

        wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        wrap.set_hexpand(True)
        wrap.set_vexpand(True)
        wrap.set_size_request(self._CONTENT_WIDTH, -1)
        wrap.append(content)
        return wrap

    def _page_count(self) -> int:
        return self.carousel.get_n_pages()

    def _current_index(self) -> int:
        return int(round(self.carousel.get_position()))

    def _on_position(self, *_args: object) -> None:
        if self._updating:
            return
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        last = self._current_index() >= self._page_count() - 1
        self.next_btn.set_label("Done" if last else "Next")
        self.skip_btn.set_visible(not last)

    def _on_next(self, *_args: object) -> None:
        index = self._current_index()
        if index >= self._page_count() - 1:
            self.close()
            return
        page = self.carousel.get_nth_page(index + 1)
        self._updating = True
        try:
            self.carousel.scroll_to(page, True)
        finally:
            self._updating = False
        self._sync_buttons()

    def _emit_closed(self, *_args: object) -> None:
        if self._on_closed is not None:
            self._on_closed()
