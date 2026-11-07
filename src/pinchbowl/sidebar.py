from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")

from gi.repository import GdkPixbuf, GLib, Gtk

from pinchbowl.paths import APP_NAME, APP_TAGLINE, data_dir

_MARK_PX = 64


def _sidebar_mark_image() -> Gtk.Image:
    mark = Gtk.Image()
    mark.add_css_class("pb-sidebar-mark")
    mark.set_pixel_size(_MARK_PX)
    mark.set_valign(Gtk.Align.CENTER)
    mark_path = data_dir() / "branding" / "pinch-bowl-mark.png"
    if mark_path.is_file():
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                str(mark_path),
                _MARK_PX,
                _MARK_PX,
                True,
            )
            mark.set_from_pixbuf(pixbuf)
            return mark
        except GLib.Error:
            pass
    mark.set_from_icon_name("dev.kakwit.PinchBowl")
    return mark


class _NavRow(Gtk.ListBoxRow):
    """Sidebar destination row with filter identity (avoids ad-hoc attributes)."""

    def __init__(self, filter_key: str, filter_tag: str | None = None) -> None:
        super().__init__()
        self.filter_key = filter_key
        self.filter_tag = filter_tag


class Sidebar(Gtk.Box):
    def __init__(self, *, tags_expanded: bool = True) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add_css_class("pb-sidebar")
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.on_filter: Callable[[str, str | None], None] | None = None
        self.on_tags_expanded_changed: Callable[[bool], None] | None = None
        self._tags_expanded = tags_expanded
        self._tag_rows: list[_NavRow] = []
        self._tags_header_row: _NavRow | None = None
        self._tags_chevron: Gtk.Image | None = None

        brand = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        brand.add_css_class("pb-sidebar-brand")
        brand.set_halign(Gtk.Align.START)
        brand.set_valign(Gtk.Align.CENTER)

        title = Gtk.Label(label=APP_NAME, xalign=0)
        title.add_css_class("pb-sidebar-title")
        tagline = Gtk.Label(label=APP_TAGLINE, xalign=0)
        tagline.add_css_class("pb-sidebar-tagline")

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        text.set_valign(Gtk.Align.CENTER)
        text.append(title)
        text.append(tagline)

        brand.append(_sidebar_mark_image())
        brand.append(text)

        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        title_box.add_css_class("pb-sidebar-header")
        title_box.append(brand)

        self.list = Gtk.ListBox()
        self.list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.list.add_css_class("navigation-sidebar")
        self.list.add_css_class("pb-nav-list")
        self.list.connect("row-selected", self._on_selected)
        self.list.connect("row-activated", self._on_activated)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.set_child(self.list)

        self.append(Gtk.WindowHandle(child=title_box))
        self.append(scroll)

        self._built_keys: list[tuple[str, str | None]] = []
        self.set_tags([])

    def set_tags(self, tags: list[tuple[str, int]]) -> None:
        selected = self.current_filter()
        while True:
            row = self.list.get_row_at_index(0)
            if row is None:
                break
            self.list.remove(row)
        self._built_keys.clear()
        self._tag_rows.clear()
        self._tags_header_row = None
        self._tags_chevron = None

        def add(label: str, icon: str, key: str, tag: str | None = None) -> _NavRow:
            row = _NavRow(key, tag)
            row.add_css_class("pb-nav-row")
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            box.set_margin_start(8)
            box.set_margin_end(8)
            box.set_margin_top(6)
            box.set_margin_bottom(6)
            image = Gtk.Image.new_from_icon_name(icon)
            image.add_css_class("accent")
            text = Gtk.Label(label=label, xalign=0)
            text.set_hexpand(True)
            box.append(image)
            box.append(text)
            row.set_child(box)
            self.list.append(row)
            self._built_keys.append((key, tag))
            return row

        add("All", "view-grid-symbolic", "all")
        add("Most Recent", "document-open-recent-symbolic", "recent")
        add("Favorites", "starred-symbolic", "favorites")
        add("Untagged", "folder-symbolic", "untagged")
        add("Groceries", "view-list-bullet-symbolic", "groceries")
        add("AI Generate", "emoji-objects-symbolic", "ai_generate")

        if tags:
            header = _NavRow("tags-header")
            header.add_css_class("pb-nav-tags-header")
            header.set_selectable(False)
            header.set_activatable(True)
            header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            header_box.set_margin_start(12)
            header_box.set_margin_end(10)
            header_box.set_margin_top(12)
            header_box.set_margin_bottom(4)
            label = Gtk.Label(label="Tags", xalign=0)
            label.add_css_class("pb-nav-section")
            label.set_hexpand(True)
            chevron = Gtk.Image()
            chevron.add_css_class("pb-nav-tags-chevron")
            chevron.set_pixel_size(12)
            header_box.append(label)
            header_box.append(chevron)
            header.set_child(header_box)
            self.list.append(header)
            self._tags_header_row = header
            self._tags_chevron = chevron

            for name, _count in tags:
                # Same bookmark icon as recipe list / detail tag chips.
                row = add(name, "user-bookmarks-symbolic", "tag", name)
                self._tag_rows.append(row)

            self._apply_tags_expanded()

        self.select_filter(*selected)

    def current_filter(self) -> tuple[str, str | None]:
        row = self.list.get_selected_row()
        if not isinstance(row, _NavRow):
            return "all", None
        return row.filter_key, row.filter_tag

    def select_filter(self, key: str, tag: str | None = None) -> None:
        i = 0
        while True:
            row = self.list.get_row_at_index(i)
            if row is None:
                break
            if (
                isinstance(row, _NavRow)
                and row.filter_key == key
                and row.filter_tag == tag
            ):
                self.list.select_row(row)
                return
            i += 1
        first = self.list.get_row_at_index(0)
        if first is not None:
            self.list.select_row(first)

    def set_tags_expanded(self, expanded: bool) -> None:
        self._tags_expanded = bool(expanded)
        self._apply_tags_expanded()

    def _apply_tags_expanded(self) -> None:
        for row in self._tag_rows:
            row.set_visible(self._tags_expanded)
        if self._tags_chevron is not None:
            icon = (
                "pan-down-symbolic" if self._tags_expanded else "pan-end-symbolic"
            )
            self._tags_chevron.set_from_icon_name(icon)

    def _toggle_tags(self) -> None:
        self.set_tags_expanded(not self._tags_expanded)
        if self.on_tags_expanded_changed is not None:
            self.on_tags_expanded_changed(self._tags_expanded)

    def _on_activated(self, _list: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        if row is self._tags_header_row:
            self._toggle_tags()

    def _on_selected(self, _list: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        if row is None or not row.get_selectable():
            return
        if not isinstance(row, _NavRow) or self.on_filter is None:
            return
        self.on_filter(row.filter_key, row.filter_tag)
