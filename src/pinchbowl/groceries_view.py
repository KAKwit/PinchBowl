from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, Gtk

from pinchbowl.models import GroceryItem
from pinchbowl.store import Store


class GroceriesView(Gtk.Box):
    """Single-page groceries checklist (replaces list + detail when selected)."""

    def __init__(self, store: Store) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.store = store
        self.on_clear_done: Callable[[], None] | None = None
        self.on_clear_all: Callable[[], None] | None = None
        self.add_css_class("pb-groceries-view")
        self.set_hexpand(True)
        self.set_vexpand(True)

        chrome = Gtk.WindowHandle()
        chrome.add_css_class("window-chrome")
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        bar.add_css_class("pb-groceries-toolbar")

        self.heading = Gtk.Label(label="Groceries", xalign=0)
        self.heading.add_css_class("heading")
        self.heading.set_hexpand(True)
        bar.append(self.heading)

        self.clear_done_btn = Gtk.Button(label="Clear done")
        self.clear_done_btn.add_css_class("flat")
        self.clear_done_btn.set_tooltip_text("Remove checked items")
        self.clear_done_btn.connect(
            "clicked", lambda *_: self.on_clear_done and self.on_clear_done()
        )
        bar.append(self.clear_done_btn)

        self.clear_all_btn = Gtk.Button(label="Clear all")
        self.clear_all_btn.add_css_class("flat")
        self.clear_all_btn.set_tooltip_text("Remove every item")
        self.clear_all_btn.connect(
            "clicked", lambda *_: self.on_clear_all and self.on_clear_all()
        )
        bar.append(self.clear_all_btn)

        self.menu_btn = Gtk.MenuButton(icon_name="open-menu-symbolic")
        self.menu_btn.add_css_class("flat")
        bar.append(self.menu_btn)
        bar.append(Gtk.WindowControls(side=Gtk.PackType.END))
        chrome.set_child(bar)
        self.append(chrome)

        self.add_entry = Adw.EntryRow(title="Add item")
        self.add_entry.set_show_apply_button(True)
        self.add_entry.connect("apply", self._on_add_apply)

        add_list = Gtk.ListBox()
        add_list.set_selection_mode(Gtk.SelectionMode.NONE)
        add_list.add_css_class("boxed-list")
        add_list.append(self.add_entry)

        add_clamp = Adw.Clamp()
        add_clamp.set_maximum_size(560)
        add_clamp.set_tightening_threshold(400)
        add_clamp.set_child(add_list)
        add_clamp.set_margin_start(24)
        add_clamp.set_margin_end(24)
        add_clamp.set_margin_top(12)
        add_clamp.set_margin_bottom(8)
        self.append(add_clamp)

        empty = Adw.StatusPage(
            icon_name="view-list-bullet-symbolic",
            title="No groceries yet",
            description="Type an item above, or add ingredients from a recipe.",
        )

        self.list = Gtk.ListBox()
        self.list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.list.add_css_class("boxed-list")
        self.list.add_css_class("pb-groceries-list")

        list_clamp = Adw.Clamp()
        list_clamp.set_maximum_size(560)
        list_clamp.set_tightening_threshold(400)
        list_clamp.set_child(self.list)
        list_clamp.set_margin_start(24)
        list_clamp.set_margin_end(24)
        list_clamp.set_margin_top(4)
        list_clamp.set_margin_bottom(28)

        list_scroll = Gtk.ScrolledWindow()
        list_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        list_scroll.set_vexpand(True)
        list_scroll.set_child(list_clamp)

        self.stack = Gtk.Stack()
        self.stack.set_vexpand(True)
        self.stack.set_hexpand(True)
        self.stack.add_named(empty, "empty")
        self.stack.add_named(list_scroll, "list")
        self.append(self.stack)

    def set_menu_model(self, model: Gio.MenuModel) -> None:
        self.menu_btn.set_menu_model(model)

    def reload(self) -> None:
        while (row := self.list.get_row_at_index(0)) is not None:
            self.list.remove(row)

        items = self.store.list_grocery_items()
        has_done = False
        for item in items:
            if item.checked:
                has_done = True
            self.list.append(self._make_row(item))

        self.clear_all_btn.set_sensitive(bool(items))
        self.clear_done_btn.set_sensitive(has_done)
        self.stack.set_visible_child_name("list" if items else "empty")

    def _on_add_apply(self, *_args: object) -> None:
        text = " ".join((self.add_entry.get_text() or "").split())
        if not text:
            return
        self.store.add_grocery_items([text])
        self.add_entry.set_text("")
        self.reload()

    def _make_row(self, item: GroceryItem) -> Adw.ActionRow:
        row = Adw.ActionRow()
        row.add_css_class("pb-grocery-row")
        row.set_title(item.text)
        row.set_title_lines(2)

        check = Gtk.CheckButton()
        check.set_active(item.checked)
        check.set_valign(Gtk.Align.CENTER)
        check.connect("toggled", self._on_toggled, item.id, row)
        row.add_prefix(check)
        row.set_activatable_widget(check)
        self._apply_done_style(row, item.checked)
        return row

    def _apply_done_style(self, row: Adw.ActionRow, checked: bool) -> None:
        if checked:
            row.add_css_class("pb-grocery-done")
        else:
            row.remove_css_class("pb-grocery-done")

    def _on_toggled(
        self, check: Gtk.CheckButton, item_id: int, row: Adw.ActionRow
    ) -> None:
        checked = check.get_active()
        self.store.set_grocery_checked(item_id, checked)
        self._apply_done_style(row, checked)
        items = self.store.list_grocery_items()
        self.clear_done_btn.set_sensitive(any(i.checked for i in items))
        self.clear_all_btn.set_sensitive(bool(items))
