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
from pinchbowl.ai_paste import is_ai_recipe
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
from pinchbowl.settings import NAV_MAX_SP, NAV_MIN_SP, SORT_KEYS, Settings
from pinchbowl.sidebar import Sidebar
from pinchbowl.store import Store
from pinchbowl.urls import clean_url, openable_url
from pinchbowl.welcome_dialog import WelcomeDialog

# Recipe list column — Newsflash article-list range (sp).
_LIST_MIN_SP = 360
_LIST_MAX_SP = 600


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
        self._width_save = None
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
        self._nav_toggles: list[Gtk.Button] = []

        # Breakpoints remove the window's implicit minimum; set an explicit floor.
        # ≥402 clears the ingredients column’s natural min inside AdwBreakpointBin.
        self.set_size_request(420, 480)

        self.sidebar = Sidebar(tags_expanded=settings.sidebar_tags_expanded)
        self.sidebar.on_filter = self._on_filter
        self.sidebar.on_destination_chosen = self._close_nav_overlay
        self.sidebar.on_tags_expanded_changed = self._on_tags_expanded_changed

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

        self.list_heading = Gtk.Label(label="All", xalign=0)
        self.list_heading.add_css_class("heading")
        self.count_label = Gtk.Label(xalign=0)
        self.count_label.add_css_class("dim-label")
        self.count_label.add_css_class("caption")
        heading_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        heading_box.add_css_class("pb-list-heading")
        heading_box.set_hexpand(True)
        heading_box.set_valign(Gtk.Align.CENTER)
        heading_box.append(self.list_heading)
        heading_box.append(self.count_label)

        self.list_header = Adw.HeaderBar()
        self.list_header.add_css_class("pb-list-header")
        self.list_header.set_show_end_title_buttons(False)
        # Empty title widget hides NavigationPage title; heading is pack_start
        # so it left-aligns with the search field (see .pb-list-header CSS).
        self.list_header.set_title_widget(Gtk.Box())
        self.list_header.pack_start(self._make_nav_toggle())
        self.list_header.pack_start(heading_box)
        self.list_header.pack_end(add_btn)
        self.list_header.pack_end(self.sort_btn)

        search_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        search_bar.add_css_class("pb-search-bar")
        search_bar.append(self.search)

        self.list = Gtk.ListBox()
        self.list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.list.set_activate_on_single_click(True)
        self.list.set_show_separators(False)
        self.list.add_css_class("pb-recipe-list")
        self.list.set_filter_func(self._row_matches_filter)
        self.list.set_sort_func(self._row_sort)
        self.list.connect("row-selected", self._on_row_selected)
        # Re-clicking the selected row does not re-emit row-selected; activation
        # covers back-from-detail then tapping the same recipe again.
        self.list.connect("row-activated", self._on_row_activated)

        list_scroll = Gtk.ScrolledWindow()
        list_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        list_scroll.set_vexpand(True)
        list_scroll.set_child(self.list)

        list_tv = Adw.ToolbarView()
        list_tv.add_css_class("pb-list-pane")
        list_tv.add_top_bar(self.list_header)
        list_tv.add_top_bar(search_bar)
        list_tv.set_content(list_scroll)

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

        detail_header = Adw.HeaderBar()
        # Recipe title is in the body; keep the header bar clear.
        detail_header.set_title_widget(Gtk.Box())
        detail_header.pack_start(self._make_nav_toggle())
        detail_header.pack_end(self.recipe_view.menu_btn)
        detail_tv = Adw.ToolbarView()
        detail_tv.add_top_bar(detail_header)
        detail_tv.set_content(self.recipe_view)

        self.groceries_view = GroceriesView(store, self.settings)
        self.groceries_view.set_menu_model(menu)
        self.groceries_view.on_clear_done = self._clear_grocery_done
        self.groceries_view.on_clear_all = self._clear_grocery_all
        self.groceries_view.pack_nav_toggle(self._make_nav_toggle())

        self.ai_generate_view = AiGenerateView()
        self.ai_generate_view.set_menu_model(menu)
        self.ai_generate_view.on_add = self._add_ai_recipe
        self.ai_generate_view.on_toast = self._toast
        self.ai_generate_view.pack_nav_toggle(self._make_nav_toggle())

        # Detail slot host: recipe detail, or groceries / AI destinations.
        self.detail_host = Gtk.Stack()
        self.detail_host.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.detail_host.set_transition_duration(180)
        self.detail_host.set_hhomogeneous(False)
        self.detail_host.set_vhomogeneous(False)
        self.detail_host.set_hexpand(True)
        self.detail_host.set_vexpand(True)
        self.detail_host.add_named(detail_tv, "recipes")
        self.detail_host.add_named(self.groceries_view, "groceries")
        self.detail_host.add_named(self.ai_generate_view, "ai_generate")
        self.detail_host.connect("notify::visible-child", self._sync_list_chrome)

        # Newsflash-style MultiLayoutView: default (Overlay+Nav) and medium
        # (detail-forward with nav|list behind). Slots: sidebar, list, detail.
        self.multi_layout = Adw.MultiLayoutView()
        self.multi_layout.set_hexpand(True)
        self.multi_layout.set_vexpand(True)

        self.default_outer = Adw.OverlaySplitView()
        self.default_outer.set_min_sidebar_width(NAV_MIN_SP)
        self.default_outer.set_max_sidebar_width(NAV_MAX_SP)
        self.default_outer.set_sidebar_width_unit(Adw.LengthUnit.SP)
        self.default_outer.set_hexpand(True)
        self.default_outer.set_vexpand(True)
        self.default_outer.connect("notify::collapsed", self._on_nav_collapsed_changed)
        self.default_outer.connect("notify::show-sidebar", self._on_nav_collapsed_changed)

        self.default_inner = Adw.NavigationSplitView()
        self.default_inner.set_min_sidebar_width(_LIST_MIN_SP)
        self.default_inner.set_max_sidebar_width(_LIST_MAX_SP)
        self.default_inner.set_sidebar_width_unit(Adw.LengthUnit.SP)
        self.default_inner.connect("notify::collapsed", self._sync_list_chrome)
        self.default_inner.connect("notify::show-content", self._sync_list_chrome)

        self.default_list_page = Adw.NavigationPage.new(
            Adw.LayoutSlot.new("list"), "Recipes"
        )
        self.default_list_page.set_tag("list")
        self.default_list_page.set_can_pop(False)
        self.default_detail_page = Adw.NavigationPage.new(
            Adw.LayoutSlot.new("detail"), "Recipe"
        )
        self.default_detail_page.set_tag("detail")
        self.default_inner.set_sidebar(self.default_list_page)
        self.default_inner.set_content(self.default_detail_page)

        default_sidebar_page = Adw.NavigationPage.new(
            Adw.LayoutSlot.new("sidebar"), "Navigation"
        )
        default_sidebar_page.set_tag("sidebar")
        default_inner_page = Adw.NavigationPage.new(self.default_inner, "inner")
        default_inner_page.set_tag("inner")
        default_inner_page.set_can_pop(False)
        self.default_outer.set_sidebar(default_sidebar_page)
        self.default_outer.set_content(default_inner_page)

        layout_default = Adw.Layout.new(self.default_outer)
        layout_default.set_name("default")

        self.medium_outer = Adw.NavigationSplitView()
        self.medium_outer.set_collapsed(True)
        self.medium_outer.set_min_sidebar_width(NAV_MIN_SP)
        self.medium_outer.set_max_sidebar_width(NAV_MAX_SP)
        self.medium_outer.set_sidebar_width_unit(Adw.LengthUnit.SP)
        self.medium_outer.set_sidebar_width_fraction(0.5)
        self.medium_outer.connect("notify::collapsed", self._sync_list_chrome)
        self.medium_outer.connect("notify::show-content", self._sync_list_chrome)
        self.medium_outer.connect(
            "notify::show-content", self._on_nav_collapsed_changed
        )

        self.medium_inner = Adw.NavigationSplitView()
        self.medium_inner.set_min_sidebar_width(NAV_MIN_SP)
        self.medium_inner.set_max_sidebar_width(NAV_MAX_SP)
        self.medium_inner.set_sidebar_width_unit(Adw.LengthUnit.SP)
        self.medium_inner.set_sidebar_width_fraction(0.35)
        # Nav | list side by side on the lists page (Newsflash medium).
        self.medium_inner.set_collapsed(False)
        self.medium_inner.connect("notify::collapsed", self._on_nav_collapsed_changed)
        self.medium_inner.connect("notify::show-content", self._on_nav_collapsed_changed)

        medium_sidebar_page = Adw.NavigationPage.new(
            Adw.LayoutSlot.new("sidebar"), "Navigation"
        )
        medium_sidebar_page.set_tag("sidebar")
        medium_sidebar_page.set_can_pop(False)
        self.medium_list_page = Adw.NavigationPage.new(
            Adw.LayoutSlot.new("list"), "Recipes"
        )
        self.medium_list_page.set_tag("list")
        self.medium_list_page.set_can_pop(False)
        self.medium_inner.set_sidebar(medium_sidebar_page)
        self.medium_inner.set_content(self.medium_list_page)

        medium_lists_page = Adw.NavigationPage.new(self.medium_inner, "lists")
        medium_lists_page.set_tag("lists")
        self.medium_detail_page = Adw.NavigationPage.new(
            Adw.LayoutSlot.new("detail"), "Recipe"
        )
        self.medium_detail_page.set_tag("detail")
        self.medium_outer.set_sidebar(medium_lists_page)
        self.medium_outer.set_content(self.medium_detail_page)

        layout_medium = Adw.Layout.new(self.medium_outer)
        layout_medium.set_name("medium")

        self.multi_layout.add_layout(layout_default)
        self.multi_layout.add_layout(layout_medium)
        self.multi_layout.set_child("sidebar", self.sidebar)
        self.multi_layout.set_child("list", list_tv)
        self.multi_layout.set_child("detail", self.detail_host)
        self.multi_layout.set_layout_name("default")

        # Compat aliases used by width snapshot / older call sites.
        self.outer_split = self.default_outer
        self.inner_split = self.default_inner
        self.list_page = self.default_list_page
        self.detail_page = self.default_detail_page
        self.content_stack = self.detail_host

        self.shell_bin = Adw.BreakpointBin()
        self.shell_bin.set_hexpand(True)
        self.shell_bin.set_vexpand(True)
        self.shell_bin.set_size_request(420, 400)
        self.shell_bin.set_child(self.multi_layout)

        self._shell_band = "wide"
        # Once a destination is chosen, keep the overlay closed until the user
        # opens it again via the nav toggle (avoids layout flicker re-showing it).
        self._nav_overlay_dismissed = False
        self._apply_split_widths_from_settings()

        self.toaster = Adw.ToastOverlay(child=self.shell_bin)

        self.cook_view = CookView(settings)
        self.cook_view.on_exit = self._exit_cook_mode

        self.mode_stack = Gtk.Stack()
        self.mode_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.mode_stack.set_transition_duration(220)
        self.mode_stack.set_hhomogeneous(False)
        self.mode_stack.set_vhomogeneous(False)
        self.mode_stack.set_hexpand(True)
        self.mode_stack.set_vexpand(True)
        self.mode_stack.add_named(self.toaster, "library")
        self.mode_stack.add_named(self.cook_view, "cook")
        self.set_content(self.mode_stack)
        self.add_css_class("pb-window")

        self._install_shell_breakpoints()
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
        self.connect("map", self._on_first_map)
        self._on_nav_collapsed_changed()

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

    def _show_recipes(self, *, dismiss_nav: bool = True) -> None:
        self.detail_host.set_visible_child_name("recipes")
        self._apply_destination_shell()
        if dismiss_nav:
            self._close_nav_overlay()

    def _show_groceries(self, *, dismiss_nav: bool = True) -> None:
        self.detail_host.set_visible_child_name("groceries")
        self.groceries_view.reload()
        self._apply_destination_shell()
        if dismiss_nav:
            self._close_nav_overlay()

    def _show_ai_generate(self, *, dismiss_nav: bool = True) -> None:
        self.detail_host.set_visible_child_name("ai_generate")
        self._apply_destination_shell()
        if dismiss_nav:
            self._close_nav_overlay()

    def _destination_content_only(self) -> bool:
        return self.detail_host.get_visible_child_name() in (
            "groceries",
            "ai_generate",
        )

    def _apply_destination_shell(self) -> None:
        """Groceries / AI replace list+detail: force content-forward."""
        dest = self._destination_content_only()
        self.default_detail_page.set_can_pop(not dest)
        self.medium_detail_page.set_can_pop(not dest)
        if dest:
            self.default_inner.set_collapsed(True)
            self.default_inner.set_show_content(True)
            self.medium_outer.set_show_content(True)
            self._sync_list_chrome()
            return
        # Leaving a destination: restore list|detail collapse for this band.
        if self._shell_band in ("wide", "nav_overlay"):
            self.default_inner.set_collapsed(False)
        elif self._shell_band == "medium":
            # Back to nav|list; row selection / _show_recipe opens detail again.
            self.medium_outer.set_show_content(False)
        elif self._shell_band == "narrow":
            self.default_inner.set_collapsed(True)
        self._sync_list_chrome()

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
        title = titles.get(key, "Recipes")
        self.list_heading.set_text(title)
        self._set_list_title(title)

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
            self._set_detail_title("Recipe")
            self._sync_selection_actions()
            if self._recipe_nav_collapsed():
                self._set_show_recipe_content(False)
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
        # Opening the nav overlay remaps the sidebar and GTK re-emits
        # row-selected for the current row. That must not dismiss the overlay
        # (first-shrink bug); only a real destination change should.
        prev = self._filter
        child = self.detail_host.get_visible_child_name()
        if key == "groceries":
            same_dest = child == "groceries"
        elif key == "ai_generate":
            same_dest = child == "ai_generate"
        else:
            same_dest = child == "recipes" and prev == (key, tag)

        self._filter = (key, tag)
        if key == "groceries":
            self._show_groceries(dismiss_nav=not same_dest)
            return
        if key == "ai_generate":
            self._show_ai_generate(dismiss_nav=not same_dest)
            return
        self._show_recipes(dismiss_nav=not same_dest)
        self._apply_list_view()
        if not same_dest:
            self._close_nav_overlay()

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
        self._open_recipe_row(row)

    def _on_row_activated(self, _list: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        if self._selecting or not isinstance(row, RecipeRow):
            return
        self._open_recipe_row(row)

    def _open_recipe_row(self, row: RecipeRow) -> None:
        detail = self.recipe_view.recipe
        if (
            row.recipe_id == self._current_id
            and detail is not None
            and detail.id == row.recipe_id
        ):
            # Same recipe already loaded (narrow: back to list, tap again).
            if self._recipe_nav_collapsed():
                self._set_show_recipe_content(True)
                self._close_nav_overlay()
            return
        recipe = self.store.get(row.recipe_id)
        if recipe is None:
            return
        self._current_id = recipe.id
        self._sync_selection_actions()
        self._show_recipe(recipe)

    def _show_recipe(self, recipe: Recipe) -> None:
        self.recipe_view.load_recipe(recipe)
        self._set_detail_title(recipe.title or "Recipe")
        if self._recipe_nav_collapsed():
            self._set_show_recipe_content(True)
        self._close_nav_overlay()

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
        self.groceries_view.sync_sort_from_settings()
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
    def _restore_maximized_after_cook(self) -> bool:
        if not self.is_fullscreen() and not self.is_maximized():
            self.maximize()
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
        if is_ai_recipe(
            source=recipe.source,
            source_url=recipe.source_url,
            ai_prompt=recipe.ai_prompt,
        ):
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

    def _make_nav_toggle(self) -> Gtk.Button:
        btn = Gtk.Button(icon_name="sidebar-show-symbolic")
        btn.add_css_class("flat")
        btn.set_tooltip_text("Show navigation")
        btn.set_visible(False)
        btn.connect("clicked", self._on_nav_toggle_clicked)
        self._nav_toggles.append(btn)
        return btn

    def _is_default_layout(self) -> bool:
        name = self.multi_layout.get_layout_name() or "default"
        return name == "default"

    def _set_list_title(self, title: str) -> None:
        self.default_list_page.set_title(title)
        self.medium_list_page.set_title(title)

    def _set_detail_title(self, title: str) -> None:
        self.default_detail_page.set_title(title)
        self.medium_detail_page.set_title(title)

    def _recipe_nav_collapsed(self) -> bool:
        if self._is_default_layout():
            return self.default_inner.get_collapsed()
        return self.medium_outer.get_collapsed()

    def _set_show_recipe_content(self, show: bool) -> None:
        self.default_inner.set_show_content(show)
        self.medium_outer.set_show_content(show)

    def _nav_toggle_needed(self) -> bool:
        if self._is_default_layout():
            return self.default_outer.get_collapsed()
        # Medium: back to nav|list from detail, or reveal filters if collapsed.
        if self.medium_outer.get_show_content():
            return True
        return self.medium_inner.get_collapsed()

    def _on_nav_toggle_clicked(self, *_args: object) -> None:
        if self._is_default_layout():
            self._nav_overlay_dismissed = False
            self.default_outer.set_show_sidebar(True)
            return
        # Medium: leave detail for nav|list (Newsflash mid-band).
        self.medium_outer.set_show_content(False)
        self.medium_inner.set_collapsed(False)

    def _on_nav_collapsed_changed(self, *_args: object) -> None:
        needed = self._nav_toggle_needed()
        for btn in self._nav_toggles:
            btn.set_visible(needed)
        # Only the wide band should keep the sidebar permanently visible.
        # Using `not get_collapsed()` re-opened the overlay after a destination
        # pick whenever collapsed flickered during the layout update.
        if self._shell_band == "wide" and self._is_default_layout():
            self._nav_overlay_dismissed = False
            self.default_outer.set_show_sidebar(True)
        elif self._nav_overlay_dismissed and self._nav_overlay_active():
            self.default_outer.set_show_sidebar(False)
        self._sync_list_chrome()

    def _sync_list_chrome(self, *_args: object) -> None:
        """Show window controls on the list header when it is the only visible page."""
        recipes = self.detail_host.get_visible_child_name() == "recipes"
        if self._is_default_layout():
            list_alone = (
                recipes
                and self.default_inner.get_collapsed()
                and not self.default_inner.get_show_content()
            )
        else:
            list_alone = (
                recipes
                and self.medium_outer.get_collapsed()
                and not self.medium_outer.get_show_content()
            )
        self.list_header.set_show_end_title_buttons(list_alone)

    def _nav_overlay_active(self) -> bool:
        """True when nav should behave as a dismissible overlay."""
        if not self._is_default_layout():
            return False
        return self._shell_band in ("nav_overlay", "narrow") or self.default_outer.get_collapsed()

    def _close_nav_overlay(self) -> None:
        if not self._nav_overlay_active():
            # Medium keeps nav|list; nothing to dismiss after a filter pick.
            return
        self._nav_overlay_dismissed = True
        self.default_outer.set_show_sidebar(False)
        # Selection/focus inside the overlay can restore show-sidebar in the
        # same turn or on the next frame — re-assert a few times.
        GLib.idle_add(self._ensure_nav_overlay_closed)
        GLib.timeout_add(50, self._ensure_nav_overlay_closed)

    def _ensure_nav_overlay_closed(self) -> bool:
        if not self._nav_overlay_dismissed or not self._is_default_layout():
            return GLib.SOURCE_REMOVE
        if self._shell_band in ("nav_overlay", "narrow"):
            if not self.default_outer.get_collapsed():
                self.default_outer.set_collapsed(True)
            self.default_outer.set_show_sidebar(False)
        elif self.default_outer.get_collapsed():
            self.default_outer.set_show_sidebar(False)
        return GLib.SOURCE_REMOVE

    def _install_shell_breakpoints(self) -> None:
        # Newsflash-style bands on the shell BreakpointBin (sp). Adw applies
        # only the last matching breakpoint — each handler sets full state.
        # Ingredient stacking stays on the detail BreakpointBin (~1100sp).
        wide_collapse = Adw.Breakpoint.new(
            Adw.BreakpointCondition.parse("max-width: 1000sp")
        )
        wide_collapse.connect("apply", self._on_shell_nav_overlay_apply)
        wide_collapse.connect("unapply", self._on_shell_wide_apply)
        self.shell_bin.add_breakpoint(wide_collapse)

        medium = Adw.Breakpoint.new(
            Adw.BreakpointCondition.parse(
                "max-width: 800sp and min-width: 500sp"
            )
        )
        medium.connect("apply", self._on_shell_medium_apply)
        medium.connect("unapply", self._on_shell_medium_unapply)
        self.shell_bin.add_breakpoint(medium)

        narrow = Adw.Breakpoint.new(
            Adw.BreakpointCondition.parse("max-width: 500sp")
        )
        narrow.connect("apply", self._on_shell_narrow_apply)
        narrow.connect("unapply", self._on_shell_narrow_unapply)
        self.shell_bin.add_breakpoint(narrow)

    def _set_compact_destinations(self, compact: bool) -> None:
        self.groceries_view.set_compact(compact)
        self.ai_generate_view.set_compact(compact)

    def _apply_shell_band(self, band: str) -> None:
        """band: wide | nav_overlay | medium | narrow."""
        # Preserve whether a recipe (or destination) is showing across topology.
        if self._is_default_layout():
            showing = self.default_inner.get_show_content()
        else:
            showing = self.medium_outer.get_show_content()
        self._shell_band = band
        if band == "wide":
            self.multi_layout.set_layout_name("default")
            self._nav_overlay_dismissed = False
            self.default_outer.set_collapsed(False)
            self.default_outer.set_show_sidebar(True)
            self.default_inner.set_collapsed(False)
            self.default_inner.set_show_content(showing)
            self._set_compact_destinations(False)
        elif band == "nav_overlay":
            self.multi_layout.set_layout_name("default")
            self.default_outer.set_collapsed(True)
            # Collapse auto-hides; keep dismissed clear so the user can open
            # the overlay (sidebar remap re-select must not look like a pick).
            self.default_outer.set_show_sidebar(False)
            self._nav_overlay_dismissed = False
            self.default_outer.set_max_sidebar_width(
                min(float(NAV_MAX_SP), self.default_outer.get_max_sidebar_width())
            )
            self.default_inner.set_collapsed(False)
            self.default_inner.set_show_content(showing)
            self._set_compact_destinations(True)
        elif band == "medium":
            self.multi_layout.set_layout_name("medium")
            self._nav_overlay_dismissed = False
            self.medium_outer.set_collapsed(True)
            self.medium_outer.set_show_content(showing)
            # Lists page: navigation beside recipe list; detail overlays.
            self.medium_inner.set_collapsed(False)
            self._set_compact_destinations(True)
        else:  # narrow
            self.multi_layout.set_layout_name("default")
            self.default_outer.set_collapsed(True)
            self.default_outer.set_show_sidebar(False)
            self._nav_overlay_dismissed = False
            self.default_outer.set_max_sidebar_width(
                min(float(NAV_MAX_SP), self.default_outer.get_max_sidebar_width())
            )
            self.default_inner.set_collapsed(True)
            self.default_inner.set_show_content(showing)
            self._set_compact_destinations(True)
        self._apply_destination_shell()
        self._on_nav_collapsed_changed()

    def _on_shell_wide_apply(self, *_args: object) -> None:
        self._apply_shell_band("wide")

    def _on_shell_nav_overlay_apply(self, *_args: object) -> None:
        self._apply_shell_band("nav_overlay")

    def _on_shell_medium_apply(self, *_args: object) -> None:
        self._apply_shell_band("medium")

    def _on_shell_medium_unapply(self, *_args: object) -> None:
        # Leaving the 500–800 band: either narrow, nav_overlay, or wide.
        # width<=0 (unmap / early lifecycle): pick nav_overlay so _shell_band
        # does not stay stuck on medium until the next real resize.
        width = self.shell_bin.get_width()
        if 0 < width <= 500:
            self._apply_shell_band("narrow")
        elif width <= 1000:
            self._apply_shell_band("nav_overlay")
        else:
            self._apply_shell_band("wide")

    def _on_shell_narrow_apply(self, *_args: object) -> None:
        self._apply_shell_band("narrow")

    def _on_shell_narrow_unapply(self, *_args: object) -> None:
        width = self.shell_bin.get_width()
        if 500 < width <= 800:
            self._apply_shell_band("medium")
        elif width <= 1000:
            self._apply_shell_band("nav_overlay")
        else:
            self._apply_shell_band("wide")

    def _clamp_list_width(self, value: int) -> int:
        return max(_LIST_MIN_SP, min(_LIST_MAX_SP, value))

    def _clamp_nav_width(self, value: int) -> int:
        return max(NAV_MIN_SP, min(NAV_MAX_SP, value))

    def _apply_split_widths_from_settings(self) -> None:
        nav = self._clamp_nav_width(int(self.settings.nav_width))
        listing = self._clamp_list_width(int(self.settings.list_width))
        win_w = max(nav + listing + 400, int(self.settings.window_width) or 1480)
        self.default_outer.set_min_sidebar_width(NAV_MIN_SP)
        self.default_outer.set_max_sidebar_width(float(nav))
        self.default_outer.set_sidebar_width_fraction(nav / win_w)
        self.medium_outer.set_min_sidebar_width(NAV_MIN_SP)
        self.medium_outer.set_max_sidebar_width(float(nav))
        self.medium_outer.set_sidebar_width_fraction(0.5)
        self.medium_inner.set_min_sidebar_width(NAV_MIN_SP)
        self.medium_inner.set_max_sidebar_width(float(nav))
        self.medium_inner.set_sidebar_width_fraction(0.35)
        self.default_inner.set_min_sidebar_width(_LIST_MIN_SP)
        self.default_inner.set_max_sidebar_width(float(listing))
        # Newsflash uses ~0.40 of the content area for the article list.
        self.default_inner.set_sidebar_width_fraction(0.40)

    def _snapshot_split_widths(self) -> tuple[int, int]:
        win_w = max(1, self.get_width())
        nav = int(round(self.default_outer.get_max_sidebar_width()))
        if (
            self._is_default_layout()
            and not self.default_outer.get_collapsed()
            and self.sidebar.get_mapped()
        ):
            allocated = self.sidebar.get_width()
            if allocated >= NAV_MIN_SP:
                nav = allocated
        listing = int(round(self.default_inner.get_max_sidebar_width()))
        list_widget = self.multi_layout.get_child("list")
        if (
            self._is_default_layout()
            and not self.default_inner.get_collapsed()
            and list_widget is not None
            and list_widget.get_mapped()
        ):
            allocated_list = list_widget.get_width()
            if allocated_list >= _LIST_MIN_SP:
                listing = allocated_list
        nav = self._clamp_nav_width(nav)
        listing = self._clamp_list_width(listing)
        self.default_outer.set_min_sidebar_width(NAV_MIN_SP)
        self.default_outer.set_max_sidebar_width(float(nav))
        self.default_outer.set_sidebar_width_fraction(nav / max(win_w, nav + 400))
        self.medium_outer.set_min_sidebar_width(NAV_MIN_SP)
        self.medium_outer.set_max_sidebar_width(float(nav))
        self.medium_inner.set_min_sidebar_width(NAV_MIN_SP)
        self.medium_inner.set_max_sidebar_width(float(nav))
        self.default_inner.set_min_sidebar_width(_LIST_MIN_SP)
        self.default_inner.set_max_sidebar_width(float(listing))
        self.default_inner.set_sidebar_width_fraction(0.40)
        return nav, listing

    def apply_layout_from_settings(self) -> None:
        self._apply_split_widths_from_settings()
        self.recipe_view.apply_appearance()

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

    def _schedule_width_save(self) -> None:
        if not self._ready:
            return
        if self._width_save is not None:
            GLib.source_remove(self._width_save)
        self._width_save = GLib.timeout_add(250, self._save_widths)

    def _save_widths(self) -> bool:
        self._width_save = None
        if self._cook_active or self.is_fullscreen():
            return False
        nav, listing = self._snapshot_split_widths()
        self.settings.update(
            notify=False,
            nav_width=nav,
            list_width=listing,
            window_width=self.get_width(),
            window_height=self.get_height(),
            window_maximized=self.is_maximized(),
        )
        return False

    def _on_window_size(self, *_args: object) -> None:
        if not self._ready:
            return
        # Don't persist fullscreen cook geometry as the normal window size.
        if self._cook_active or self.is_fullscreen():
            return
        self._schedule_width_save()

    def _on_close(self, *_args: object) -> bool:
        self._closed = True
        if self._mela_watch is not None:
            self._mela_watch.stop()
        if self._cook_active:
            self.cook_view.clear_timers()
        nav, listing = self._snapshot_split_widths()
        kwargs: dict = {
            "notify": False,
            "nav_width": nav,
            "list_width": listing,
        }
        if self._cook_active or self.is_fullscreen():
            kwargs["window_maximized"] = self._cook_was_maximized if self._cook_active else self.is_maximized()
        else:
            kwargs["window_width"] = self.get_width()
            kwargs["window_height"] = self.get_height()
            kwargs["window_maximized"] = self.is_maximized()
        self.settings.update(**kwargs)
        return False
