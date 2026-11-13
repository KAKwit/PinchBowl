"""Distraction-free cook mode: fullscreen-friendly steps + ingredients."""

from __future__ import annotations

import time
from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango

from pinchbowl.adjust import adjust_ingredients, scale_yield_text
from pinchbowl.directions import parse_instructions
from pinchbowl.duration import format_duration_text
from pinchbowl.fonts import css_font_family
from pinchbowl.ingredients import clean_ingredient_line, ingredient_line_markup
from pinchbowl.models import Recipe
from pinchbowl.schema_recipe import normalize_yield_text
from pinchbowl.settings import Settings
from pinchbowl.textutil import decode_entities
from pinchbowl.timers import (
    PRESET_MINUTES,
    CookTimer,
    TimerManager,
    format_remaining,
    start_timer_alarm,
    stop_timer_alarm,
)

# Step column max width; Clamp centers it beside the ingredients column.
_STEP_CLAMP = 840
_ING_MAX = 400
_ING_TIGHTEN = 320
# Collapse ingredients to an overlay below this width.
_COOK_NARROW_SP = 720
# Type ramp above the recipe preference.
_SIZE_BUMP = 7
_SCROLL_ANIM_MS = 220
_WHEEL_COOLDOWN_S = 0.12
_WHEEL_STEP_THRESHOLD = 1.0


class CookView(Gtk.Box):
    def __init__(self, settings: Settings) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.settings = settings
        self.recipe: Recipe | None = None
        self.on_exit: Callable[[], None] | None = None
        self._adjust_factor = 1.0
        self._adjust_units = "original"
        self._steps: list[Gtk.Widget] = []
        self._current = 0
        self._scroll_acc = 0.0
        self._wheel_last_step_at = 0.0
        self._scroll_anim: Adw.TimedAnimation | None = None
        self._scroll_anim_target: Adw.AnimationTarget | None = None
        self._scroll_retries = 0
        self._font_css = Gtk.CssProvider()
        self._font_css_attached = False
        self._timers = TimerManager()
        self._timers.on_changed = self._rebuild_timer_popover
        self._timers.on_tick = self._update_timer_labels
        self._timers.on_finished = self._on_timer_finished
        self._timer_remaining_labels: dict[str, Gtk.Label] = {}
        self._show_add_panel = True

        self.add_css_class("pb-cook-view")
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_focusable(True)

        # Thin top strip: title, progress, Done.
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        top.add_css_class("pb-cook-top")
        top.set_hexpand(True)

        self.title_label = Gtk.Label(xalign=0)
        self.title_label.add_css_class("pb-cook-title")
        self.title_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.title_label.set_hexpand(True)
        self.title_label.set_halign(Gtk.Align.START)

        self.progress = Gtk.Label(xalign=1)
        self.progress.add_css_class("pb-cook-progress")
        self.progress.add_css_class("dim-label")

        self.theme_btn = Gtk.Button()
        self.theme_btn.add_css_class("flat")
        self.theme_btn.add_css_class("circular")
        self.theme_btn.set_valign(Gtk.Align.CENTER)
        self._theme_icon = Gtk.Image()
        self._theme_icon.set_pixel_size(16)
        self.theme_btn.set_child(self._theme_icon)
        self.theme_btn.connect("clicked", self._on_theme_clicked)

        self.timer_btn = Gtk.MenuButton()
        self.timer_btn.add_css_class("flat")
        self.timer_btn.add_css_class("circular")
        self.timer_btn.set_valign(Gtk.Align.CENTER)
        self._timer_icon = Gtk.Image.new_from_icon_name("alarm-symbolic")
        self._timer_icon.set_pixel_size(16)
        self.timer_btn.set_child(self._timer_icon)
        self.timer_btn.set_tooltip_text("Timers")
        self.timer_btn.set_direction(Gtk.ArrowType.DOWN)
        self._timer_popover = Gtk.Popover()
        self._timer_popover.add_css_class("pb-cook-timer-popover")
        self._timer_popover_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=8
        )
        self._timer_popover_box.set_margin_top(10)
        self._timer_popover_box.set_margin_bottom(10)
        self._timer_popover_box.set_margin_start(12)
        self._timer_popover_box.set_margin_end(12)
        self._timer_popover.set_child(self._timer_popover_box)
        self.timer_btn.set_popover(self._timer_popover)
        self.timer_btn.connect("notify::active", self._on_timer_btn_active)

        done = Gtk.Button(label="Done")
        done.add_css_class("pill")
        done.add_css_class("suggested-action")
        done.set_tooltip_text("Leave cook mode (Escape)")
        done.connect("clicked", lambda *_: self.request_exit())

        top.append(self.title_label)
        top.append(self.progress)
        top.append(self.theme_btn)
        top.append(self.timer_btn)
        top.append(done)
        self.append(top)
        self._sync_theme_button()
        self._rebuild_timer_popover()

        self.meta = Gtk.Label(xalign=0)
        self.meta.add_css_class("pb-cook-meta")
        self.meta.add_css_class("dim-label")
        self.meta.set_ellipsize(Pango.EllipsizeMode.END)
        self.meta.set_margin_start(28)
        self.meta.set_margin_end(28)
        self.meta.set_margin_bottom(4)
        self.append(self.meta)

        # Directions column — Clamp centers the measure in the space beside
        # ingredients (wide max, balanced side margins).
        self.steps_scroll = Gtk.ScrolledWindow()
        self.steps_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.steps_scroll.set_hexpand(True)
        self.steps_scroll.set_vexpand(True)
        self.steps_scroll.set_propagate_natural_height(False)

        steps_clamp = Adw.Clamp()
        steps_clamp.set_maximum_size(_STEP_CLAMP)
        steps_clamp.set_tightening_threshold(_STEP_CLAMP - 120)
        steps_clamp.set_hexpand(True)

        self.steps_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.steps_box.add_css_class("pb-cook-steps")
        self.steps_box.set_valign(Gtk.Align.START)
        self.steps_box.set_margin_top(12)
        self.steps_box.set_margin_bottom(48)
        self.steps_box.set_margin_start(12)
        self.steps_box.set_margin_end(12)
        steps_clamp.set_child(self.steps_box)
        self.steps_scroll.set_child(steps_clamp)

        scroll_ctl = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL
        )
        scroll_ctl.connect("scroll", self._on_steps_scroll)
        self.steps_scroll.add_controller(scroll_ctl)

        # Ingredients column (sidebar of OverlaySplitView; overlays when narrow)
        ing_outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        ing_outer.add_css_class("pb-cook-ing-col")
        ing_outer.set_hexpand(True)
        ing_outer.set_vexpand(True)

        ing_heading = Gtk.Label(label="Ingredients", xalign=0)
        ing_heading.add_css_class("pb-cook-ing-heading")
        ing_heading.set_margin_start(20)
        ing_heading.set_margin_end(20)
        ing_heading.set_margin_top(16)
        ing_heading.set_margin_bottom(8)
        ing_outer.append(ing_heading)

        self.ing_scroll = Gtk.ScrolledWindow()
        self.ing_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.ing_scroll.set_vexpand(True)
        self.ing_scroll.set_hexpand(True)

        self.ingredients_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.ingredients_box.add_css_class("pb-cook-ingredients")
        self.ingredients_box.set_margin_start(16)
        self.ingredients_box.set_margin_end(16)
        self.ingredients_box.set_margin_bottom(32)
        self.ing_scroll.set_child(self.ingredients_box)
        ing_outer.append(self.ing_scroll)

        ing_clamp = Adw.Clamp()
        ing_clamp.set_maximum_size(_ING_MAX)
        ing_clamp.set_tightening_threshold(_ING_TIGHTEN)
        ing_clamp.set_child(ing_outer)
        ing_clamp.set_hexpand(True)

        self.body_split = Adw.OverlaySplitView()
        self.body_split.set_sidebar_position(Gtk.PackType.END)
        self.body_split.set_min_sidebar_width(240)
        self.body_split.set_max_sidebar_width(float(_ING_MAX))
        self.body_split.set_sidebar_width_fraction(0.38)
        self.body_split.set_sidebar_width_unit(Adw.LengthUnit.SP)
        self.body_split.set_content(self.steps_scroll)
        self.body_split.set_sidebar(ing_clamp)
        self.body_split.set_hexpand(True)
        self.body_split.set_vexpand(True)
        self.body_split.add_css_class("pb-cook-body")
        self.body_split.connect("notify::collapsed", self._on_cook_split_collapsed)

        self.ing_toggle = Gtk.ToggleButton()
        self.ing_toggle.add_css_class("flat")
        self.ing_toggle.add_css_class("circular")
        self.ing_toggle.set_valign(Gtk.Align.CENTER)
        self.ing_toggle.set_icon_name("view-list-bullet-symbolic")
        self.ing_toggle.set_tooltip_text("Ingredients")
        self.ing_toggle.set_visible(False)
        self.ing_toggle.connect("toggled", self._on_ing_toggle)
        # Insert before Done (last child of top).
        top.insert_child_after(self.ing_toggle, self.timer_btn)

        cook_bin = Adw.BreakpointBin()
        cook_bin.set_size_request(320, 320)
        cook_bin.set_hexpand(True)
        cook_bin.set_vexpand(True)
        cook_bin.set_child(self.body_split)
        cook_bp = Adw.Breakpoint.new(
            Adw.BreakpointCondition.parse(f"max-width: {_COOK_NARROW_SP}sp")
        )
        cook_bp.connect(
            "apply", lambda *_: self.body_split.set_collapsed(True)
        )
        cook_bp.connect(
            "unapply", lambda *_: self.body_split.set_collapsed(False)
        )
        cook_bin.add_breakpoint(cook_bp)
        self.append(cook_bin)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key_pressed)
        self.add_controller(keys)

        self.apply_appearance()
        self._on_cook_split_collapsed()

    def _on_cook_split_collapsed(self, *_args: object) -> None:
        collapsed = self.body_split.get_collapsed()
        self.ing_toggle.set_visible(collapsed)
        if not collapsed:
            self.body_split.set_show_sidebar(True)
            self.ing_toggle.set_active(False)
        else:
            showing = self.body_split.get_show_sidebar()
            if self.ing_toggle.get_active() != showing:
                self.ing_toggle.set_active(showing)

    def _on_ing_toggle(self, btn: Gtk.ToggleButton) -> None:
        if not self.body_split.get_collapsed():
            return
        self.body_split.set_show_sidebar(btn.get_active())

    def request_exit(self) -> None:
        n = self._timers.count
        if n == 0:
            self._confirm_exit()
            return
        noun = "timer" if n == 1 else "timers"
        dialog = Adw.AlertDialog(
            heading="Leave cook mode?",
            body=f"{n} active {noun} will be cleared.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("leave", "Leave")
        dialog.set_response_appearance("leave", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_exit_response)
        root = self.get_root()
        dialog.present(root if root is not None else self)

    def _on_exit_response(self, _dialog: Adw.AlertDialog, response: str) -> None:
        if response == "leave":
            self._confirm_exit()

    def _confirm_exit(self) -> None:
        if self.on_exit:
            self.on_exit()

    def clear_timers(self) -> None:
        stop_timer_alarm()
        self._timers.clear()

    def _cook_scheme(self) -> str:
        scheme = self.settings.cook_color_scheme
        return scheme if scheme in {"light", "dark"} else "dark"

    def _sync_theme_button(self) -> None:
        if self._cook_scheme() == "dark":
            self._theme_icon.set_from_icon_name("weather-clear-symbolic")
            self.theme_btn.set_tooltip_text("Use light appearance")
        else:
            self._theme_icon.set_from_icon_name("weather-clear-night-symbolic")
            self.theme_btn.set_tooltip_text("Use dark appearance")

    def _on_theme_clicked(self, *_args: object) -> None:
        nxt = "light" if self._cook_scheme() == "dark" else "dark"
        self.settings.update(cook_color_scheme=nxt)
        self._sync_theme_button()

    def _on_timer_btn_active(self, button: Gtk.MenuButton, *_args: object) -> None:
        if button.get_active():
            # Opening: show list when timers exist, else add panel.
            self._show_add_panel = self._timers.count == 0
            self._rebuild_timer_popover()

    def _clear_timer_popover(self) -> None:
        while (child := self._timer_popover_box.get_first_child()) is not None:
            self._timer_popover_box.remove(child)
        self._timer_remaining_labels.clear()

    def _rebuild_timer_popover(self) -> None:
        self._clear_timer_popover()
        box = self._timer_popover_box
        active = self._timers.timers

        if active and not self._show_add_panel:
            heading = Gtk.Label(label="Timers", xalign=0)
            heading.add_css_class("heading")
            box.append(heading)
            for timer in active:
                box.append(self._make_timer_row(timer))
            add_label = Gtk.Label(label="Add timer")
            add_label.set_halign(Gtk.Align.CENTER)
            add_label.set_hexpand(True)
            add_btn = Gtk.Button()
            add_btn.set_child(add_label)
            add_btn.add_css_class("flat")
            add_btn.set_hexpand(True)
            add_btn.set_halign(Gtk.Align.FILL)
            add_btn.connect("clicked", self._on_show_add_panel)
            box.append(add_btn)
            self._sync_timer_button()
            return

        if active:
            heading = Gtk.Label(label="Timers", xalign=0)
            heading.add_css_class("heading")
            box.append(heading)
            for timer in active:
                box.append(self._make_timer_row(timer))
            sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
            sep.set_margin_top(4)
            sep.set_margin_bottom(4)
            box.append(sep)

        add_heading = Gtk.Label(label="Add timer", xalign=0)
        add_heading.add_css_class("heading")
        box.append(add_heading)

        presets = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        for minutes in PRESET_MINUTES:
            btn = Gtk.Button(label=f"{minutes} min")
            btn.add_css_class("pill")
            btn.connect(
                "clicked",
                lambda _b, m=minutes: self._start_timer_minutes(m),
            )
            presets.append(btn)
        box.append(presets)

        custom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        custom.set_margin_top(4)
        adj = Gtk.Adjustment(
            value=3, lower=1, upper=180, step_increment=1, page_increment=5
        )
        spin = Gtk.SpinButton(adjustment=adj, climb_rate=1, digits=0)
        spin.set_valign(Gtk.Align.CENTER)
        spin.set_width_chars(4)
        start = Gtk.Button(label="Start")
        start.add_css_class("suggested-action")
        start.add_css_class("pill")
        start.connect(
            "clicked",
            lambda *_: self._start_timer_minutes(int(spin.get_value())),
        )
        custom.append(spin)
        mins_label = Gtk.Label(label="min")
        mins_label.add_css_class("dim-label")
        custom.append(mins_label)
        custom.append(start)
        box.append(custom)

        if active:
            back = Gtk.Button(label="Back to timers")
            back.add_css_class("flat")
            back.set_halign(Gtk.Align.START)
            back.set_margin_top(4)
            back.connect("clicked", self._on_hide_add_panel)
            box.append(back)

        self._sync_timer_button()

    def _make_timer_row(self, timer: CookTimer) -> Gtk.Box:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.set_hexpand(True)

        name = Gtk.Label(label=timer.label, xalign=0)
        name.set_hexpand(True)
        name.set_ellipsize(Pango.EllipsizeMode.END)
        name.set_halign(Gtk.Align.START)

        remaining = Gtk.Label(label=format_remaining(timer.remaining_seconds), xalign=1)
        remaining.add_css_class("numeric")
        remaining.set_width_chars(6)
        self._timer_remaining_labels[timer.id] = remaining

        cancel = Gtk.Button()
        cancel.add_css_class("flat")
        cancel.add_css_class("circular")
        cancel.set_icon_name("window-close-symbolic")
        cancel.set_tooltip_text("Cancel timer")
        cancel.connect(
            "clicked",
            lambda *_args, tid=timer.id: self._timers.cancel(tid),
        )

        row.append(name)
        row.append(remaining)
        row.append(cancel)
        return row

    def _update_timer_labels(self) -> None:
        for timer in self._timers.timers:
            label = self._timer_remaining_labels.get(timer.id)
            if label is not None:
                label.set_text(format_remaining(timer.remaining_seconds))
        self._sync_timer_button()

    def _sync_timer_button(self) -> None:
        n = self._timers.count
        if n:
            self.timer_btn.add_css_class("pb-cook-timer-active")
            self.timer_btn.set_tooltip_text(
                f"{n} active timer" + ("" if n == 1 else "s")
            )
        else:
            self.timer_btn.remove_css_class("pb-cook-timer-active")
            self.timer_btn.set_tooltip_text("Timers")

    def _on_show_add_panel(self, *_args: object) -> None:
        self._show_add_panel = True
        self._rebuild_timer_popover()

    def _on_hide_add_panel(self, *_args: object) -> None:
        self._show_add_panel = False
        self._rebuild_timer_popover()

    def _start_timer_minutes(self, minutes: int) -> None:
        seconds = max(1, int(minutes)) * 60
        self._show_add_panel = False
        self._timers.add(seconds)

    def _on_timer_finished(self, timer: CookTimer) -> None:
        start_timer_alarm()
        recipe_title = (
            decode_entities(self.recipe.title) if self.recipe is not None else "Recipe"
        )
        body = f"{timer.label} · {recipe_title}"
        notif_id = f"timer-{timer.id}"

        app = self._application()
        if app is not None:
            notification = Gio.Notification.new("Timer finished")
            notification.set_body(body)
            notification.set_priority(Gio.NotificationPriority.URGENT)
            try:
                notification.set_icon(Gio.ThemedIcon.new("alarm-symbolic"))
            except Exception:
                pass
            # Clicking the shell notification also silences the alarm.
            notification.set_default_action("app.stop-timer-alarm")
            app.send_notification(notif_id, notification)

        root = self.get_root()
        dialog = Adw.AlertDialog(
            heading="Timer finished",
            body=body,
        )
        dialog.add_response("ok", "OK")
        dialog.set_default_response("ok")
        dialog.set_close_response("ok")
        dialog.connect(
            "response",
            lambda *_: self._on_timer_alert_dismissed(notif_id),
        )
        if root is not None:
            dialog.present(root)
        else:
            dialog.present()

    def _on_timer_alert_dismissed(self, notification_id: str) -> None:
        stop_timer_alarm()
        app = self._application()
        if app is not None:
            app.withdraw_notification(notification_id)

    def _application(self) -> Adw.Application | None:
        root = self.get_root()
        if root is None:
            return None
        app = root.get_application()
        return app if isinstance(app, Adw.Application) else None

    def load_recipe(
        self,
        recipe: Recipe,
        *,
        adjust_factor: float = 1.0,
        adjust_units: str = "original",
    ) -> None:
        self.recipe = recipe
        self._adjust_factor = adjust_factor
        self._adjust_units = (
            adjust_units if adjust_units in {"original", "metric", "us"} else "original"
        )
        self.clear_timers()
        self._show_add_panel = True
        self.title_label.set_text(decode_entities(recipe.title))
        self.meta.set_text(self._meta_line(recipe))
        self.meta.set_visible(bool(self.meta.get_text()))
        self._render_steps(recipe.instructions)
        self._render_ingredients(recipe.ingredients)
        self.apply_appearance()
        self._sync_theme_button()
        self._set_current(0, scroll=True)
        self.grab_focus()

    def _meta_line(self, recipe: Recipe) -> str:
        parts: list[str] = []
        raw_yield = decode_entities(recipe.yield_text)
        scaled = scale_yield_text(raw_yield, self._adjust_factor)
        shown_yield = normalize_yield_text(scaled) if scaled else ""
        if shown_yield:
            parts.append(shown_yield)
        for value in (recipe.total_time, recipe.cook_time, recipe.prep_time):
            shown = format_duration_text(value)
            if shown:
                parts.append(shown)
                break
        return " · ".join(parts)

    def _clear_box(self, box: Gtk.Box) -> None:
        while (child := box.get_first_child()) is not None:
            box.remove(child)

    def _render_steps(self, text: str) -> None:
        self._clear_box(self.steps_box)
        self._steps = []
        parsed, max_digits = parse_instructions(text)
        num_chars = max_digits + 1

        if not parsed:
            empty = Gtk.Label(label="No directions for this recipe.", xalign=0)
            empty.add_css_class("dim-label")
            self.steps_box.append(empty)
            self._update_progress()
            return

        for item in parsed:
            kind = item[0]
            if kind == "heading":
                heading = Gtk.Label(label=item[1], xalign=0)
                heading.add_css_class("pb-cook-heading")
                heading.set_wrap(True)
                heading.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
                self.steps_box.append(heading)
                continue

            if kind == "plain":
                row = self._make_step_row(None, item[1], num_chars)
            else:
                row = self._make_step_row(item[1], item[2], num_chars)
            self.steps_box.append(row)
            self._steps.append(row)

        self._update_progress()

    def _make_step_row(
        self, marker: str | None, body_text: str, num_chars: int
    ) -> Gtk.Box:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        row.add_css_class("pb-cook-step")
        row.set_hexpand(True)
        row.set_focusable(True)

        if marker is not None:
            num = Gtk.Label(xalign=1.0, label=marker)
            num.add_css_class("pb-cook-step-num")
            num.set_width_chars(num_chars)
            num.set_max_width_chars(num_chars)
            num.set_halign(Gtk.Align.END)
            num.set_valign(Gtk.Align.START)
            row.append(num)

        body = Gtk.Label(xalign=0, label=body_text)
        body.add_css_class("pb-cook-step-body")
        body.set_wrap(True)
        body.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        body.set_hexpand(True)
        body.set_halign(Gtk.Align.FILL)
        body.set_valign(Gtk.Align.START)
        row.append(body)

        click = Gtk.GestureClick()
        click.set_button(1)
        click.connect("released", self._on_step_clicked, row)
        row.add_controller(click)
        row.set_cursor(Gdk.Cursor.new_from_name("pointer"))
        return row

    def _on_step_clicked(
        self,
        _gesture: Gtk.GestureClick,
        _n_press: int,
        _x: float,
        _y: float,
        row: Gtk.Widget,
    ) -> None:
        try:
            index = self._steps.index(row)
        except ValueError:
            return
        self._set_current(index, scroll=True)

    def _render_ingredients(self, text: str) -> None:
        self._clear_box(self.ingredients_box)
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
            label.set_max_width_chars(42)
            if cleaned.startswith("#"):
                label.add_css_class("pb-cook-heading")
                self.ingredients_box.append(label)
                continue
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            row.add_css_class("pb-cook-ingredient")
            row.add_css_class("pb-checkable")
            row.set_hexpand(True)
            row.append(label)
            row.set_cursor(Gdk.Cursor.new_from_name("pointer"))
            click = Gtk.GestureClick()
            click.set_button(1)
            click.connect("released", self._on_ingredient_released, row)
            row.add_controller(click)
            self.ingredients_box.append(row)

    def _clear_checked_ingredients(
        self, *, except_widget: Gtk.Widget | None = None
    ) -> None:
        # Exclusive highlight (same as recipe detail) — not a multi-tick list.
        child = self.ingredients_box.get_first_child()
        while child is not None:
            if child is not except_widget and child.has_css_class("pb-checked"):
                child.remove_css_class("pb-checked")
            child = child.get_next_sibling()

    def _on_ingredient_released(
        self,
        _gesture: Gtk.GestureClick,
        _n_press: int,
        _x: float,
        _y: float,
        widget: Gtk.Widget,
    ) -> None:
        if widget.has_css_class("pb-checked"):
            widget.remove_css_class("pb-checked")
            return
        self._clear_checked_ingredients(except_widget=widget)
        widget.add_css_class("pb-checked")

    def _set_current(self, index: int, *, scroll: bool) -> None:
        if not self._steps:
            self._current = 0
            self._update_progress()
            return
        index = max(0, min(index, len(self._steps) - 1))
        self._current = index
        for i, step in enumerate(self._steps):
            if i == index:
                step.remove_css_class("pb-cook-dim")
                step.add_css_class("pb-cook-current")
            else:
                step.remove_css_class("pb-cook-current")
                step.add_css_class("pb-cook-dim")
        self._update_progress()
        if scroll:
            self._scroll_retries = 0
            # Wait a tick so allocations are current after class changes.
            GLib.idle_add(self._scroll_current_into_view)

    def _update_progress(self) -> None:
        n = len(self._steps)
        if n == 0:
            self.progress.set_text("")
            return
        self.progress.set_text(f"{self._current + 1} / {n}")

    def _stop_scroll_anim(self) -> None:
        if self._scroll_anim is None:
            return
        if self._scroll_anim.get_state() == Adw.AnimationState.PLAYING:
            self._scroll_anim.skip()
        self._scroll_anim = None

    def _animate_scroll_to(self, target: float) -> None:
        vadj = self.steps_scroll.get_vadjustment()
        page = vadj.get_page_size()
        upper = max(0.0, vadj.get_upper() - page)
        target = max(0.0, min(target, upper))
        start = vadj.get_value()
        if abs(start - target) < 1.0:
            vadj.set_value(target)
            return

        self._stop_scroll_anim()

        def _set_value(value: float, *_args: object) -> None:
            vadj.set_value(value)

        target_obj = Adw.CallbackAnimationTarget.new(_set_value)
        anim = Adw.TimedAnimation.new(
            self.steps_scroll,
            start,
            target,
            _SCROLL_ANIM_MS,
            target_obj,
        )
        anim.set_easing(Adw.Easing.EASE_OUT_CUBIC)
        self._scroll_anim_target = target_obj
        self._scroll_anim = anim
        anim.play()

    def _scroll_current_into_view(self) -> bool:
        """Nudge the viewport so the current step stays on screen.

        Uses coordinates relative to the scrolled window (visible area), then
        applies that delta to the adjustment — more reliable than comparing
        content Y to adjustment value across Clamp/Viewport.
        """
        if not self._steps:
            return False

        # Settle any in-flight tween so translate_coordinates matches value.
        self._stop_scroll_anim()

        widget = self._steps[self._current]
        vadj = self.steps_scroll.get_vadjustment()
        height = widget.get_height()
        page = vadj.get_page_size() or float(self.steps_scroll.get_height())
        if height <= 0 or page <= 1:
            self._scroll_retries += 1
            return self._scroll_retries < 12

        # PyGObject strips the C boolean: success → (x, y), failure → None.
        coords = widget.translate_coordinates(self.steps_scroll, 0, 0)
        if coords is None:
            self._scroll_retries += 1
            return self._scroll_retries < 12

        self._scroll_retries = 0
        _x, y_view = coords
        margin = min(72.0, page * 0.12)

        delta = 0.0
        if height >= page - 2 * margin:
            # Tall step: pin its top into the padded band.
            delta = y_view - margin
        elif y_view < margin:
            delta = y_view - margin
        elif y_view + height > page - margin:
            delta = (y_view + height) - (page - margin)

        if abs(delta) > 1.0:
            self._animate_scroll_to(vadj.get_value() + delta)
        return False

    def _move_step(self, delta: int) -> None:
        if not self._steps:
            return
        self._set_current(self._current + delta, scroll=True)

    def _on_steps_scroll(
        self, _controller: Gtk.EventControllerScroll, _dx: float, dy: float
    ) -> bool:
        """Wheel/trackpad advances one step; never free-scrolls the list."""
        if not self._steps or dy == 0:
            return True

        now = time.monotonic()
        if now - self._wheel_last_step_at < _WHEEL_COOLDOWN_S:
            # Swallow burst/momentum so we don't skip steps or fight kinetic scroll.
            self._scroll_acc = 0.0
            return True

        self._scroll_acc += dy
        if abs(self._scroll_acc) < _WHEEL_STEP_THRESHOLD:
            return True

        direction = 1 if self._scroll_acc > 0 else -1
        self._scroll_acc = 0.0
        self._wheel_last_step_at = now
        self._move_step(direction)
        return True

    def on_key_pressed(
        self,
        _controller: Gtk.EventControllerKey,
        keyval: int,
        _keycode: int,
        _state: Gdk.ModifierType,
    ) -> bool:
        if keyval in (Gdk.KEY_Escape,):
            self.request_exit()
            return True
        if keyval in (Gdk.KEY_Up, Gdk.KEY_Left, Gdk.KEY_Page_Up, Gdk.KEY_KP_Up):
            self._move_step(-1)
            return True
        if keyval in (
            Gdk.KEY_Down,
            Gdk.KEY_Right,
            Gdk.KEY_Page_Down,
            Gdk.KEY_KP_Down,
            Gdk.KEY_space,
        ):
            self._move_step(1)
            return True
        if keyval in (Gdk.KEY_Home, Gdk.KEY_KP_Home):
            self._set_current(0, scroll=True)
            return True
        if keyval in (Gdk.KEY_End, Gdk.KEY_KP_End):
            self._set_current(len(self._steps) - 1, scroll=True)
            return True
        return False

    def apply_appearance(self) -> None:
        size = self.settings.font_size + _SIZE_BUMP
        family = css_font_family(self.settings.recipe_font)
        self._font_css.load_from_string(
            f"""
            .pb-cook-view {{
              background-color: @window_bg_color;
              color: @window_fg_color;
              font-family: {family};
            }}
            .pb-cook-top {{
              padding: 14px 20px 10px 20px;
              min-height: 48px;
            }}
            .pb-cook-timer-active {{
              color: @accent_color;
            }}
            .pb-cook-title {{
              font-size: {size + 4}px;
              font-weight: 700;
            }}
            .pb-cook-progress {{
              font-size: {max(13, size - 4)}px;
              font-weight: 600;
            }}
            .pb-cook-meta {{
              font-size: {max(13, size - 5)}px;
            }}
            .pb-cook-heading {{
              font-size: {size + 2}px;
              font-weight: 700;
              margin-top: 22px;
              margin-bottom: 12px;
            }}
            .pb-cook-step {{
              border-radius: 14px;
              padding: 16px 18px;
              margin-bottom: 10px;
              transition: opacity 180ms ease;
            }}
            .pb-cook-step-body,
            .pb-cook-step-num {{
              font-size: {size}px;
              font-weight: 400;
              line-height: 1.4;
            }}
            .pb-cook-step-num {{
              font-weight: 600;
              opacity: 0.5;
            }}
            .pb-cook-step.pb-cook-dim {{
              opacity: 0.38;
            }}
            .pb-cook-step.pb-cook-current {{
              opacity: 1;
              background-color: alpha(@accent_bg_color, 0.14);
            }}
            .pb-cook-ing-col {{
              background-color: alpha(@view_fg_color, 0.04);
            }}
            .pb-cook-ing-heading {{
              font-size: {size - 1}px;
              font-weight: 700;
            }}
            .pb-cook-ingredients {{
              font-size: {max(14, size - 2)}px;
              line-height: 1.45;
            }}
            .pb-cook-ingredient {{
              border-radius: 10px;
              padding: 10px 12px;
              margin-bottom: 8px;
            }}
            .pb-cook-ingredient.pb-checkable.pb-checked {{
              background-color: alpha(@accent_bg_color, 0.12);
            }}
            """
        )
        display = Gdk.Display.get_default()
        if display is not None and not self._font_css_attached:
            Gtk.StyleContext.add_provider_for_display(
                display,
                self._font_css,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 3,
            )
            self._font_css_attached = True
        self._sync_theme_button()
