from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")

from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango

from pinchbowl.adjust import adjust_ingredients, scale_yield_text
from pinchbowl.ai_paste import AI_SOURCE_LABEL, is_ai_recipe
from pinchbowl.directions import parse_instructions
from pinchbowl.duration import format_duration_text
from pinchbowl.fonts import css_font_family
from pinchbowl.images import cover_crop_pixbuf
from pinchbowl.ingredients import clean_ingredient_line, ingredient_line_markup
from pinchbowl.models import Recipe
from pinchbowl.portals import copy_image, copy_text, read_image_png
from pinchbowl.schema_recipe import normalize_yield_text
from pinchbowl.settings import Settings
from pinchbowl.textutil import decode_entities
from pinchbowl.urls import openable_url, source_display

# Side column (hero + ingredients) as a fraction of the recipe body width.
_SIDE_FRACTION = 1 / 3
# Hero frame is always this aspect (width / height); images are cover-cropped to fit.
_HERO_ASPECT = 1.0  # square
# Horizontal padding inside .pb-side-col (18px each side).
_SIDE_PAD_X = 36


def _meta_icon_label(
    icon_name: str,
    text: str,
    *,
    accent_icon: bool = False,
    bold: bool = False,
    ellipsize: bool = False,
    max_chars: int = 0,
) -> Gtk.Box:
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    icon = Gtk.Image.new_from_icon_name(icon_name)
    icon.set_pixel_size(14)
    icon.set_valign(Gtk.Align.CENTER)
    if accent_icon:
        icon.add_css_class("pb-meta-chip-accent")
    label = Gtk.Label(label=text, xalign=0)
    label.set_valign(Gtk.Align.CENTER)
    if bold:
        label.add_css_class("pb-meta-chip-label")
    if ellipsize:
        label.set_ellipsize(Pango.EllipsizeMode.END)
        if max_chars > 0:
            label.set_max_width_chars(max_chars)
    box.append(icon)
    box.append(label)
    return box


def _meta_part(icon_name: str, text: str) -> Gtk.Widget:
    box = _meta_icon_label(icon_name, text)
    box.add_css_class("pb-meta-part")
    box.set_valign(Gtk.Align.CENTER)
    return box


def _meta_chip(
    icon_name: str,
    text: str,
    tooltip: str,
    *,
    accent_icon: bool = False,
    menu: Gio.MenuModel | None = None,
    on_clicked=None,
    ellipsize: bool = False,
    max_chars: int = 0,
) -> Gtk.Button:
    """Flat icon+label chip used for source, tags, Cook, and Adjust."""
    btn = Gtk.Button()
    btn.add_css_class("flat")
    btn.add_css_class("pb-meta-part")
    btn.add_css_class("pb-meta-chip")
    btn.set_halign(Gtk.Align.START)
    btn.set_valign(Gtk.Align.CENTER)
    btn.set_tooltip_text(tooltip)
    btn.set_child(
        _meta_icon_label(
            icon_name,
            text,
            accent_icon=accent_icon,
            bold=True,
            ellipsize=ellipsize,
            max_chars=max_chars,
        )
    )

    if menu is not None:
        popover = Gtk.PopoverMenu.new_from_model(menu)
        popover.set_parent(btn)

        def _open_menu(*_args: object) -> None:
            popover.popup()

        def _detach_popover(*_args: object) -> None:
            if popover.get_parent() is not None:
                popover.unparent()

        btn.connect("clicked", _open_menu)
        btn.connect("destroy", _detach_popover)
    elif on_clicked is not None:
        btn.connect("clicked", lambda *_: on_clicked())
    return btn


def _tags_label(tags: list[str]) -> str:
    if not tags:
        return "No tags"
    return ", ".join(tags)


def _source_button(icon_name: str, text: str) -> Gtk.Button:
    """Clickable site chip with Open / Copy Link / Share menu."""
    menu = Gio.Menu()
    menu.append("Open", "source.open")
    menu.append("Copy Link", "source.copy")
    menu.append("Share…", "source.share")
    return _meta_chip(
        icon_name,
        text,
        "Open or share this recipe’s source",
        menu=menu,
    )


def _time_part(name: str, value: str) -> Gtk.Widget:
    col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    col.add_css_class("pb-meta-time")
    col.set_valign(Gtk.Align.CENTER)
    heading = Gtk.Label(label=name, xalign=0)
    heading.add_css_class("caption")
    heading.add_css_class("dim-label")
    val = Gtk.Label(label=value, xalign=0)
    val.add_css_class("heading")
    col.append(heading)
    col.append(val)
    return col


class RecipeView(Gtk.Box):
    def __init__(self, settings: Settings) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.settings = settings
        self.recipe: Recipe | None = None
        self.on_cook: Callable[[], None] | None = None
        self.on_adjust: Callable[[], None] | None = None
        self.on_groceries: Callable[[], None] | None = None
        self.on_open_source: Callable[[], None] | None = None
        self.on_share_source: Callable[[], None] | None = None
        self.on_copy_source: Callable[[], None] | None = None
        self.on_rename: Callable[[], None] | None = None
        self.on_toggle_favorite: Callable[[bool], None] | None = None
        self.on_edit_tags: Callable[[], None] | None = None
        self.on_set_image: Callable[[str], None] | None = None
        self._source_actions = Gio.SimpleActionGroup()
        open_a = Gio.SimpleAction.new("open", None)
        open_a.connect(
            "activate", lambda *_: self.on_open_source and self.on_open_source()
        )
        copy_a = Gio.SimpleAction.new("copy", None)
        copy_a.connect(
            "activate", lambda *_: self.on_copy_source and self.on_copy_source()
        )
        share_a = Gio.SimpleAction.new("share", None)
        share_a.connect(
            "activate", lambda *_: self.on_share_source and self.on_share_source()
        )
        self._source_actions.add_action(open_a)
        self._source_actions.add_action(copy_a)
        self._source_actions.add_action(share_a)
        self.insert_action_group("source", self._source_actions)
        self._hero_path: Path | None = None
        self._hero_pixbuf: GdkPixbuf.Pixbuf | None = None
        self._font_css = Gtk.CssProvider()
        self._adjust_factor = 1.0
        self._adjust_units = "original"
        self._yield_label: Gtk.Label | None = None
        self.add_css_class("pb-recipe-view")
        self.set_hexpand(True)
        self.set_vexpand(True)

        chrome = Gtk.WindowHandle()
        chrome.add_css_class("window-chrome")
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        drag = Gtk.Box()
        drag.set_hexpand(True)
        bar.append(drag)

        self.menu_btn = Gtk.MenuButton(icon_name="open-menu-symbolic")
        self.menu_btn.add_css_class("flat")
        bar.append(self.menu_btn)
        bar.append(Gtk.WindowControls(side=Gtk.PackType.END))
        chrome.set_child(bar)
        self.append(chrome)

        empty = Adw.StatusPage(
            icon_name="emblem-documents-symbolic",
            title="No recipe selected",
            description=(
                "Paste a URL, import a Melarecipe, or use AI Generate to add one — "
                "or choose a recipe from the list."
            ),
        )

        self.stack = Gtk.Stack()
        self.stack.set_vexpand(True)
        self.stack.set_hexpand(True)
        self.stack.add_named(empty, "empty")

        self.body_scroll = Gtk.ScrolledWindow()
        self.body_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.body_scroll.set_vexpand(True)
        self.body_scroll.set_hexpand(True)

        self.body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.body.add_css_class("pb-recipe-body")
        self.body.set_margin_start(28)
        self.body.set_margin_end(28)
        self.body.set_margin_top(12)
        self.body.set_margin_bottom(28)
        self.body.set_hexpand(True)
        # Allocation width (not requisition). Pane/window shrinks can miss
        # body notify::width; the view itself tracks the paned end-child size.
        self.connect("notify::width", self._on_viewport_width)
        self.body_scroll.connect("notify::width", self._on_viewport_width)
        self.body.connect("notify::width", self._on_viewport_width)
        self._side_applied = 0
        self._viewport_width = 0

        # Left: heading, description, directions
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        left.set_hexpand(True)
        left.set_valign(Gtk.Align.START)
        left.add_css_class("pb-main-col")

        self.title_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.title_row.add_css_class("pb-title-row")
        self.title = Gtk.Label(xalign=0)
        self.title.add_css_class("pb-recipe-title")
        self.title.set_wrap(True)
        self.title.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.title.set_hexpand(True)
        self.title.set_halign(Gtk.Align.FILL)
        self.title.set_valign(Gtk.Align.CENTER)
        self.favorite_btn = Gtk.Button()
        self.favorite_btn.add_css_class("flat")
        self.favorite_btn.add_css_class("pb-title-edit")
        self.favorite_btn.add_css_class("pb-favorite-btn")
        self.favorite_btn.set_valign(Gtk.Align.CENTER)
        self._favorite_icon = Gtk.Image.new_from_icon_name("non-starred-symbolic")
        self._favorite_icon.set_pixel_size(18)
        self.favorite_btn.set_child(self._favorite_icon)
        self.favorite_btn.connect("clicked", self._on_favorite_clicked)

        self.rename_btn = Gtk.Button()
        self.rename_btn.add_css_class("flat")
        self.rename_btn.add_css_class("pb-title-edit")
        self.rename_btn.set_valign(Gtk.Align.CENTER)
        self.rename_btn.set_tooltip_text("Rename recipe")
        rename_icon = Gtk.Image.new_from_icon_name("document-edit-symbolic")
        rename_icon.set_pixel_size(18)
        self.rename_btn.set_child(rename_icon)
        self.rename_btn.connect(
            "clicked", lambda *_: self.on_rename and self.on_rename()
        )
        self.title_row.append(self.title)
        self.title_row.append(self.favorite_btn)
        self.title_row.append(self.rename_btn)

        self.meta = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        self.meta.add_css_class("pb-meta")
        self.meta.set_valign(Gtk.Align.CENTER)

        self.actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.actions.add_css_class("pb-actions")
        # Widget margin — CSS margin on boxes is unreliable here.
        self.actions.set_margin_top(10)
        self.cook_btn = _meta_chip(
            "emoji-food-symbolic",
            "Cook",
            "Distraction-free cook mode",
            accent_icon=True,
            on_clicked=lambda: self.on_cook and self.on_cook(),
        )
        self.adjust_btn = _meta_chip(
            "accessories-calculator-symbolic",
            "Adjust",
            "Scale servings or convert units",
            accent_icon=True,
            on_clicked=lambda: self.on_adjust and self.on_adjust(),
        )
        self.groceries_btn = _meta_chip(
            "view-list-bullet-symbolic",
            "Groceries",
            "Add ingredients to the groceries list",
            accent_icon=True,
            on_clicked=lambda: self.on_groceries and self.on_groceries(),
        )
        self.actions.append(self.cook_btn)
        self.actions.append(self.adjust_btn)
        self.actions.append(self.groceries_btn)

        self.description = Gtk.Label(xalign=0)
        self.description.set_wrap(True)
        self.description.add_css_class("pb-description")

        self.preamble_sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        self.preamble_sep.add_css_class("pb-preamble-sep")

        self.instr_heading = Gtk.Label(label="Directions", xalign=0)
        self.instr_heading.add_css_class("pb-recipe-heading")
        self.instr_heading.add_css_class("pb-directions-heading")
        self.instructions_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.instructions_box.add_css_class("pb-instructions")

        left.append(self.title_row)
        left.append(self.meta)
        left.append(self.actions)
        left.append(self.description)
        left.append(self.preamble_sep)
        left.append(self.instr_heading)
        left.append(self.instructions_box)

        # Right: hero, then ingredients. Adw.Clamp + size_request pin the column
        # width; labels/hero are capped so they cannot inflate the clamp.
        self.side_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.side_col.add_css_class("pb-side-col")
        self.side_col.set_hexpand(True)
        self.side_col.set_halign(Gtk.Align.FILL)
        self.side_col.set_vexpand(False)
        self.side_col.set_valign(Gtk.Align.START)
        self.side_col.set_overflow(Gtk.Overflow.HIDDEN)

        self.side_clamp = Adw.Clamp()
        self.side_clamp.set_child(self.side_col)
        self.side_clamp.set_hexpand(False)
        self.side_clamp.set_halign(Gtk.Align.END)
        self.side_clamp.set_valign(Gtk.Align.START)

        self.hero_stack = Gtk.Stack()
        self.hero_stack.add_css_class("pb-hero-stack")
        self.hero_stack.set_halign(Gtk.Align.FILL)

        self.hero = Gtk.Picture()
        self.hero.set_content_fit(Gtk.ContentFit.COVER)
        self.hero.set_can_shrink(True)
        self.hero.add_css_class("pb-hero")
        self.hero.set_cursor(Gdk.Cursor.new_from_name("pointer"))
        click = Gtk.GestureClick()
        click.connect("released", self._on_hero_clicked)
        self.hero.add_controller(click)

        self.hero_placeholder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.hero_placeholder.add_css_class("pb-hero-placeholder")
        self.hero_placeholder.set_halign(Gtk.Align.FILL)
        self.hero_placeholder.set_valign(Gtk.Align.FILL)
        self.hero_placeholder.set_hexpand(True)
        self.hero_placeholder.set_vexpand(True)
        self.hero_placeholder.set_cursor(Gdk.Cursor.new_from_name("pointer"))
        self.hero_placeholder.set_tooltip_text("Add a photo — paste or choose a file")
        ph_icon = Gtk.Image.new_from_icon_name("image-x-generic-symbolic")
        ph_icon.set_pixel_size(40)
        ph_icon.add_css_class("dim-label")
        ph_icon.set_halign(Gtk.Align.CENTER)
        ph_label = Gtk.Label(label="Add photo", xalign=0.5)
        ph_label.add_css_class("dim-label")
        ph_label.add_css_class("caption")
        ph_center = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        ph_center.set_halign(Gtk.Align.CENTER)
        ph_center.set_valign(Gtk.Align.CENTER)
        ph_center.set_vexpand(True)
        ph_center.append(ph_icon)
        ph_center.append(ph_label)
        self.hero_placeholder.append(ph_center)
        ph_click = Gtk.GestureClick()
        ph_click.connect("released", self._on_placeholder_clicked)
        self.hero_placeholder.add_controller(ph_click)

        self.hero_stack.add_named(self.hero, "image")
        self.hero_stack.add_named(self.hero_placeholder, "placeholder")
        self.hero_stack.set_visible_child_name("placeholder")

        self.ing_heading = Gtk.Label(label="Ingredients", xalign=0)
        self.ing_heading.add_css_class("pb-recipe-heading")
        self.ingredients_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.ingredients_box.add_css_class("pb-ingredients")
        self.ingredients_box.set_hexpand(False)
        self.ingredients_box.set_halign(Gtk.Align.FILL)

        self.side_col.append(self.hero_stack)
        self.side_col.append(self.ing_heading)
        self.side_col.append(self.ingredients_box)

        self.body.append(left)
        self.body.append(self.side_clamp)

        self.body_scroll.set_child(self.body)
        self.stack.add_named(self.body_scroll, "recipe")
        self.append(self.stack)
        self.show_empty()
        self.apply_appearance()

    def set_menu_model(self, model: Gio.MenuModel) -> None:
        self.menu_btn.set_menu_model(model)

    def show_empty(self) -> None:
        self.recipe = None
        self._hero_path = None
        self._hero_pixbuf = None
        self._reset_adjust_state()
        self.stack.set_visible_child_name("empty")

    def adjust_state(self) -> tuple[float, str]:
        return self._adjust_factor, self._adjust_units

    def apply_adjust(self, factor: float, units: str) -> None:
        self._adjust_factor = factor
        self._adjust_units = units if units in {"original", "metric", "us"} else "original"
        self._refresh_adjusted()

    def reset_adjust(self) -> None:
        self._reset_adjust_state()
        self._refresh_adjusted()

    def _reset_adjust_state(self) -> None:
        self._adjust_factor = 1.0
        self._adjust_units = "original"
        self._set_adjust_chip_active(False)

    def _refresh_adjusted(self) -> None:
        if self.recipe is None:
            return
        self._update_yield_label()
        self._render_ingredients(self.recipe.ingredients)
        self._set_adjust_chip_active(
            abs(self._adjust_factor - 1.0) > 1e-9 or self._adjust_units != "original"
        )
        self._sync_side_widths()
        GLib.idle_add(self._sync_side_widths)

    def _set_adjust_chip_active(self, active: bool) -> None:
        if active:
            self.adjust_btn.add_css_class("pb-adjust-on")
        else:
            self.adjust_btn.remove_css_class("pb-adjust-on")

    def _update_yield_label(self) -> None:
        if self._yield_label is None or self.recipe is None:
            return
        raw = decode_entities(self.recipe.yield_text)
        scaled = scale_yield_text(raw, self._adjust_factor)
        shown = normalize_yield_text(scaled) or scaled
        self._yield_label.set_text(shown)

    def load_recipe(self, recipe: Recipe) -> None:
        self.recipe = recipe
        self._reset_adjust_state()
        self.title.set_text(decode_entities(recipe.title))
        self._set_favorite_ui(recipe.favorite)

        while (child := self.meta.get_first_child()) is not None:
            self.meta.remove(child)
        self._yield_label = None

        # Site + tags share Cook/Adjust spacing so those two chips sit close.
        source_tags = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        source_tags.set_halign(Gtk.Align.START)
        source_tags.set_valign(Gtk.Align.CENTER)

        is_ai = is_ai_recipe(
            source=recipe.source,
            source_url=recipe.source_url,
            ai_prompt=recipe.ai_prompt,
        )
        site, is_web = source_display(recipe.source_url, recipe.source)
        url = (
            ""
            if is_ai
            else openable_url(recipe.source_url, recipe.source)
        )
        if is_ai:
            source_tags.append(
                _meta_chip(
                    "emoji-objects-symbolic",
                    AI_SOURCE_LABEL,
                    "View the AI prompt used for this recipe",
                    on_clicked=self._show_ai_prompt,
                )
            )
        elif site:
            icon = (
                "insert-link-symbolic" if is_web else "x-office-address-book-symbolic"
            )
            if url:
                source_tags.append(_source_button(icon, site))
            else:
                source_tags.append(_meta_part(icon, site))

        source_tags.append(
            _meta_chip(
                "user-bookmarks-symbolic",
                _tags_label(recipe.tags),
                "Edit tags",
                ellipsize=True,
                max_chars=32,
                on_clicked=lambda: self.on_edit_tags and self.on_edit_tags(),
            )
        )
        self.meta.append(source_tags)

        yield_text = normalize_yield_text(decode_entities(recipe.yield_text))
        if yield_text:
            yield_box = _meta_part("system-users-symbolic", yield_text)
            self._yield_label = yield_box.get_last_child()
            self.meta.append(yield_box)

        times = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        times.set_halign(Gtk.Align.END)
        times.set_valign(Gtk.Align.CENTER)
        for label, value in (
            ("Prep", recipe.prep_time),
            ("Cook", recipe.cook_time),
            ("Total", recipe.total_time),
        ):
            shown = format_duration_text(value)
            if shown:
                times.append(_time_part(label, shown))
        if times.get_first_child() is not None:
            spacer = Gtk.Box()
            spacer.set_hexpand(True)
            self.meta.append(spacer)
            self.meta.append(times)

        self.meta.set_visible(self.meta.get_first_child() is not None)

        description = decode_entities(recipe.description)
        has_preamble = bool(description.strip())
        self.description.set_text(description)
        self.description.set_visible(has_preamble)
        self.preamble_sep.set_visible(has_preamble)
        self._render_directions(recipe.instructions)
        self._render_ingredients(recipe.ingredients)
        self._set_hero(recipe.image_path)
        self.stack.set_visible_child_name("recipe")
        # Constrain before the next frame so long ingredient lines cannot
        # inflate the side column's minimum width past the clamp.
        self._sync_side_widths()
        GLib.idle_add(self._sync_side_widths)

    def _render_directions(self, text: str) -> None:
        while (child := self.instructions_box.get_first_child()) is not None:
            self.instructions_box.remove(child)

        parsed, max_digits = parse_instructions(text)
        num_chars = max_digits + 1  # digits + "." / ")"
        for item in parsed:
            kind = item[0]
            if kind == "heading":
                heading = Gtk.Label(label=item[1], xalign=0)
                heading.add_css_class("pb-recipe-heading")
                heading.set_wrap(True)
                heading.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
                self.instructions_box.append(heading)
                continue
            if kind == "plain":
                step = Gtk.Label(xalign=0, label=item[1])
                step.set_wrap(True)
                step.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
                step.set_hexpand(True)
                step.set_halign(Gtk.Align.FILL)
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
                row.add_css_class("pb-dir-step")
                row.set_hexpand(True)
                row.append(step)
                self._make_checkable(row)
                self.instructions_box.append(row)
                continue
            _, marker, body_text = item
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row.add_css_class("pb-dir-step")
            row.set_hexpand(True)
            num = Gtk.Label(xalign=1.0)
            num.set_width_chars(num_chars)
            num.set_max_width_chars(num_chars)
            num.set_use_markup(True)
            num.set_markup(
                f'<span alpha="45%">{GLib.markup_escape_text(marker)}</span>'
            )
            num.set_halign(Gtk.Align.END)
            num.set_valign(Gtk.Align.START)
            num.set_hexpand(False)
            body = Gtk.Label(xalign=0, label=body_text)
            body.set_wrap(True)
            body.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            body.set_hexpand(True)
            body.set_halign(Gtk.Align.FILL)
            body.set_valign(Gtk.Align.START)
            row.append(num)
            row.append(body)
            self._make_checkable(row)
            self.instructions_box.append(row)

    def _render_ingredients(self, text: str) -> None:
        while (child := self.ingredients_box.get_first_child()) is not None:
            self.ingredients_box.remove(child)
        inner = self._inner_width(self._side_width()) if self._side_width() > 0 else 0
        adjusted = adjust_ingredients(text, self._adjust_factor, self._adjust_units)
        for raw in (adjusted or "").splitlines():
            cleaned = clean_ingredient_line(raw)
            markup = ingredient_line_markup(raw)
            if markup is None:
                continue
            label = Gtk.Label(xalign=0)
            label.set_wrap(True)
            label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            label.set_use_markup(True)
            label.set_markup(markup)
            label.set_hexpand(True)
            label.set_halign(Gtk.Align.FILL)
            self._limit_label_width(label, max(40, inner - 16) if inner else 0)
            if cleaned.startswith("#"):
                label.add_css_class("pb-recipe-heading")
                if inner > 0:
                    label.set_size_request(inner, -1)
                self.ingredients_box.append(label)
                continue
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            row.add_css_class("pb-ingredient-line")
            row.set_hexpand(False)
            row.set_halign(Gtk.Align.FILL)
            if inner > 0:
                row.set_size_request(inner, -1)
            row.append(label)
            self._make_checkable(row)
            self.ingredients_box.append(row)

    def _make_checkable(self, widget: Gtk.Widget) -> None:
        """Click-to-highlight a step or ingredient (session-only focus cue).

        Only one sibling stays highlighted — this is not a multi-select checklist.
        """
        widget.add_css_class("pb-checkable")
        widget.set_cursor(Gdk.Cursor.new_from_name("pointer"))
        click = Gtk.GestureClick()
        click.set_button(1)
        click.connect("released", self._on_checkable_released, widget)
        widget.add_controller(click)

    def _clear_checked_in(self, container: Gtk.Widget, *, except_widget: Gtk.Widget | None = None) -> None:
        child = container.get_first_child()
        while child is not None:
            if child is not except_widget and child.has_css_class("pb-checked"):
                child.remove_css_class("pb-checked")
            child = child.get_next_sibling()

    def _on_checkable_released(
        self,
        _gesture: Gtk.GestureClick,
        _n_press: int,
        _x: float,
        _y: float,
        widget: Gtk.Widget,
    ) -> None:
        parent = widget.get_parent()
        if widget.has_css_class("pb-checked"):
            widget.remove_css_class("pb-checked")
            return
        if parent is not None:
            self._clear_checked_in(parent, except_widget=widget)
        widget.add_css_class("pb-checked")

    def _set_favorite_ui(self, favorite: bool) -> None:
        if favorite:
            self._favorite_icon.set_from_icon_name("starred-symbolic")
            self.favorite_btn.add_css_class("pb-favorite-on")
            self.favorite_btn.set_tooltip_text("Remove from favorites")
        else:
            self._favorite_icon.set_from_icon_name("non-starred-symbolic")
            self.favorite_btn.remove_css_class("pb-favorite-on")
            self.favorite_btn.set_tooltip_text("Add to favorites")

    def _on_favorite_clicked(self, *_args: object) -> None:
        if self.recipe is None or self.on_toggle_favorite is None:
            return
        self.on_toggle_favorite(not self.recipe.favorite)

    def _side_width(self) -> int:
        # Prefer the recipe view allocation — that is what the paned actually
        # sized. Fall back to the scrolled body during the first map.
        width = self.get_width()
        if width <= 100:
            width = self.body_scroll.get_width()
        if width <= 100:
            width = self.body.get_width()
        if width <= 100:
            return 0
        # Body margins are outside the fractioned content area.
        margins = self.body.get_margin_start() + self.body.get_margin_end()
        content = max(0, width - margins)
        return max(180, int(content * _SIDE_FRACTION))

    def _inner_width(self, side: int) -> int:
        return max(120, side - _SIDE_PAD_X)

    def _hero_size(self, side: int) -> tuple[int, int]:
        inner = self._inner_width(side)
        height = max(1, int(round(inner / _HERO_ASPECT)))
        return inner, height

    def _limit_label_width(self, label: Gtk.Label, width_px: int) -> None:
        """Keep wrapping labels from reporting a huge natural width."""
        if width_px <= 0:
            return
        # ~7px/char is a safe estimate for the recipe UI font sizes we use.
        label.set_max_width_chars(max(8, width_px // 7))

    def _constrain_side_labels(self, widget: Gtk.Widget, width_px: int) -> None:
        if isinstance(widget, Gtk.Label):
            self._limit_label_width(widget, width_px)
            return
        child = widget.get_first_child() if hasattr(widget, "get_first_child") else None
        while child is not None:
            self._constrain_side_labels(child, max(40, width_px - 16))
            child = child.get_next_sibling()

    def _on_viewport_width(self, *_args: object) -> None:
        width = self.get_width() or self.body_scroll.get_width() or self.body.get_width()
        if width == self._viewport_width:
            return
        self._viewport_width = width
        self._sync_side_widths()

    def sync_layout(self) -> bool:
        """Recompute side column after outer paned positions settle."""
        self._side_applied = 0
        self._viewport_width = 0
        self._sync_side_widths()
        return False

    def _sync_side_widths(self, *_args: object) -> bool:
        side = self._side_width()
        if side <= 0:
            return False
        inner = self._inner_width(side)
        side_changed = side != self._side_applied
        self._side_applied = side

        # Shrink child requisitions first so a previous wide size_request cannot
        # keep the column (and HSCROLL NEVER viewport) pinned open.
        self._layout_hero(side)
        self.ingredients_box.set_size_request(inner, -1)
        self.ingredients_box.set_hexpand(False)
        self.ing_heading.set_size_request(inner, -1)
        self._limit_label_width(self.ing_heading, inner)
        child = self.ingredients_box.get_first_child()
        while child is not None:
            child.set_size_request(inner, -1)
            child.set_hexpand(False)
            self._constrain_side_labels(child, max(40, inner - 16))
            child = child.get_next_sibling()
        self.ingredients_box.queue_resize()

        if side_changed:
            self.side_clamp.set_maximum_size(side)
            self.side_clamp.set_tightening_threshold(side)
            self.side_clamp.set_size_request(side, -1)
            self.side_col.set_size_request(side, -1)
            self.side_clamp.queue_resize()
        return False

    def _set_hero(self, path: str) -> None:
        self._hero_path = None
        self._hero_pixbuf = None
        if path and Path(path).is_file():
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file(path)
                self._hero_path = Path(path)
                self._hero_pixbuf = pixbuf
            except Exception:
                pass
        self._layout_hero(self._side_width())

    def _layout_hero(self, side: int) -> None:
        if side <= 0:
            return
        tw, th = self._hero_size(side)
        if self._hero_pixbuf is not None:
            pixbuf = self._hero_pixbuf
            if pixbuf.get_width() <= 0 or pixbuf.get_height() <= 0:
                self._show_placeholder(side)
                return
            cropped = cover_crop_pixbuf(
                pixbuf, tw, th, bilinear=GdkPixbuf.InterpType.BILINEAR
            )
            if cropped is None:
                self._show_placeholder(side)
                return
            self.hero.set_paintable(Gdk.Texture.new_for_pixbuf(cropped))
            self.hero.set_size_request(tw, th)
            self.hero.set_hexpand(False)
            self.hero_stack.set_size_request(tw, th)
            self.hero_stack.set_hexpand(False)
            self.hero_stack.set_halign(Gtk.Align.FILL)
            self.hero_stack.set_visible_child_name("image")
            return
        self._show_placeholder(side)

    def _show_placeholder(self, side: int) -> None:
        self._hero_path = None
        self.hero.set_paintable(None)
        tw, th = self._hero_size(side)
        self.hero_placeholder.set_size_request(tw, th)
        self.hero_stack.set_size_request(tw, th)
        self.hero_stack.set_hexpand(False)
        self.hero_stack.set_halign(Gtk.Align.FILL)
        self.hero_stack.set_visible_child_name("placeholder")

    def _show_ai_prompt(self) -> None:
        recipe = self.recipe
        if recipe is None:
            return
        parent = self.get_root()
        dialog = Adw.Dialog()
        dialog.set_title("AI generated")
        dialog.set_content_width(520)
        dialog.set_content_height(440)

        header = Adw.HeaderBar()
        copy_btn = Gtk.Button(icon_name="edit-copy-symbolic")
        copy_btn.add_css_class("flat")
        copy_btn.set_tooltip_text("Copy prompt")
        copy_btn.set_sensitive(bool(recipe.ai_prompt.strip()))
        header.pack_end(copy_btn)

        explainer = Gtk.Label(
            label=(
                "This recipe was imported from an AI assistant. Pinch Bowl does not "
                "call an AI itself — it only saved the structured recipe you pasted. "
                "The prompt below is what was copied for that import (if available). "
                "AI recipes may be inaccurate; check ingredients, times, and "
                "techniques before cooking."
            ),
            xalign=0,
            wrap=True,
        )
        explainer.add_css_class("body")
        explainer.add_css_class("dim-label")
        explainer.set_margin_start(18)
        explainer.set_margin_end(18)
        explainer.set_margin_top(8)
        explainer.set_margin_bottom(8)

        prompt_text = recipe.ai_prompt.strip()
        if prompt_text:
            prompt_heading = Gtk.Label(label="Prompt used", xalign=0)
            prompt_heading.add_css_class("heading")
            prompt_heading.set_margin_start(18)
            prompt_heading.set_margin_end(18)
            prompt_heading.set_margin_bottom(6)

            view = Gtk.TextView()
            view.set_editable(False)
            view.set_cursor_visible(False)
            view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
            view.set_top_margin(10)
            view.set_bottom_margin(10)
            view.set_left_margin(12)
            view.set_right_margin(12)
            view.get_buffer().set_text(prompt_text)
            view.add_css_class("card")
            scroll = Gtk.ScrolledWindow()
            scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scroll.set_vexpand(True)
            scroll.set_margin_start(18)
            scroll.set_margin_end(18)
            scroll.set_margin_bottom(18)
            scroll.set_child(view)
        else:
            prompt_heading = Gtk.Label(
                label=(
                    "No prompt was saved for this recipe "
                    "(it may have been imported before prompts were stored)."
                ),
                xalign=0,
                wrap=True,
            )
            prompt_heading.add_css_class("dim-label")
            prompt_heading.set_margin_start(18)
            prompt_heading.set_margin_end(18)
            prompt_heading.set_margin_bottom(18)
            scroll = None

        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        column.append(header)
        column.append(explainer)
        column.append(prompt_heading)
        if scroll is not None:
            column.append(scroll)

        toaster = Adw.ToastOverlay(child=column)
        dialog.set_child(toaster)

        def on_copy(*_a: object) -> None:
            if not prompt_text:
                return
            copy_text(copy_btn, prompt_text)
            toaster.add_toast(Adw.Toast(title="Prompt copied"))

        copy_btn.connect("clicked", on_copy)
        dialog.present(parent if isinstance(parent, Gtk.Widget) else None)

    def _on_placeholder_clicked(self, *_args: object) -> None:
        if self.recipe is None or self.on_set_image is None:
            return
        parent = self.get_root()
        dialog = Adw.AlertDialog(
            heading="Add photo",
            body=(
                "Paste an image from the clipboard, or choose a file. "
                "If your AI showed sample photos, copy one and paste it here."
            ),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("paste", "Paste")
        dialog.add_response("choose", "Choose file…")
        dialog.set_default_response("paste")
        dialog.set_close_response("cancel")

        def on_response(_d: Adw.AlertDialog, response: str) -> None:
            if response == "paste":
                self._paste_hero_image()
            elif response == "choose":
                self._choose_hero_image()

        dialog.connect("response", on_response)
        dialog.present(parent if isinstance(parent, Gtk.Widget) else None)

    def _paste_hero_image(self) -> None:
        if self.on_set_image is None:
            return

        def _done(png: bytes | None) -> None:
            if not png:
                parent = self.get_root()
                toast_host = parent
                # Window exposes toaster via callback path; use a transient dialog toast.
                fail = Adw.AlertDialog(
                    heading="No image on clipboard",
                    body="Copy a photo first, then try Paste again.",
                )
                fail.add_response("ok", "OK")
                fail.set_default_response("ok")
                fail.set_close_response("ok")
                fail.present(toast_host if isinstance(toast_host, Gtk.Widget) else None)
                return
            from pinchbowl.images import save_image_bytes

            stem = self.recipe.title if self.recipe else "recipe"
            path = save_image_bytes(png, stem=stem)
            if not path:
                return
            if self.on_set_image:
                self.on_set_image(path)

        read_image_png(self, _done)

    def _choose_hero_image(self) -> None:
        if self.on_set_image is None:
            return
        parent = self.get_root()
        dialog = Gtk.FileDialog(title="Choose recipe photo")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        images = Gtk.FileFilter()
        images.set_name("Images")
        images.add_pixbuf_formats()
        filters.append(images)
        dialog.set_filters(filters)
        dialog.set_default_filter(images)

        def on_open(_d: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
            try:
                file = dialog.open_finish(result)
            except GLib.Error:
                return
            if file is None:
                return
            path = file.get_path()
            if not path:
                return
            from pinchbowl.images import import_image_file

            stem = self.recipe.title if self.recipe else "recipe"
            stored = import_image_file(path, stem=stem)
            if stored and self.on_set_image:
                self.on_set_image(stored)

        dialog.open(parent if isinstance(parent, Gtk.Window) else None, None, on_open)

    def _on_hero_clicked(self, *_args: object) -> None:
        if self._hero_path is None or not self._hero_path.is_file():
            return
        image_path = self._hero_path
        parent = self.get_root()
        dialog = Adw.Dialog()
        dialog.set_title(self.recipe.title if self.recipe else "Image")
        dialog.add_css_class("pb-image-dialog")
        if isinstance(parent, Gtk.Widget):
            width = parent.get_width()
            height = parent.get_height()
            if width > 240 and height > 240:
                dialog.set_content_width(width)
                dialog.set_content_height(height)
        dialog.set_follows_content_size(False)

        header = Adw.HeaderBar()
        copy_btn = Gtk.Button(icon_name="edit-copy-symbolic")
        copy_btn.add_css_class("flat")
        copy_btn.set_tooltip_text("Copy image")
        header.pack_end(copy_btn)

        picture = Gtk.Picture.new_for_filename(str(image_path))
        picture.set_can_shrink(True)
        picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        picture.set_hexpand(True)
        picture.set_vexpand(True)

        stage = Gtk.Box()
        stage.add_css_class("pb-image-stage")
        stage.set_hexpand(True)
        stage.set_vexpand(True)
        stage.append(picture)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(header)
        box.append(stage)
        toaster = Adw.ToastOverlay(child=box)
        dialog.set_child(toaster)

        def on_copy(*_a: object) -> None:
            if copy_image(copy_btn, image_path):
                toaster.add_toast(Adw.Toast(title="Image copied"))
            else:
                toaster.add_toast(Adw.Toast(title="Could not copy image"))

        copy_btn.connect("clicked", on_copy)
        dialog.present(parent if isinstance(parent, Gtk.Widget) else None)

    def apply_appearance(self) -> None:
        size = self.settings.font_size
        family = css_font_family(self.settings.recipe_font)
        self.body.set_margin_start(max(20, size + 10))
        self.body.set_margin_end(max(20, size + 10))
        self._font_css.load_from_string(
            f"""
            .pb-recipe-body {{
              font-size: {size}px;
              font-family: {family};
            }}
            .pb-recipe-title {{ font-size: {size + 14}px; font-weight: 700; }}
            .pb-title-edit {{
              min-width: 32px;
              min-height: 32px;
              padding: 0;
              color: @view_fg_color;
            }}
            .pb-title-edit image {{
              color: @view_fg_color;
              -gtk-icon-style: symbolic;
            }}
            .pb-favorite-on image {{
              color: @accent_color;
            }}
            button.flat.pb-meta-chip.pb-adjust-on {{
              background-color: alpha(@accent_bg_color, 0.12);
            }}
            button.flat.pb-meta-chip.pb-adjust-on image.pb-meta-chip-accent {{
              color: @accent_color;
            }}
            .pb-recipe-heading {{
              font-size: {size + 2}px;
              font-weight: 700;
              margin-top: 18px;
              margin-bottom: 9px;
            }}
            .pb-directions-heading,
            .pb-instructions .pb-recipe-heading {{
              margin-bottom: 19px;
            }}
            .pb-description {{
              margin-top: 25px;
              margin-bottom: 14px;
              font-weight: 700;
              line-height: 1.5;
            }}
            .pb-preamble-sep {{
              margin-top: 10px;
              margin-bottom: 10px;
            }}
            .pb-dir-step {{
              margin-bottom: 6px;
              line-height: 1.5;
            }}
            .pb-dir-step label {{
              line-height: 1.5;
            }}
            .pb-ingredients {{ font-size: {max(11, size - 1)}px; }}
            .pb-ingredient-line {{
              margin-bottom: 2px;
              line-height: 1.5;
            }}
            .pb-checkable {{
              border-radius: 8px;
              padding: 4px 8px;
              background-color: transparent;
            }}
            .pb-checkable.pb-checked {{
              background-color: alpha(@accent_bg_color, 0.10);
            }}
            .pb-meta {{
              margin-top: 10px;
              color: @view_fg_color;
            }}
            .pb-meta-part image {{
              color: @view_fg_color;
              -gtk-icon-style: symbolic;
            }}
            /* Hover highlight’s left edge lines up with the title. */
            button.flat.pb-meta-chip {{
              min-width: 0;
              min-height: 0;
              padding: 6px 12px;
              margin-left: 0;
              border-radius: 9px;
              font-weight: inherit;
            }}
            .pb-meta-chip label.pb-meta-chip-label {{
              font-weight: 700;
            }}
            .pb-meta-chip image.pb-meta-chip-accent {{
              color: @accent_color;
              -gtk-icon-style: symbolic;
            }}
            .pb-meta-time {{
              margin-left: 4px;
            }}
            """
        )
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(
                display,
                self._font_css,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 2,
            )
        self._sync_side_widths()
