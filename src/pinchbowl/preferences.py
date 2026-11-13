from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, Gtk

from pinchbowl.fonts import KIND_LABEL, available_fonts
from pinchbowl.settings import Settings


class PreferencesDialog(Adw.PreferencesDialog):
    def __init__(
        self,
        settings: Settings,
        *,
        on_import_mela: Callable[[], None] | None = None,
        on_export_mela: Callable[[], None] | None = None,
        on_clear_library: Callable[[], None] | None = None,
        on_defaults_restored: Callable[[], None] | None = None,
        on_mela_watch_changed: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(title="Preferences")
        self.settings = settings
        self._on_import_mela = on_import_mela
        self._on_export_mela = on_export_mela
        self._on_clear_library = on_clear_library
        self._on_defaults_restored = on_defaults_restored
        self._on_mela_watch_changed = on_mela_watch_changed
        self._applying = True

        page = Adw.PreferencesPage(title="General")
        appearance = Adw.PreferencesGroup(title="Appearance")

        self.scheme_row = Adw.ComboRow(title="Color scheme")
        self.scheme_row.set_model(Gtk.StringList.new(["System", "Light", "Dark"]))
        scheme_map = {"system": 0, "light": 1, "dark": 2}
        self.scheme_row.set_selected(scheme_map.get(settings.color_scheme, 0))
        self.scheme_row.connect("notify::selected", self._on_scheme)
        appearance.add(self.scheme_row)

        self.font_choices = available_fonts()
        self.font_row = Adw.ComboRow(
            title="Recipe font",
            subtitle="Typeface for the recipe detail view",
        )
        self.font_row.set_model(
            Gtk.StringList.new([font.name for font in self.font_choices])
        )
        font_ids = [font.id for font in self.font_choices]
        font_index = (
            font_ids.index(settings.recipe_font)
            if settings.recipe_font in font_ids
            else 0
        )
        self.font_row.set_selected(font_index)
        self.font_row.set_subtitle(self._font_subtitle(self.font_choices[font_index]))
        self.font_row.connect("notify::selected", self._on_font)
        appearance.add(self.font_row)

        self.size_row = Adw.SpinRow(
            title="Font size",
            subtitle="Affects the recipe detail view",
            adjustment=Gtk.Adjustment(
                value=settings.font_size,
                lower=12,
                upper=22,
                step_increment=1,
                page_increment=2,
            ),
        )
        self.size_row.connect("notify::value", self._on_font_size)
        appearance.add(self.size_row)
        page.add(appearance)

        library = Adw.PreferencesGroup(
            title="Library",
            description="Import or export recipes, or remove everything from this device.",
        )
        mela_row = Adw.ActionRow(
            title="Import from Mela",
            subtitle="Duplicates update the existing recipe",
        )
        mela_btn = Gtk.Button(label="Import")
        mela_btn.set_valign(Gtk.Align.CENTER)
        mela_btn.add_css_class("suggested-action")
        mela_btn.connect("clicked", self._import_mela_clicked)
        mela_row.add_suffix(mela_btn)
        mela_row.set_activatable_widget(mela_btn)
        library.add(mela_row)

        self.watch_row = Adw.ActionRow(title="Watch Mela export")
        watch_choose = Gtk.Button(label="Choose…")
        watch_choose.set_valign(Gtk.Align.CENTER)
        watch_choose.connect("clicked", self._choose_watch_clicked)
        self.watch_clear = Gtk.Button(label="Clear")
        self.watch_clear.set_valign(Gtk.Align.CENTER)
        self.watch_clear.connect("clicked", self._clear_watch_clicked)
        self.watch_row.add_suffix(self.watch_clear)
        self.watch_row.add_suffix(watch_choose)
        self.watch_row.set_activatable_widget(watch_choose)
        self._sync_watch_row()
        library.add(self.watch_row)

        export_row = Adw.ActionRow(
            title="Export to Mela",
            subtitle="Save the whole library as a .melarecipes archive",
        )
        export_btn = Gtk.Button(label="Export")
        export_btn.set_valign(Gtk.Align.CENTER)
        export_btn.connect("clicked", self._export_mela_clicked)
        export_row.add_suffix(export_btn)
        export_row.set_activatable_widget(export_btn)
        library.add(export_row)

        clear_row = Adw.ActionRow(
            title="Clear library",
            subtitle="Delete every recipe and its images",
        )
        clear_btn = Gtk.Button(label="Clear")
        clear_btn.add_css_class("destructive-action")
        clear_btn.set_valign(Gtk.Align.CENTER)
        clear_btn.connect("clicked", self._on_clear_clicked)
        clear_row.add_suffix(clear_btn)
        clear_row.set_activatable_widget(clear_btn)
        library.add(clear_row)
        page.add(library)

        reset_group = Adw.PreferencesGroup(
            title="Reset",
            description="Restore preferences and panel sizes to the original Pinch Bowl defaults. Your recipes and tags are left alone.",
        )
        reset_row = Adw.ActionRow(
            title="Restore defaults",
            subtitle="Color scheme, font, size, sort, and panels",
        )
        reset_btn = Gtk.Button(label="Reset")
        reset_btn.add_css_class("destructive-action")
        reset_btn.set_valign(Gtk.Align.CENTER)
        reset_btn.connect("clicked", self._on_reset_clicked)
        reset_row.add_suffix(reset_btn)
        reset_row.set_activatable_widget(reset_btn)
        reset_group.add(reset_row)
        page.add(reset_group)

        self.add(page)
        GLib.idle_add(self._finish_init)

    def _finish_init(self) -> bool:
        self._applying = False
        return False

    def _font_subtitle(self, font) -> str:
        return KIND_LABEL.get(font.kind, "Sans")

    def _on_scheme(self, row: Adw.ComboRow, *_args: object) -> None:
        if self._applying:
            return
        values = ("system", "light", "dark")
        index = int(row.get_selected())
        self.settings.update(color_scheme=values[index])

    def _on_font(self, row: Adw.ComboRow, *_args: object) -> None:
        if self._applying:
            return
        index = int(row.get_selected())
        if index < 0 or index >= len(self.font_choices):
            return
        font = self.font_choices[index]
        row.set_subtitle(self._font_subtitle(font))
        self.settings.update(recipe_font=font.id)

    def _on_font_size(self, row: Adw.SpinRow, *_args: object) -> None:
        if self._applying:
            return
        self.settings.update(font_size=int(row.get_value()))

    def _import_mela_clicked(self, *_args: object) -> None:
        if self._on_import_mela:
            self._on_import_mela()

    def _export_mela_clicked(self, *_args: object) -> None:
        if self._on_export_mela:
            self._on_export_mela()

    def _watch_subtitle(self) -> str:
        path = (self.settings.mela_watch_path or "").strip()
        if not path:
            return "Auto-import new recipes when this file changes"
        return path

    def _sync_watch_row(self) -> None:
        path = (self.settings.mela_watch_path or "").strip()
        self.watch_row.set_subtitle(self._watch_subtitle())
        self.watch_clear.set_sensitive(bool(path))

    def _choose_watch_clicked(self, *_args: object) -> None:
        dialog = Gtk.FileDialog(title="Watch Mela export")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        mela = Gtk.FileFilter(name="Mela recipes")
        mela.add_pattern("*.melarecipe")
        mela.add_pattern("*.melarecipes")
        mela.add_pattern("*.json")
        filters.append(mela)
        all_files = Gtk.FileFilter(name="All files")
        all_files.add_pattern("*")
        filters.append(all_files)
        dialog.set_filters(filters)
        current = (self.settings.mela_watch_path or "").strip()
        if current:
            dialog.set_initial_file(Gio.File.new_for_path(current))

        def on_open(_d: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
            try:
                file = dialog.open_finish(result)
            except GLib.Error:
                return
            if file is None:
                return
            path = Path(file.get_path() or "")
            if not path:
                return
            self.settings.update(
                mela_watch_path=str(path),
                mela_watch_mtime=0.0,
                mela_watch_size=0,
            )
            self._sync_watch_row()
            if self._on_mela_watch_changed:
                self._on_mela_watch_changed()

        # PreferencesDialog is not a Gtk.Window; FileDialog needs one (or None).
        parent = self.get_ancestor(Gtk.Window)
        if not isinstance(parent, Gtk.Window):
            root = self.get_root()
            parent = root if isinstance(root, Gtk.Window) else None
        dialog.open(parent, None, on_open)

    def _clear_watch_clicked(self, *_args: object) -> None:
        self.settings.update(
            mela_watch_path="",
            mela_watch_mtime=0.0,
            mela_watch_size=0,
        )
        self._sync_watch_row()
        if self._on_mela_watch_changed:
            self._on_mela_watch_changed()

    def _on_clear_clicked(self, *_args: object) -> None:
        dialog = Adw.AlertDialog(
            heading="Clear library?",
            body=(
                "Every recipe and its images will be permanently deleted from this device. "
                "This cannot be undone."
            ),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("clear", "Clear library")
        dialog.set_response_appearance("clear", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_clear_response)
        dialog.present(self)

    def _on_clear_response(self, _dialog: Adw.AlertDialog, response: str) -> None:
        if response != "clear" or self._on_clear_library is None:
            return
        self._on_clear_library()

    def _on_reset_clicked(self, *_args: object) -> None:
        dialog = Adw.AlertDialog(
            heading="Restore defaults?",
            body=(
                "Color scheme, recipe font, font size, sort order, window size, and "
                "sidebar widths will reset to the Pinch Bowl defaults. Your recipes and "
                "tags are not changed."
            ),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("reset", "Restore defaults")
        dialog.set_response_appearance("reset", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_reset_response)
        dialog.present(self)

    def _on_reset_response(self, _dialog: Adw.AlertDialog, response: str) -> None:
        if response != "reset":
            return
        self.settings.reset_to_defaults()
        self._sync_controls_from_settings()
        if self._on_defaults_restored is not None:
            self._on_defaults_restored()

    def _sync_controls_from_settings(self) -> None:
        self._applying = True
        try:
            settings = self.settings
            self.scheme_row.set_selected(
                {"system": 0, "light": 1, "dark": 2}.get(settings.color_scheme, 0)
            )
            self.size_row.set_value(settings.font_size)
            font_ids = [font.id for font in self.font_choices]
            font_index = (
                font_ids.index(settings.recipe_font)
                if settings.recipe_font in font_ids
                else 0
            )
            self.font_row.set_selected(font_index)
            self.font_row.set_subtitle(
                self._font_subtitle(self.font_choices[font_index])
            )
            self._sync_watch_row()
        finally:
            self._applying = False
