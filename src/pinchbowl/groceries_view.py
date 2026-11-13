from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, Gtk

from pinchbowl.models import GroceryItem
from pinchbowl.settings import Settings
from pinchbowl.store import Store


def _row_title(text: str) -> str:
    """Escape grocery text for Adw.ActionRow (Pango markup)."""
    return GLib.markup_escape_text(text)


class GroceriesView(Gtk.Box):
    """Single-page groceries checklist (replaces list + detail when selected)."""

    def __init__(self, store: Store, settings: Settings) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.store = store
        self.settings = settings
        self.on_clear_done: Callable[[], None] | None = None
        self.on_clear_all: Callable[[], None] | None = None
        self._empty_page: Adw.StatusPage | None = None
        self._compact = False
        self.add_css_class("pb-groceries-view")
        self.set_hexpand(True)
        self.set_vexpand(True)

        self.heading = Gtk.Label(label="Groceries", xalign=0)
        self.heading.add_css_class("heading")

        self.sort_btn = Gtk.MenuButton()
        self.sort_btn.add_css_class("flat")
        self.sort_btn.set_tooltip_text("Sort groceries")
        self._sync_sort_icon()
        sort_menu = Gio.Menu()
        by_section = Gio.Menu()
        by_section.append("Date added", "grocery.sort-by::created")
        by_section.append("Name", "grocery.sort-by::text")
        sort_menu.append_section("Sort by", by_section)
        dir_section = Gio.Menu()
        dir_section.append("Ascending", "grocery.sort-dir::asc")
        dir_section.append("Descending", "grocery.sort-dir::desc")
        sort_menu.append_section(None, dir_section)
        self.sort_btn.set_menu_model(sort_menu)

        # Icon-only clear actions keep the header under narrow window mins.
        self.clear_done_btn = Gtk.Button(icon_name="edit-clear-all-symbolic")
        self.clear_done_btn.add_css_class("flat")
        self.clear_done_btn.set_tooltip_text("Clear done")
        self.clear_done_btn.connect(
            "clicked", lambda *_: self.on_clear_done and self.on_clear_done()
        )

        self.clear_all_btn = Gtk.Button(icon_name="user-trash-symbolic")
        self.clear_all_btn.add_css_class("flat")
        self.clear_all_btn.set_tooltip_text("Clear all")
        self.clear_all_btn.connect(
            "clicked", lambda *_: self.on_clear_all and self.on_clear_all()
        )

        self.menu_btn = Gtk.MenuButton(icon_name="open-menu-symbolic")
        self.menu_btn.add_css_class("flat")

        self._header = Adw.HeaderBar()
        self._header.set_title_widget(self.heading)
        self._header.pack_end(self.menu_btn)
        self._header.pack_end(self.clear_all_btn)
        self._header.pack_end(self.clear_done_btn)
        self._header.pack_end(self.sort_btn)

        self._install_sort_actions()

        self.add_entry = Adw.EntryRow(title="Add item")
        self.add_entry.set_show_apply_button(True)
        self.add_entry.connect("apply", self._on_add_apply)

        add_list = Gtk.ListBox()
        add_list.set_selection_mode(Gtk.SelectionMode.NONE)
        add_list.add_css_class("boxed-list")
        add_list.append(self.add_entry)

        self._add_clamp = Adw.Clamp()
        self._add_clamp.set_maximum_size(560)
        self._add_clamp.set_tightening_threshold(400)
        self._add_clamp.set_child(add_list)
        self._add_clamp.set_margin_start(24)
        self._add_clamp.set_margin_end(24)
        self._add_clamp.set_margin_top(12)
        self._add_clamp.set_margin_bottom(8)

        self.search = Gtk.SearchEntry()
        self.search.set_placeholder_text("Search groceries")
        self.search.set_hexpand(True)
        self.search.connect("search-changed", self._on_search_changed)

        self._search_clamp = Adw.Clamp()
        self._search_clamp.set_maximum_size(560)
        self._search_clamp.set_tightening_threshold(400)
        self._search_clamp.set_child(self.search)
        self._search_clamp.set_margin_start(24)
        self._search_clamp.set_margin_end(24)
        self._search_clamp.set_margin_bottom(8)

        empty = Adw.StatusPage(
            icon_name="view-list-bullet-symbolic",
            title="No groceries yet",
            description="Type an item above, or add ingredients from a recipe.",
        )
        self._empty_page = empty

        self.list = Gtk.ListBox()
        self.list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.list.add_css_class("boxed-list")
        self.list.add_css_class("pb-groceries-list")

        self._list_clamp = Adw.Clamp()
        self._list_clamp.set_maximum_size(560)
        self._list_clamp.set_tightening_threshold(400)
        self._list_clamp.set_child(self.list)
        self._list_clamp.set_margin_start(24)
        self._list_clamp.set_margin_end(24)
        self._list_clamp.set_margin_top(4)
        self._list_clamp.set_margin_bottom(28)

        list_scroll = Gtk.ScrolledWindow()
        list_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        list_scroll.set_vexpand(True)
        list_scroll.set_child(self._list_clamp)

        self.stack = Gtk.Stack()
        self.stack.set_vexpand(True)
        self.stack.set_hexpand(True)
        self.stack.add_named(empty, "empty")
        self.stack.add_named(list_scroll, "list")

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        content.append(self._add_clamp)
        content.append(self._search_clamp)
        content.append(self.stack)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(self._header)
        toolbar.set_content(content)
        self.append(toolbar)

    def pack_nav_toggle(self, button: Gtk.Button) -> None:
        self._header.pack_start(button)

    def set_compact(self, compact: bool) -> None:
        if compact == self._compact:
            return
        self._compact = compact
        margin = 12 if compact else 24
        bottom = 16 if compact else 28
        for clamp in (self._add_clamp, self._search_clamp, self._list_clamp):
            clamp.set_margin_start(margin)
            clamp.set_margin_end(margin)
        self._list_clamp.set_margin_bottom(bottom)

    def set_menu_model(self, model: Gio.MenuModel) -> None:
        self.menu_btn.set_menu_model(model)

    def sync_sort_from_settings(self) -> None:
        """Refresh sort action states after preferences restore defaults."""
        sort_by = self._action_group.lookup_action("sort-by")
        if isinstance(sort_by, Gio.SimpleAction):
            sort_by.set_state(GLib.Variant.new_string(self.settings.grocery_sort))
        sort_dir = self._action_group.lookup_action("sort-dir")
        if isinstance(sort_dir, Gio.SimpleAction):
            sort_dir.set_state(
                GLib.Variant.new_string(
                    "asc" if self.settings.grocery_sort_ascending else "desc"
                )
            )
        self._sync_sort_icon()
        self.reload()

    def reload(self) -> None:
        while (row := self.list.get_row_at_index(0)) is not None:
            self.list.remove(row)

        items = self.store.list_grocery_items(
            sort=self.settings.grocery_sort,
            ascending=self.settings.grocery_sort_ascending,
        )
        query = (self.search.get_text() or "").strip().casefold()
        visible = 0
        has_done = False
        for item in items:
            if item.checked:
                has_done = True
            if query and query not in item.text.casefold():
                continue
            self.list.append(self._make_row(item))
            visible += 1

        self.clear_all_btn.set_sensitive(bool(items))
        self.clear_done_btn.set_sensitive(has_done)

        if not items:
            self._set_empty_page(
                "No groceries yet",
                "Type an item above, or add ingredients from a recipe.",
            )
            self.stack.set_visible_child_name("empty")
        elif visible == 0:
            self._set_empty_page(
                "No matching items",
                "Try a different search, or clear the search box.",
            )
            self.stack.set_visible_child_name("empty")
        else:
            self.stack.set_visible_child_name("list")

    def _set_empty_page(self, title: str, description: str) -> None:
        if self._empty_page is None:
            return
        self._empty_page.set_title(title)
        self._empty_page.set_description(description)

    def _install_sort_actions(self) -> None:
        group = Gio.SimpleActionGroup()
        sort_by = Gio.SimpleAction.new_stateful(
            "sort-by",
            GLib.VariantType.new("s"),
            GLib.Variant.new_string(self.settings.grocery_sort),
        )
        sort_by.connect("change-state", self._on_sort_by)
        group.add_action(sort_by)

        sort_dir = Gio.SimpleAction.new_stateful(
            "sort-dir",
            GLib.VariantType.new("s"),
            GLib.Variant.new_string(
                "asc" if self.settings.grocery_sort_ascending else "desc"
            ),
        )
        sort_dir.connect("change-state", self._on_sort_dir)
        group.add_action(sort_dir)

        self._action_group = group
        self.insert_action_group("grocery", group)

    def _sync_sort_icon(self) -> None:
        icon = (
            "view-sort-ascending-symbolic"
            if self.settings.grocery_sort_ascending
            else "view-sort-descending-symbolic"
        )
        self.sort_btn.set_icon_name(icon)

    def _on_sort_by(
        self, action: Gio.SimpleAction, value: GLib.Variant | None
    ) -> None:
        if value is None:
            return
        key = value.get_string()
        if key not in {"created", "text"}:
            return
        action.set_state(value)
        self.settings.update(grocery_sort=key, notify=False)
        self.reload()

    def _on_sort_dir(
        self, action: Gio.SimpleAction, value: GLib.Variant | None
    ) -> None:
        if value is None:
            return
        direction = value.get_string()
        if direction not in {"asc", "desc"}:
            return
        action.set_state(value)
        ascending = direction == "asc"
        self.settings.update(grocery_sort_ascending=ascending, notify=False)
        self._sync_sort_icon()
        self.reload()

    def _on_search_changed(self, *_args: object) -> None:
        self.reload()

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
        row.set_title(_row_title(item.text))
        row.set_title_lines(2)

        check = Gtk.CheckButton()
        check.set_active(item.checked)
        check.set_valign(Gtk.Align.CENTER)
        check.connect("toggled", self._on_toggled, item.id, row)
        row.add_prefix(check)
        row.set_activatable_widget(check)

        edit_btn = Gtk.Button()
        edit_btn.add_css_class("flat")
        edit_btn.set_valign(Gtk.Align.CENTER)
        edit_btn.set_tooltip_text("Edit item")
        edit_icon = Gtk.Image.new_from_icon_name("document-edit-symbolic")
        edit_btn.set_child(edit_icon)
        edit_btn.connect("clicked", self._on_edit_clicked, item)
        row.add_suffix(edit_btn)

        self._apply_done_style(row, item.checked)
        return row

    def _on_edit_clicked(self, _button: Gtk.Button, item: GroceryItem) -> None:
        dialog = Adw.AlertDialog(
            heading="Edit item",
            body="Change the text for this grocery item.",
        )
        entry = Gtk.Entry()
        entry.set_text(item.text)
        entry.set_hexpand(True)
        entry.set_margin_top(8)
        entry.set_activates_default(True)
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("save", "Save")
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("save")
        dialog.set_close_response("cancel")

        def on_response(_d: Adw.AlertDialog, response: str) -> None:
            if response != "save":
                return
            text = " ".join(entry.get_text().split())
            if not text or text == item.text:
                return
            if self.store.update_grocery_text(item.id, text):
                self.reload()

        entry.connect("activate", lambda *_: dialog.activate_default())
        dialog.connect("response", on_response)
        root = self.get_root()
        dialog.present(root if isinstance(root, Gtk.Window) else None)
        entry.grab_focus()
        entry.select_region(0, -1)

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
