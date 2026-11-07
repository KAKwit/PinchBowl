from __future__ import annotations

import urllib.request
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango

from pinchbowl.adjust_dialog import AdjustDialog
from pinchbowl.ai_generate_view import AiGenerateView
from pinchbowl.cook_view import CookView
from pinchbowl.groceries_dialog import grocery_candidate_lines, present_groceries_dialog
from pinchbowl.groceries_view import GroceriesView
from pinchbowl.images import apply_mapped_image
from pinchbowl.mela import MelaExportSession, MelaImportSession
from pinchbowl.mela_watch import MelaWatch
from pinchbowl.models import Recipe
from pinchbowl.paths import APP_NAME, version
from pinchbowl.portals import copy_text, open_uri, share_uri
from pinchbowl.preferences import PreferencesDialog
from pinchbowl.recipe_io import (
    export_filename,
    export_melarecipe_bytes,
    export_schema_bytes,
    import_recipe_file,
)
from pinchbowl.recipe_row import RecipeRow
from pinchbowl.recipe_view import RecipeView
from pinchbowl.schema_recipe import extract_recipe_from_html
from pinchbowl.settings import SORT_KEYS, Settings
from pinchbowl.sidebar import Sidebar
from pinchbowl.store import Store
from pinchbowl.urls import clean_url, openable_url
from pinchbowl.welcome_dialog import WelcomeDialog


class PinchBowlWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application, store: Store, settings: Settings) -> None:
        super().__init__(application=app, title=APP_NAME)
        self.set_default_size(settings.window_width, settings.window_height)
        if settings.window_maximized:
            self.maximize()
        self.store = store
        self.settings = settings
        self._filter = ("all", None)
        self._query = ""
        self._search_ids: set[int] | None = None
        self._recent_ids: set[int] = set()
        self._current_id: int | None = None
        self._pane_save = None
        self._layout_sync = None
        self._ready = False
        self._selecting = False
        self._cook_active = False
        self._cook_was_maximized = False
        self._cook_was_fullscreen = False
        self._closed = False
        self._url_import_gen = 0
        self._mela_import_gen = 0
        self._mela_export_gen = 0
        self._mela_import_busy = False
        self._mela_watch: MelaWatch | None = None
        self._welcome_dialog: WelcomeDialog | None = None
        self._welcome_scheduled = False

        self.sidebar = Sidebar(tags_expanded=settings.sidebar_tags_expanded)
        self.sidebar.on_filter = self._on_filter
        self.sidebar.on_tags_expanded_changed = self._on_tags_expanded_changed
        self.sidebar.set_size_request(160, -1)

        # Recipe list column
        self.search = Gtk.SearchEntry()
        self.search.set_placeholder_text("Search recipes")
        self.search.set_hexpand(True)
        self.search.connect("search-changed", self._on_search)

        add_btn = Gtk.Button(icon_name="list-add-symbolic")
        add_btn.add_css_class("flat")
        add_btn.set_tooltip_text("Add recipe from URL (Ctrl+N)")
        add_btn.set_action_name("win.add-url")

        self.sort_btn = Gtk.MenuButton()
        self.sort_btn.add_css_class("flat")
        self.sort_btn.set_tooltip_text("Sort recipes")
        self._sync_sort_icon()
        sort_menu = Gio.Menu()
        by_section = Gio.Menu()
        by_section.append("Date added", "win.sort-by::created")
        by_section.append("Title", "win.sort-by::title")
        by_section.append("Total time", "win.sort-by::time")
        sort_menu.append_section("Sort by", by_section)
        dir_section = Gio.Menu()
        dir_section.append("Ascending", "win.sort-dir::asc")
        dir_section.append("Descending", "win.sort-dir::desc")
        sort_menu.append_section(None, dir_section)
        self.sort_btn.set_menu_model(sort_menu)

        list_toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        list_toolbar.add_css_class("pb-list-toolbar")
        self.list_heading = Gtk.Label(label="All", xalign=0)
        self.list_heading.add_css_class("heading")
        self.count_label = Gtk.Label(xalign=0)
        self.count_label.add_css_class("dim-label")
        self.count_label.add_css_class("caption")
        heading_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        heading_box.set_hexpand(True)
        heading_box.append(self.list_heading)
        heading_box.append(self.count_label)
        list_toolbar.append(heading_box)
        list_toolbar.append(self.sort_btn)
        list_toolbar.append(add_btn)

        search_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        search_bar.add_css_class("pb-search-bar")
        search_bar.set_margin_start(10)
        search_bar.set_margin_end(10)
        search_bar.set_margin_bottom(8)
        search_bar.append(self.search)

        self.list = Gtk.ListBox()
        self.list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.list.set_show_separators(False)
        self.list.add_css_class("pb-recipe-list")
        self.list.set_filter_func(self._row_matches_filter)
        self.list.set_sort_func(self._row_sort)
        self.list.connect("row-selected", self._on_row_selected)

        list_scroll = Gtk.ScrolledWindow()
        list_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        list_scroll.set_vexpand(True)
        list_scroll.set_child(self.list)

        list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        list_box.add_css_class("pb-list-pane")
        list_box.set_size_request(240, -1)
        list_box.append(Gtk.WindowHandle(child=list_toolbar))
        list_box.append(search_bar)
        list_box.append(list_scroll)
        self.list_box = list_box

        self.recipe_view = RecipeView(settings)
        self.recipe_view.on_cook = self._on_cook
        self.recipe_view.on_adjust = self._on_adjust
        self.recipe_view.on_groceries = self._on_groceries
        self.recipe_view.on_open_source = self._open_source
        self.recipe_view.on_share_source = self._share_source
        self.recipe_view.on_copy_source = self._copy_source
        self.recipe_view.on_rename = self._rename_recipe
        self.recipe_view.on_toggle_favorite = self._toggle_favorite
        self.recipe_view.on_edit_tags = self._edit_tags
        self.recipe_view.on_set_image = self._set_recipe_image

        menu = Gio.Menu()
        menu.append("Add from URL…", "win.add-url")
        menu.append("Import recipe…", "win.import-recipe")
        export_menu = Gio.Menu()
        export_menu.append("Melarecipe…", "win.export-melarecipe")
        export_menu.append("Schema.org JSON…", "win.export-schema")
        menu.append_submenu("Export recipe", export_menu)
        menu.append("Delete recipe", "win.delete-recipe")
        menu.append("Preferences", "win.preferences")
        menu.append("Welcome", "win.welcome")
        menu.append("About Pinch Bowl", "win.about")
        self.recipe_view.set_menu_model(menu)

        self.groceries_view = GroceriesView(store)
        self.groceries_view.set_menu_model(menu)
        self.groceries_view.on_clear_done = self._clear_grocery_done
        self.groceries_view.on_clear_all = self._clear_grocery_all

        self.ai_generate_view = AiGenerateView()
        self.ai_generate_view.set_menu_model(menu)
        self.ai_generate_view.on_add = self._add_ai_recipe
        self.ai_generate_view.on_toast = self._toast

        self.list_paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.list_paned.add_css_class("pb-paned")
        self.list_paned.set_wide_handle(False)
        self.list_paned.set_resize_start_child(False)
        self.list_paned.set_resize_end_child(True)
        self.list_paned.set_shrink_start_child(False)
        self.list_paned.set_shrink_end_child(True)
        self.list_paned.set_start_child(list_box)
        self.list_paned.set_end_child(self.recipe_view)

        self.content_stack = Gtk.Stack()
        self.content_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.content_stack.set_transition_duration(180)
        self.content_stack.set_hexpand(True)
        self.content_stack.set_vexpand(True)
        self.content_stack.add_named(self.list_paned, "recipes")
        self.content_stack.add_named(self.groceries_view, "groceries")
        self.content_stack.add_named(self.ai_generate_view, "ai_generate")

        self.outer_paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.outer_paned.add_css_class("pb-paned")
        self.outer_paned.set_wide_handle(False)
        self.outer_paned.set_resize_start_child(False)
        self.outer_paned.set_resize_end_child(True)
        self.outer_paned.set_shrink_start_child(False)
        self.outer_paned.set_shrink_end_child(True)
        self.outer_paned.set_start_child(self.sidebar)
        self.outer_paned.set_end_child(self.content_stack)
        self.outer_paned.set_hexpand(True)
        self.outer_paned.set_vexpand(True)

        # Apply remembered divider positions before the first map so the
        # window does not open at the children's natural widths and then jump.
        self.outer_paned.set_position(settings.nav_width)
        self.list_paned.set_position(settings.list_width)

        self.outer_paned.connect("notify::position", self._on_pane_moved)
        self.list_paned.connect("notify::position", self._on_pane_moved)

        self.toaster = Adw.ToastOverlay(child=self.outer_paned)

        self.cook_view = CookView(settings)
        self.cook_view.on_exit = self._exit_cook_mode

        self.mode_stack = Gtk.Stack()
        self.mode_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.mode_stack.set_transition_duration(220)
        self.mode_stack.set_hexpand(True)
        self.mode_stack.set_vexpand(True)
        self.mode_stack.add_named(self.toaster, "library")
        self.mode_stack.add_named(self.cook_view, "cook")
        self.set_content(self.mode_stack)
        self.add_css_class("pb-window")

        self._install_actions()
        key_controller = Gtk.EventControllerKey()
        key_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        key_controller.connect("key-pressed", self._on_window_key_pressed)
        self.add_controller(key_controller)
        self.connect("notify::default-width", self._on_window_size)
        self.connect("notify::default-height", self._on_window_size)
        self.connect("notify::maximized", self._on_window_size)
        self.connect("close-request", self._on_close)

        self.refresh()
        self._mela_watch = MelaWatch(
            self.settings,
            self.store,
            is_busy=lambda: self._mela_import_busy,
            on_imported=self._on_mela_watch_imported,
            on_error=self._on_mela_watch_error,
        )
        self._mela_watch.restart()
        GLib.idle_add(self._mela_watch_check_later)
        # Reaffirm positions once the window has a real allocation (maximize /
        # compositor sizing). No-op visually if they already match.
        self.connect("map", self._on_first_map)

    def _mela_watch_check_later(self) -> bool:
        if self._closed or self._mela_watch is None:
            return False
        self._mela_watch.check_now()
        return False

    def _on_mela_watch_imported(self, count: int) -> None:
        if self._closed:
            return
        self.refresh()
        label = f"Imported {count} new recipe" + ("" if count == 1 else "s")
        self.toaster.add_toast(Adw.Toast(title=label))

    def _on_mela_watch_error(self, message: str) -> None:
        if self._closed:
            return
        self.toaster.add_toast(Adw.Toast(title=message))

    def _restart_mela_watch(self) -> None:
        if self._mela_watch is None:
            return
        self._mela_watch.restart()
        self._mela_watch.check_now()

    def _install_actions(self) -> None:
        actions = {
            "preferences": self.show_preferences,
            "about": self._about,
            "welcome": self._show_welcome,
            "add-url": self._add_from_url,
            "import-recipe": self._import_recipe,
            "find": lambda: self.search.grab_focus(),
        }
        for name, handler in actions.items():
            action = Gio.SimpleAction.new(name, None)

            def _activate(_a: Gio.SimpleAction, _p: GLib.Variant | None, h=handler) -> None:
                h()

            action.connect("activate", _activate)
            self.add_action(action)

        self._export_melarecipe_action = Gio.SimpleAction.new("export-melarecipe", None)
        self._export_melarecipe_action.connect(
            "activate", lambda *_: self._export_recipe("melarecipe")
        )
        self.add_action(self._export_melarecipe_action)

        self._export_schema_action = Gio.SimpleAction.new("export-schema", None)
        self._export_schema_action.connect(
            "activate", lambda *_: self._export_recipe("schema")
        )
        self.add_action(self._export_schema_action)

        self._delete_recipe_action = Gio.SimpleAction.new("delete-recipe", None)
        self._delete_recipe_action.connect(
            "activate", lambda *_: self._delete_recipe()
        )
        self.add_action(self._delete_recipe_action)
        self._sync_selection_actions()

        sort_by = Gio.SimpleAction.new_stateful(
            "sort-by",
            GLib.VariantType.new("s"),
            GLib.Variant.new_string(self.settings.list_sort),
        )
        sort_by.connect("change-state", self._on_sort_by)
        self.add_action(sort_by)

        sort_dir = Gio.SimpleAction.new_stateful(
            "sort-dir",
            GLib.VariantType.new("s"),
            GLib.Variant.new_string(
                "asc" if self.settings.list_sort_ascending else "desc"
            ),
        )
        sort_dir.connect("change-state", self._on_sort_dir)
        self.add_action(sort_dir)

    def _sync_selection_actions(self) -> None:
        enabled = self._current_id is not None
        self._export_melarecipe_action.set_enabled(enabled)
        self._export_schema_action.set_enabled(enabled)
        self._delete_recipe_action.set_enabled(enabled)

    def _sync_sort_icon(self) -> None:
        icon = (
            "view-sort-ascending-symbolic"
            if self.settings.list_sort_ascending
            else "view-sort-descending-symbolic"
        )
        self.sort_btn.set_icon_name(icon)

    def _sync_sort_actions_from_settings(self) -> None:
        sort_by = self.lookup_action("sort-by")
        if isinstance(sort_by, Gio.SimpleAction):
            sort_by.set_state(GLib.Variant.new_string(self.settings.list_sort))
        sort_dir = self.lookup_action("sort-dir")
        if isinstance(sort_dir, Gio.SimpleAction):
            sort_dir.set_state(
                GLib.Variant.new_string(
                    "asc" if self.settings.list_sort_ascending else "desc"
                )
            )
        self._sync_sort_icon()

    def _on_sort_by(
        self, action: Gio.SimpleAction, value: GLib.Variant | None
    ) -> None:
        if value is None:
            return
        key = value.get_string()
        if key not in SORT_KEYS:
            return
        action.set_state(value)
        self.settings.update(list_sort=key, notify=False)
        self._apply_list_view()

    def _on_sort_dir(
        self, action: Gio.SimpleAction, value: GLib.Variant | None
    ) -> None:
        if value is None:
            return
        ascending = value.get_string() == "asc"
        action.set_state(value)
        self.settings.update(list_sort_ascending=ascending, notify=False)
        self._sync_sort_icon()
        self._apply_list_view()

    def refresh(self) -> None:
        self.sidebar.set_tags(self.store.list_tags())
        if self._filter[0] == "groceries":
            self._show_groceries()
        elif self._filter[0] == "ai_generate":
            self._show_ai_generate()
        else:
            self._show_recipes()
            self._reload_list()

    def _show_recipes(self) -> None:
        self.content_stack.set_visible_child_name("recipes")

    def _show_groceries(self) -> None:
        self.content_stack.set_visible_child_name("groceries")
        self.groceries_view.reload()

    def _show_ai_generate(self) -> None:
        self.content_stack.set_visible_child_name("ai_generate")

    def _reload_list(self) -> None:
        """Rebuild every list row from SQLite (imports, edits, deletes).

        Always loads the full library; sidebar/search filters run in the
        ListBox so switching All / Favorites / tags stays instant.
        """
        key, _tag = self._filter
        if key in ("groceries", "ai_generate"):
            return
        recipes = self.store.list_recipes(filter_key="all", query="")
        recent = sorted(recipes, key=lambda r: r.updated_at, reverse=True)[:20]
        self._recent_ids = {r.id for r in recent}
        if self._query.strip():
            hits = self.store.list_recipes(filter_key="all", query=self._query)
            self._search_ids = {r.id for r in hits}
        else:
            self._search_ids = None

        self._selecting = True
        while (row := self.list.get_row_at_index(0)) is not None:
            self.list.remove(row)
        for recipe in recipes:
            self.list.append(RecipeRow(recipe))
        self._selecting = False
        self._apply_list_view()

    def _apply_list_view(self) -> None:
        """Re-filter/sort existing rows — used for sidebar, search, and sort changes."""
        key, tag = self._filter
        if key in ("groceries", "ai_generate"):
            return
        titles = {
            "all": "All",
            "recent": "Most Recent",
            "favorites": "Favorites",
            "untagged": "Untagged",
            "tag": tag or "Tag",
        }
        self.list_heading.set_text(titles.get(key, "Recipes"))

        keep_id = self._current_id
        self._selecting = True
        self.list.invalidate_filter()
        self.list.invalidate_sort()

        n = 0
        first_visible: RecipeRow | None = None
        keep_row: RecipeRow | None = None
        i = 0
        while (row := self.list.get_row_at_index(i)) is not None:
            i += 1
            if not isinstance(row, RecipeRow) or not row.get_child_visible():
                continue
            n += 1
            if first_visible is None:
                first_visible = row
            if keep_id is not None and row.recipe_id == keep_id:
                keep_row = row
        self.count_label.set_text(f"{n} recipe" + ("" if n == 1 else "s"))

        target = keep_row or first_visible
        if target is None:
            self._selecting = False
            self._current_id = None
            self.recipe_view.show_empty()
            self._sync_selection_actions()
            return

        # Selection alone is not enough: after import we set _current_id before
        # the list rebuilds, so the row looks "current" while detail is still
        # empty/stale. Only skip reload when the detail view matches.
        detail = self.recipe_view.recipe
        already_shown = (
            target.recipe_id == self._current_id
            and detail is not None
            and detail.id == target.recipe_id
        )
        self.list.select_row(target)
        self._selecting = False
        if already_shown:
            return
        recipe = self.store.get(target.recipe_id)
        if recipe is None:
            return
        self._current_id = recipe.id
        self._sync_selection_actions()
        self._show_recipe(recipe)

    def _row_matches_filter(self, row: Gtk.ListBoxRow) -> bool:
        if not isinstance(row, RecipeRow):
            return False
        key, tag = self._filter
        if key in ("groceries", "ai_generate"):
            return False
        if self._search_ids is not None and row.recipe_id not in self._search_ids:
            return False
        if key == "all":
            return True
        if key == "favorites":
            return row.favorite
        if key == "untagged":
            return not row.tags
        if key == "tag" and tag:
            needle = tag.casefold()
            return any(t.casefold() == needle for t in row.tags)
        if key == "recent":
            return row.recipe_id in self._recent_ids
        return True

    def _row_sort(self, row1: Gtk.ListBoxRow, row2: Gtk.ListBoxRow) -> int:
        if not isinstance(row1, RecipeRow) or not isinstance(row2, RecipeRow):
            return 0
        # Most Recent keeps newest-first regardless of the sort menu.
        if self._filter[0] == "recent":
            if row1.updated_at != row2.updated_at:
                return -1 if row1.updated_at > row2.updated_at else 1
            return row1.recipe_id - row2.recipe_id

        ascending = self.settings.list_sort_ascending
        mul = 1 if ascending else -1
        sort = self.settings.list_sort
        if sort == "title":
            if row1.sort_title != row2.sort_title:
                cmp = -1 if row1.sort_title < row2.sort_title else 1
                return cmp * mul
            return row1.recipe_id - row2.recipe_id
        if sort == "time":
            m1, m2 = row1.sort_minutes, row2.sort_minutes
            if m1 is None and m2 is None:
                return row1.recipe_id - row2.recipe_id
            if m1 is None:
                return 1
            if m2 is None:
                return -1
            if m1 != m2:
                return (-1 if m1 < m2 else 1) * mul
            return row1.recipe_id - row2.recipe_id
        if row1.created_at != row2.created_at:
            return (-1 if row1.created_at < row2.created_at else 1) * mul
        return row1.recipe_id - row2.recipe_id

    def _on_filter(self, key: str, tag: str | None) -> None:
        self._filter = (key, tag)
        if key == "groceries":
            self._show_groceries()
            return
        if key == "ai_generate":
            self._show_ai_generate()
            return
        self._show_recipes()
        self._apply_list_view()

    def _on_tags_expanded_changed(self, expanded: bool) -> None:
        self.settings.update(sidebar_tags_expanded=expanded, notify=False)

    def _on_search(self, entry: Gtk.SearchEntry) -> None:
        if self._filter[0] in ("groceries", "ai_generate"):
            return
        self._query = entry.get_text()
        q = self._query.strip()
        if q:
            hits = self.store.list_recipes(filter_key="all", query=q)
            self._search_ids = {r.id for r in hits}
        else:
            self._search_ids = None
        self._apply_list_view()

    def _on_row_selected(self, _list: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        if self._selecting or row is None:
            return
        if not isinstance(row, RecipeRow):
            return
        detail = self.recipe_view.recipe
        if (
            row.recipe_id == self._current_id
            and detail is not None
            and detail.id == row.recipe_id
        ):
            return
        recipe = self.store.get(row.recipe_id)
        if recipe is None:
            return
        self._current_id = recipe.id
        self._sync_selection_actions()
        self._show_recipe(recipe)

    def _show_recipe(self, recipe: Recipe) -> None:
        self.recipe_view.load_recipe(recipe)

    def show_preferences(self) -> None:
        dialog = PreferencesDialog(
            self.settings,
            on_import_mela=self._import_mela,
            on_export_mela=self._export_mela,
            on_clear_library=self._clear_library,
            on_defaults_restored=self._on_defaults_restored,
            on_mela_watch_changed=self._restart_mela_watch,
        )
        dialog.present(self)

    def _clear_library(self) -> None:
        count = self.store.clear_library()
        self._current_id = None
        self._sync_selection_actions()
        self.refresh()
        self.recipe_view.show_empty()
        label = f"Cleared {count} recipe" + ("" if count == 1 else "s")
        self.toaster.add_toast(Adw.Toast(title=label))

    def _on_defaults_restored(self) -> None:
        self.apply_layout_from_settings()
        self.recipe_view.apply_appearance()
        self.cook_view.apply_appearance()
        self._sync_sort_actions_from_settings()
        self.refresh()
        self.toaster.add_toast(Adw.Toast(title="Defaults restored"))

    def _show_welcome(self) -> None:
        if self._closed:
            return
        if self._welcome_dialog is not None:
            return

        def mark_seen() -> None:
            self._welcome_dialog = None
            if self._closed:
                return
            if not self.settings.welcome_seen:
                self.settings.update(welcome_seen=True, notify=False)

        dialog = WelcomeDialog(on_closed=mark_seen)
        self._welcome_dialog = dialog
        dialog.present(self)

    def _about(self) -> None:
        dialog = Adw.AboutDialog(
            application_name=APP_NAME,
            application_icon="dev.kakwit.PinchBowl",
            developer_name="KAKwit",
            version=version(),
            developers=["KAKwit"],
            copyright="© 2026 KAKwit",
            license_type=Gtk.License.GPL_3_0,
            website="https://github.com/KAKwit/PinchBowl",
            issue_url="https://github.com/KAKwit/PinchBowl/issues",
            comments=(
                "Browse your recipes by tag or favorite, then open one to "
                "cook with larger type, step focus, and kitchen timers.\n\n"
                "Add recipes from a website, a Mela export, a recipe file, "
                "or AI Generate — copy a prompt, paste the Schema.org JSON "
                "back in, and save it to your library.\n\n"
                "Scale servings, switch between metric and US units, and "
                "send ingredients to a shared groceries list you can tick "
                "off as you shop."
            ),
        )
        dialog.add_acknowledgement_section(
            "Inspiration",
            ["Mela https://mela.recipes"],
        )
        dialog.add_acknowledgement_section(
            "Timer alarm",
            [
                "Alarm clock beep by Kesu (CC0) "
                "https://freesound.org/people/Kesu/sounds/182351/",
            ],
        )
        dialog.present(self)

    def cook_mode_active(self) -> bool:
        return self._cook_active

    def _refresh_appearance(self) -> None:
        app = self.get_application()
        apply = getattr(app, "apply_appearance", None)
        if callable(apply):
            apply()

    def _on_cook(self) -> None:
        recipe = self.recipe_view.recipe
        if recipe is None or self._cook_active:
            return
        self._cook_was_maximized = self.is_maximized()
        self._cook_was_fullscreen = self.is_fullscreen()
        self._cook_active = True
        factor, units = self.recipe_view.adjust_state()
        self.cook_view.load_recipe(
            recipe, adjust_factor=factor, adjust_units=units
        )
        self.mode_stack.set_visible_child_name("cook")
        self._refresh_appearance()
        if not self.is_fullscreen():
            self.fullscreen()
        self.cook_view.grab_focus()

    def _exit_cook_mode(self) -> None:
        if not self._cook_active:
            return
        self._cook_active = False
        self.cook_view.clear_timers()
        self.mode_stack.set_visible_child_name("library")
        self._refresh_appearance()
        if self.is_fullscreen() and not self._cook_was_fullscreen:
            self.unfullscreen()
        if self._cook_was_maximized and not self.is_maximized():
            # Unfullscreen is async; restore maximize on the next idle.
            GLib.idle_add(self._restore_maximized_after_cook)
        # Hero/side column sized against the cook/fullscreen allocation; refresh
        # after the library pane is visible and window geometry has settled.
        self._cook_layout_tries = 0
        GLib.idle_add(self._sync_layout_after_cook)

    def _restore_maximized_after_cook(self) -> bool:
        if not self.is_fullscreen() and not self.is_maximized():
            self.maximize()
        return False

    def _sync_layout_after_cook(self) -> bool:
        self._cook_layout_tries += 1
        if self.is_fullscreen() and self._cook_layout_tries < 40:
            return True
        self.recipe_view.sync_layout()
        if self._cook_layout_tries < 8:
            # A couple of follow-up passes after unfullscreen reallocates panes.
            GLib.timeout_add(50, self.recipe_view.sync_layout)
        return False

    def _on_adjust(self) -> None:
        if self.recipe_view.recipe is None:
            return
        AdjustDialog(self.recipe_view).present(self)

    def _on_groceries(self) -> None:
        recipe = self.recipe_view.recipe
        if recipe is None:
            return
        factor, units = self.recipe_view.adjust_state()
        lines = grocery_candidate_lines(recipe.ingredients, factor, units)

        def on_add(selected: list[str]) -> None:
            added = self.store.add_grocery_items(selected)
            if not added:
                return
            label = f"Added {added} item" + ("" if added == 1 else "s") + " to Groceries"
            self.toaster.add_toast(Adw.Toast(title=label))
            self.sidebar.select_filter("groceries")

        present_groceries_dialog(self, lines, on_add=on_add)

    def _clear_grocery_done(self) -> None:
        removed = self.store.clear_grocery_done()
        self.groceries_view.reload()
        if removed:
            label = f"Cleared {removed} done item" + ("" if removed == 1 else "s")
            self.toaster.add_toast(Adw.Toast(title=label))

    def _clear_grocery_all(self) -> None:
        items = self.store.list_grocery_items()
        if not items:
            return
        dialog = Adw.AlertDialog(
            heading="Clear all groceries?",
            body="This removes every item from your list.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("clear", "Clear all")
        dialog.set_response_appearance("clear", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def on_response(_d: Adw.AlertDialog, response: str) -> None:
            if response != "clear":
                return
            self.store.clear_grocery_all()
            self.groceries_view.reload()
            self.toaster.add_toast(Adw.Toast(title="Groceries cleared"))

        dialog.connect("response", on_response)
        dialog.present(self)

    def _toggle_favorite(self, value: bool) -> None:
        if self._current_id is None:
            return
        self.store.set_favorite(self._current_id, value)
        i = 0
        while (row := self.list.get_row_at_index(i)) is not None:
            if isinstance(row, RecipeRow) and row.recipe_id == self._current_id:
                row.favorite = value
                break
            i += 1
        self._apply_list_view()
        recipe = self.store.get(self._current_id)
        if recipe is not None:
            self._show_recipe(recipe)

    def _on_tags_changed(self, tags: list[str]) -> None:
        if self._current_id is None:
            return
        self.store.set_tags(self._current_id, tags)
        self.refresh()
        recipe = self.store.get(self._current_id)
        if recipe is not None:
            self._show_recipe(recipe)

    def _rename_recipe(self) -> None:
        if self._current_id is None:
            return
        recipe = self.store.get(self._current_id)
        if recipe is None:
            return

        dialog = Adw.AlertDialog(
            heading="Rename recipe",
            body="Choose a name for this recipe.",
        )
        entry = Gtk.Entry()
        entry.set_text(recipe.title)
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
            if response != "save" or self._current_id is None:
                return
            title = " ".join(entry.get_text().split())
            if not title or title == recipe.title:
                return
            self.store.set_title(self._current_id, title)
            self.refresh()
            updated = self.store.get(self._current_id)
            if updated is not None:
                self._show_recipe(updated)

        entry.connect("activate", lambda *_: dialog.activate_default())
        dialog.connect("response", on_response)
        dialog.present(self)
        entry.grab_focus()
        entry.select_region(0, -1)

    def _edit_tags(self) -> None:
        if self._current_id is None:
            return
        recipe = self.store.get(self._current_id)
        if recipe is None:
            return

        selected = {t.casefold(): t for t in recipe.tags}
        known: list[str] = []
        seen: set[str] = set()
        for name, _count in self.store.list_tags():
            key = name.casefold()
            if key not in seen:
                known.append(name)
                seen.add(key)
        for name in recipe.tags:
            key = name.casefold()
            if key not in seen:
                known.append(name)
                seen.add(key)
        known.sort(key=str.casefold)

        dialog = Adw.AlertDialog(
            heading="Tags",
            body="Tick the tags for this recipe, or add a new one.",
        )
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        root.set_margin_top(8)

        entry_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        entry = Gtk.Entry()
        entry.set_placeholder_text("New tag")
        entry.set_hexpand(True)
        entry.set_activates_default(False)
        add_btn = Gtk.Button(label="Add")
        add_btn.add_css_class("pill")
        entry_row.append(entry)
        entry_row.append(add_btn)
        root.append(entry_row)

        checks_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        checks: dict[str, Gtk.CheckButton] = {}

        def _add_check(name: str, *, active: bool) -> None:
            key = name.casefold()
            if key in checks:
                checks[key].set_active(True)
                return
            check = Gtk.CheckButton(label=name)
            check.set_active(active)
            checks[key] = check
            checks_box.append(check)

        for name in known:
            _add_check(name, active=name.casefold() in selected)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_propagate_natural_height(True)
        scroll.set_min_content_height(80)
        scroll.set_max_content_height(320)
        scroll.set_child(checks_box)
        root.append(scroll)

        empty = Gtk.Label(
            label="No tags yet — add one above.",
            xalign=0,
        )
        empty.add_css_class("dim-label")
        empty.set_visible(not checks)
        root.append(empty)

        def _on_add(*_args: object) -> None:
            tag = " ".join(entry.get_text().split())
            if not tag:
                return
            _add_check(tag, active=True)
            empty.set_visible(False)
            entry.set_text("")
            entry.grab_focus()

        add_btn.connect("clicked", _on_add)
        entry.connect("activate", _on_add)

        dialog.set_extra_child(root)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("done", "Done")
        dialog.set_response_appearance("done", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("done")
        dialog.set_close_response("cancel")

        def on_response(_d: Adw.AlertDialog, response: str) -> None:
            if response != "done":
                return
            tags = [
                check.get_label() or ""
                for key, check in sorted(checks.items(), key=lambda item: item[0])
                if check.get_active() and (check.get_label() or "").strip()
            ]
            self._on_tags_changed(tags)

        dialog.connect("response", on_response)
        dialog.present(self)
        entry.grab_focus()

    def _on_window_key_pressed(
        self,
        _controller: Gtk.EventControllerKey,
        keyval: int,
        _keycode: int,
        state: Gdk.ModifierType,
    ) -> bool:
        if self._cook_active:
            if keyval == Gdk.KEY_Escape:
                # Go through CookView so active timers get a leave warning.
                self.cook_view.request_exit()
                return True
            # Let CookView handle step navigation when it has focus; still
            # route arrows here if focus drifted to a non-editable child.
            if keyval in (
                Gdk.KEY_Up,
                Gdk.KEY_Down,
                Gdk.KEY_Left,
                Gdk.KEY_Right,
                Gdk.KEY_Page_Up,
                Gdk.KEY_Page_Down,
                Gdk.KEY_Home,
                Gdk.KEY_End,
                Gdk.KEY_space,
                Gdk.KEY_KP_Up,
                Gdk.KEY_KP_Down,
                Gdk.KEY_KP_Home,
                Gdk.KEY_KP_End,
            ):
                return self.cook_view.on_key_pressed(
                    _controller, keyval, _keycode, state
                )
            return False
        if keyval not in (Gdk.KEY_Delete, Gdk.KEY_KP_Delete):
            return False
        focus = self.get_focus()
        if isinstance(focus, Gtk.Editable):
            return False
        if isinstance(focus, Gtk.TextView) and focus.get_editable():
            return False
        self._delete_recipe()
        return True

    def _delete_recipe(self) -> None:
        if self._current_id is None:
            return
        recipe = self.store.get(self._current_id)
        if recipe is None:
            return
        dialog = Adw.AlertDialog(
            heading="Delete recipe?",
            body=f"“{recipe.title}” will be removed from your library. This cannot be undone.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def on_response(_d: Adw.AlertDialog, response: str) -> None:
            if response != "delete" or self._current_id is None:
                return
            self.store.delete(self._current_id)
            self._current_id = None
            self._sync_selection_actions()
            self.recipe_view.show_empty()
            self.refresh()
            self.toaster.add_toast(Adw.Toast(title="Recipe deleted"))

        dialog.connect("response", on_response)
        dialog.present(self)

    def _source_url(self) -> str:
        recipe = self.recipe_view.recipe
        if recipe is None:
            return ""
        return openable_url(recipe.source_url, recipe.source)

    def _open_source(self) -> None:
        url = self._source_url()
        if url:
            open_uri(self, url)

    def _copy_source(self) -> None:
        url = self._source_url()
        if not url:
            return
        copy_text(self, url)
        self.toaster.add_toast(Adw.Toast(title="Link copied"))

    def _share_source(self) -> None:
        url = self._source_url()
        if not url:
            return
        # OpenURI portal with ask=True — closest system “share sheet” for URLs
        # until a dedicated Share portal ships.
        share_uri(self, url)

    def _add_from_url(self) -> None:
        dialog = Adw.AlertDialog(
            heading="Add recipe from URL",
            body="Paste a recipe page URL. Tracking parameters are stripped, and duplicates update the existing entry.",
        )
        entry = Gtk.Entry()
        entry.set_placeholder_text("https://…")
        entry.set_hexpand(True)
        entry.set_margin_top(8)
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("add", "Add")
        dialog.set_response_appearance("add", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("add")
        dialog.set_close_response("cancel")

        def on_response(_d: Adw.AlertDialog, response: str) -> None:
            if response != "add":
                return
            url = clean_url(entry.get_text())
            if not url:
                self.toaster.add_toast(Adw.Toast(title="Enter a valid URL"))
                return
            self._import_url_async(url)

        entry.connect("activate", lambda *_: dialog.activate_default())
        dialog.connect("response", on_response)
        dialog.present(self)
        entry.grab_focus()

    def _import_url_async(self, url: str) -> None:
        self.toaster.add_toast(Adw.Toast(title="Fetching recipe…"))
        self._url_import_gen += 1
        gen = self._url_import_gen

        def work() -> None:
            try:
                req = urllib.request.Request(
                    url,
                    headers={"User-Agent": f"PinchBowl/{version()} (GNOME recipe app)"},
                )
                with urllib.request.urlopen(req, timeout=20) as resp:
                    html = resp.read().decode("utf-8", errors="replace")
                mapped = extract_recipe_from_html(html, page_url=url)
                if mapped is not None:
                    mapped = apply_mapped_image(mapped, default_page_url=url)
                GLib.idle_add(self._finish_url_import, mapped, gen)
            except Exception as exc:
                GLib.idle_add(self._toast_error, f"Import failed: {exc}", gen)

        from threading import Thread

        Thread(target=work, daemon=True).start()

    def _toast(self, message: str) -> None:
        self.toaster.add_toast(Adw.Toast(title=message))

    def _toast_error(self, message: str, gen: int | None = None) -> bool:
        if self._closed:
            return False
        if gen is not None and gen != self._url_import_gen:
            return False
        self._toast(message)
        return False

    def _add_ai_recipe(self, mapped: dict) -> None:
        try:
            data = dict(mapped)
            data.pop("_image_url", None)
            data.pop("_page_url", None)
            data["tags"] = []
            data["image_path"] = ""
            ai_prompt = str(data.pop("ai_prompt", "") or "")
            recipe = self.store.upsert_recipe(**data, ai_prompt=ai_prompt)
        except Exception as exc:
            self._toast(f"Could not add recipe: {exc}")
            return
        self.ai_generate_view.clear()
        self._current_id = recipe.id
        self._sync_selection_actions()
        self._filter = ("all", None)
        self.sidebar.select_filter("all")
        self.refresh()
        self._toast(f"Added “{recipe.title}” — click Add photo to attach an image")

    def _set_recipe_image(self, path: str) -> None:
        if self._current_id is None or not path:
            return
        self.store.set_image_path(self._current_id, path)
        recipe = self.store.get(self._current_id)
        if recipe is not None:
            self._show_recipe(recipe)
        self._reload_list()
        self._toast("Photo added")

    def _finish_url_import(self, mapped: dict | None, gen: int) -> bool:
        if self._closed or gen != self._url_import_gen:
            return False
        if mapped is None:
            self.toaster.add_toast(
                Adw.Toast(title="No Schema.org Recipe found on that page")
            )
            return False
        try:
            mapped = dict(mapped)
            mapped.pop("_image_url", None)
            mapped.pop("_page_url", None)
            mapped.pop("tags", None)  # URL import never assigns tags
            recipe = self.store.upsert_recipe(**mapped)
        except Exception as exc:
            self.toaster.add_toast(Adw.Toast(title=f"Import failed: {exc}"))
            return False
        self._current_id = recipe.id
        self._sync_selection_actions()
        self.refresh()
        self.toaster.add_toast(Adw.Toast(title=f"Added “{recipe.title}”"))
        return False

    def _import_recipe(self) -> None:
        dialog = Gtk.FileDialog(title="Import recipe")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        recipes = Gtk.FileFilter(name="Recipe files")
        recipes.add_pattern("*.melarecipe")
        recipes.add_pattern("*.json")
        filters.append(recipes)
        all_files = Gtk.FileFilter(name="All files")
        all_files.add_pattern("*")
        filters.append(all_files)
        dialog.set_filters(filters)

        def on_open(_d: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
            try:
                file = dialog.open_finish(result)
            except GLib.Error:
                return
            if file is None:
                return
            path = Path(file.get_path() or "")
            try:
                recipe = import_recipe_file(path, self.store)
            except Exception as exc:
                self.toaster.add_toast(Adw.Toast(title=f"Import failed: {exc}"))
                return
            self._current_id = recipe.id
            self._sync_selection_actions()
            self._filter = ("all", None)
            self.sidebar.select_filter("all")
            self.refresh()
            self.toaster.add_toast(Adw.Toast(title=f"Imported “{recipe.title}”"))

        dialog.open(self, None, on_open)

    def _export_recipe(self, fmt: str) -> None:
        if self._current_id is None:
            return
        recipe = self.store.get(self._current_id)
        if recipe is None:
            return
        if fmt == "melarecipe":
            title = "Export Melarecipe"
            filename = export_filename(recipe.title, ".melarecipe")
            payload = export_melarecipe_bytes(recipe)
            filter_name = "Melarecipe"
            pattern = "*.melarecipe"
        else:
            title = "Export Schema.org JSON"
            filename = export_filename(recipe.title, ".json")
            payload = export_schema_bytes(recipe)
            filter_name = "Schema.org JSON"
            pattern = "*.json"

        dialog = Gtk.FileDialog(title=title)
        dialog.set_initial_name(filename)
        filters = Gio.ListStore.new(Gtk.FileFilter)
        chosen = Gtk.FileFilter(name=filter_name)
        chosen.add_pattern(pattern)
        filters.append(chosen)
        all_files = Gtk.FileFilter(name="All files")
        all_files.add_pattern("*")
        filters.append(all_files)
        dialog.set_filters(filters)

        def on_save(_d: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
            try:
                file = dialog.save_finish(result)
            except GLib.Error:
                return
            if file is None:
                return
            path = Path(file.get_path() or "")
            try:
                path.write_bytes(payload)
            except OSError as exc:
                self.toaster.add_toast(Adw.Toast(title=f"Export failed: {exc}"))
                return
            self.toaster.add_toast(Adw.Toast(title=f"Exported “{recipe.title}”"))

        dialog.save(self, None, on_save)

    def _import_mela(self) -> None:
        dialog = Gtk.FileDialog(title="Import Mela recipes")
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

        def on_open(_d: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
            try:
                file = dialog.open_finish(result)
            except GLib.Error:
                return
            if file is None:
                return
            path = Path(file.get_path() or "")
            self._run_mela_import(path)

        dialog.open(self, None, on_open)

    def _run_mela_import(self, path: Path) -> None:
        if self._mela_import_busy or (
            self._mela_watch is not None and self._mela_watch.importing
        ):
            self.toaster.add_toast(Adw.Toast(title="A Mela import is already running"))
            return
        try:
            session = MelaImportSession(path, self.store)
        except Exception as exc:
            self.toaster.add_toast(Adw.Toast(title=f"Import failed: {exc}"))
            return
        if session.total == 0:
            session.close()
            self.toaster.add_toast(Adw.Toast(title="No recipes found in that file"))
            return

        self._mela_import_gen += 1
        gen = self._mela_import_gen
        self._mela_import_busy = True
        cancelled = False
        finished = False

        # Fixed width so long recipe titles ellipsize instead of resizing the dialog.
        _import_chars = 36
        progress = Gtk.ProgressBar(show_text=True, hexpand=True)
        progress.set_fraction(0)
        progress.set_text(f"0 of {session.total}")
        status = Gtk.Label(label="Starting…", xalign=0, hexpand=True)
        status.add_css_class("dim-label")
        status.set_ellipsize(Pango.EllipsizeMode.END)
        status.set_width_chars(_import_chars)
        status.set_max_width_chars(_import_chars)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(8)
        box.set_size_request(320, -1)
        box.append(status)
        box.append(progress)

        alert = Adw.AlertDialog(heading="Importing Mela recipes")
        alert.set_extra_child(box)
        alert.add_response("cancel", "Cancel")
        alert.set_close_response("cancel")
        alert.set_default_response("cancel")

        def finish(message: str) -> None:
            nonlocal finished
            if finished:
                return
            finished = True
            self._mela_import_busy = False
            session.close()
            try:
                alert.force_close()
            except Exception:
                pass
            if self._closed or gen != self._mela_import_gen:
                return
            self.refresh()
            self.toaster.add_toast(Adw.Toast(title=message))

        def on_response(_d: Adw.AlertDialog, response: str) -> None:
            nonlocal cancelled
            if response == "cancel" and not finished:
                cancelled = True

        def step() -> bool:
            if self._closed or gen != self._mela_import_gen:
                self._mela_import_busy = False
                session.close()
                return False
            if cancelled:
                n = session.imported
                total = session.total
                finish(
                    f"Imported {n} of {total} (cancelled)"
                    if n
                    else "Import cancelled"
                )
                return False
            try:
                result = session.import_next()
            except Exception as exc:
                finish(f"Import failed: {exc}")
                return False
            if result is None:
                n = session.imported
                finish(
                    f"Imported {n} recipe" + ("" if n == 1 else "s")
                )
                return False
            index, title = result
            total = session.total
            progress.set_fraction(index / total if total else 1.0)
            progress.set_text(f"{index} of {total}")
            status.set_label(f"Importing “{title}”…")
            return True

        alert.connect("response", on_response)
        alert.present(self)
        GLib.idle_add(step)

    def _export_mela(self) -> None:
        recipes = self.store.list_recipes(sort="title", ascending=True)
        if not recipes:
            self.toaster.add_toast(Adw.Toast(title="No recipes to export"))
            return

        dialog = Gtk.FileDialog(title="Export Mela recipes")
        dialog.set_initial_name("Pinch Bowl Recipes.melarecipes")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        mela = Gtk.FileFilter(name="Mela recipes")
        mela.add_pattern("*.melarecipes")
        filters.append(mela)
        all_files = Gtk.FileFilter(name="All files")
        all_files.add_pattern("*")
        filters.append(all_files)
        dialog.set_filters(filters)

        def on_save(_d: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
            try:
                file = dialog.save_finish(result)
            except GLib.Error:
                return
            if file is None:
                return
            path = Path(file.get_path() or "")
            if path.suffix.lower() != ".melarecipes":
                path = path.with_suffix(".melarecipes")
            self._run_mela_export(path, recipes)

        dialog.save(self, None, on_save)

    def _run_mela_export(self, path: Path, recipes: list) -> None:
        try:
            session = MelaExportSession(path, recipes)
        except Exception as exc:
            self.toaster.add_toast(Adw.Toast(title=f"Export failed: {exc}"))
            return

        self._mela_export_gen += 1
        gen = self._mela_export_gen
        cancelled = False
        finished = False

        _export_chars = 36
        progress = Gtk.ProgressBar(show_text=True, hexpand=True)
        progress.set_fraction(0)
        progress.set_text(f"0 of {session.total}")
        status = Gtk.Label(label="Starting…", xalign=0, hexpand=True)
        status.add_css_class("dim-label")
        status.set_ellipsize(Pango.EllipsizeMode.END)
        status.set_width_chars(_export_chars)
        status.set_max_width_chars(_export_chars)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(8)
        box.set_size_request(320, -1)
        box.append(status)
        box.append(progress)

        alert = Adw.AlertDialog(heading="Exporting Mela recipes")
        alert.set_extra_child(box)
        alert.add_response("cancel", "Cancel")
        alert.set_close_response("cancel")
        alert.set_default_response("cancel")

        def finish(message: str, *, discard: bool = False) -> None:
            nonlocal finished
            if finished:
                return
            finished = True
            if discard:
                session.discard()
            session.close()
            try:
                alert.force_close()
            except Exception:
                pass
            if self._closed or gen != self._mela_export_gen:
                return
            self.toaster.add_toast(Adw.Toast(title=message))

        def on_response(_d: Adw.AlertDialog, response: str) -> None:
            nonlocal cancelled
            if response == "cancel" and not finished:
                cancelled = True

        def step() -> bool:
            if self._closed or gen != self._mela_export_gen:
                session.discard()
                session.close()
                return False
            if cancelled:
                finish("Export cancelled", discard=True)
                return False
            try:
                result = session.export_next()
            except Exception as exc:
                finish(f"Export failed: {exc}", discard=True)
                return False
            if result is None:
                n = session.exported
                finish(f"Exported {n} recipe" + ("" if n == 1 else "s"))
                return False
            index, title = result
            total = session.total
            progress.set_fraction(index / total if total else 1.0)
            progress.set_text(f"{index} of {total}")
            status.set_label(f"Exporting “{title}”…")
            return True

        alert.connect("response", on_response)
        alert.present(self)
        GLib.idle_add(step)

    def apply_layout_from_settings(self) -> None:
        self.outer_paned.set_position(self.settings.nav_width)
        self.list_paned.set_position(self.settings.list_width)
        self.recipe_view.apply_appearance()
        # Side column fraction depends on the detail pane's allocated width.
        GLib.idle_add(self.recipe_view.sync_layout)

    def _on_first_map(self, *_args: object) -> None:
        if self._ready:
            return
        self.apply_layout_from_settings()
        self._ready = True
        if not self.settings.welcome_seen and not self._welcome_scheduled:
            self._welcome_scheduled = True
            GLib.idle_add(self._show_welcome_once)

    def _show_welcome_once(self) -> bool:
        if self._closed or self.settings.welcome_seen:
            return False
        self._show_welcome()
        return False

    def _on_pane_moved(self, *_args: object) -> None:
        if not self._ready:
            return
        self._schedule_layout_sync()
        if self._pane_save is not None:
            GLib.source_remove(self._pane_save)
        self._pane_save = GLib.timeout_add(200, self._save_panes)

    def _save_panes(self) -> bool:
        self._pane_save = None
        self.settings.update(
            notify=False,
            nav_width=self.outer_paned.get_position(),
            list_width=self.list_paned.get_position(),
        )
        return False

    def _schedule_layout_sync(self) -> None:
        """Debounce side-column reflow while the window or panes are resizing."""
        if not self._ready:
            return
        if self._layout_sync is not None:
            GLib.source_remove(self._layout_sync)
        self._layout_sync = GLib.timeout_add(50, self._run_layout_sync)

    def _run_layout_sync(self) -> bool:
        self._layout_sync = None
        self.recipe_view.sync_layout()
        return False

    def _on_window_size(self, *_args: object) -> None:
        if not self._ready:
            return
        self._schedule_layout_sync()
        # Don't persist fullscreen cook geometry as the normal window size.
        if self._cook_active or self.is_fullscreen():
            return
        self.settings.update(
            notify=False,
            window_width=self.get_width(),
            window_height=self.get_height(),
            window_maximized=self.is_maximized(),
        )

    def _on_close(self, *_args: object) -> bool:
        self._closed = True
        if self._mela_watch is not None:
            self._mela_watch.stop()
        if self._cook_active:
            self.cook_view.clear_timers()
        kwargs: dict = {
            "notify": False,
            "nav_width": self.outer_paned.get_position(),
            "list_width": self.list_paned.get_position(),
        }
        if self._cook_active or self.is_fullscreen():
            kwargs["window_maximized"] = self._cook_was_maximized if self._cook_active else self.is_maximized()
        else:
            kwargs["window_width"] = self.get_width()
            kwargs["window_height"] = self.get_height()
            kwargs["window_maximized"] = self.is_maximized()
        self.settings.update(**kwargs)
        return False
