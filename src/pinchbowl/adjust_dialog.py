"""Ad-hoc Adjust dialog: scale servings and switch unit systems."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk

from pinchbowl.adjust import (
    SCALE_FACTORS,
    nearest_scale_factor,
    parse_servings,
    scale_label,
)


class AdjustDialog(Adw.PreferencesDialog):
    def __init__(self, recipe_view) -> None:
        super().__init__(title="Adjust")
        self.set_search_enabled(False)
        self._view = recipe_view
        self._applying = True

        recipe = recipe_view.recipe
        yield_text = recipe.yield_text if recipe is not None else ""
        self._base = parse_servings(yield_text)
        factor, units = recipe_view.adjust_state()

        page = Adw.PreferencesPage()
        scale_group = Adw.PreferencesGroup(title="Servings")

        if self._base is not None:
            lower = max(1, self._base // 2)
            upper = max(lower, self._base * 4)
            current = max(lower, min(upper, int(round(self._base * factor))))
            self._servings_row = Adw.SpinRow(
                title="Servings",
                subtitle=self._servings_subtitle(current),
                adjustment=Gtk.Adjustment(
                    value=current,
                    lower=lower,
                    upper=upper,
                    step_increment=1,
                    page_increment=1,
                ),
                digits=0,
            )
            self._servings_row.connect("notify::value", self._on_servings)
            scale_group.add(self._servings_row)
            self._scale_row = None
        else:
            self._servings_row = None
            labels = [scale_label(f) for f in SCALE_FACTORS]
            self._scale_row = Adw.ComboRow(
                title="Scale",
                subtitle="Half down, up to 4×",
            )
            self._scale_row.set_model(Gtk.StringList.new(labels))
            snapped = nearest_scale_factor(factor)
            self._scale_row.set_selected(SCALE_FACTORS.index(snapped))
            self._scale_row.connect("notify::selected", self._on_scale)
            scale_group.add(self._scale_row)

        page.add(scale_group)

        units_group = Adw.PreferencesGroup(title="Units")
        self._units_row = Adw.ComboRow(
            title="Units",
            subtitle="Converts mass and volume when possible",
        )
        self._units_row.set_model(Gtk.StringList.new(["Original", "Metric", "US"]))
        unit_index = {"original": 0, "metric": 1, "us": 2}.get(units, 0)
        self._units_row.set_selected(unit_index)
        self._units_row.connect("notify::selected", self._on_units)
        units_group.add(self._units_row)
        page.add(units_group)

        actions = Adw.PreferencesGroup()
        self._reset_row = Adw.ActionRow(
            title="Reset",
            subtitle="Restore original amounts and units",
        )
        reset_btn = Gtk.Button(label="Reset")
        reset_btn.add_css_class("destructive-action")
        reset_btn.set_valign(Gtk.Align.CENTER)
        reset_btn.connect("clicked", self._on_reset)
        self._reset_row.add_suffix(reset_btn)
        self._reset_row.set_activatable_widget(reset_btn)
        self._reset_btn = reset_btn
        actions.add(self._reset_row)
        page.add(actions)

        self.add(page)

        self._applying = False
        self._sync_reset_sensitive()

    def _current_factor(self) -> float:
        if self._servings_row is not None and self._base:
            return float(self._servings_row.get_value()) / float(self._base)
        if self._scale_row is not None:
            idx = self._scale_row.get_selected()
            if 0 <= idx < len(SCALE_FACTORS):
                return SCALE_FACTORS[idx]
        return 1.0

    def _current_units(self) -> str:
        return {0: "original", 1: "metric", 2: "us"}.get(
            self._units_row.get_selected(), "original"
        )

    def _servings_subtitle(self, servings: int) -> str:
        if not self._base:
            return ""
        if servings == self._base:
            return "Original recipe"
        return f"Originally {self._base}"

    def _push(self) -> None:
        if self._applying:
            return
        self._view.apply_adjust(self._current_factor(), self._current_units())
        self._sync_reset_sensitive()

    def _sync_reset_sensitive(self) -> None:
        factor = self._current_factor()
        units = self._current_units()
        active = abs(factor - 1.0) > 1e-9 or units != "original"
        self._reset_btn.set_sensitive(active)
        self._reset_row.set_sensitive(active)

    def _on_servings(self, row: Adw.SpinRow, *_args: object) -> None:
        servings = int(round(row.get_value()))
        row.set_subtitle(self._servings_subtitle(servings))
        self._push()

    def _on_scale(self, *_args: object) -> None:
        self._push()

    def _on_units(self, *_args: object) -> None:
        self._push()

    def _on_reset(self, *_args: object) -> None:
        self._applying = True
        if self._servings_row is not None and self._base is not None:
            self._servings_row.set_value(self._base)
            self._servings_row.set_subtitle(self._servings_subtitle(self._base))
        if self._scale_row is not None:
            self._scale_row.set_selected(SCALE_FACTORS.index(1.0))
        self._units_row.set_selected(0)
        self._applying = False
        self._view.reset_adjust()
        self._sync_reset_sensitive()
