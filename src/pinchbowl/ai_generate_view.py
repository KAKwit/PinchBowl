"""Guided AI recipe import: copy prompt, paste JSON, preview, add to library."""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, Gtk

from pinchbowl.ai_paste import (
    PasteParseError,
    PasteParseResult,
    build_ai_prompt,
    parse_ai_paste,
)
from pinchbowl.portals import copy_text, open_default_browser, read_text


class AiGenerateView(Gtk.Box):
    """Sidebar destination: help the user paste an AI-generated recipe."""

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add_css_class("pb-ai-generate-view")
        self.set_hexpand(True)
        self.set_vexpand(True)

        self.on_add: Callable[[dict], None] | None = None
        self.on_toast: Callable[[str], None] | None = None
        self._parsed: PasteParseResult | None = None
        self._preview_timeout: int | None = None
        self._prompt_dirty = False
        self._syncing_prompt = False
        self._compact = False

        heading = Gtk.Label(label="AI Generate", xalign=0)
        heading.add_css_class("heading")

        self.menu_btn = Gtk.MenuButton(icon_name="open-menu-symbolic")
        self.menu_btn.add_css_class("flat")

        self._header = Adw.HeaderBar()
        self._header.set_title_widget(heading)
        self._header.pack_end(self.menu_btn)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.set_hexpand(True)

        self._clamp = Adw.Clamp()
        self._clamp.set_maximum_size(640)
        self._clamp.set_tightening_threshold(400)
        self._clamp.set_margin_start(24)
        self._clamp.set_margin_end(24)
        self._clamp.set_margin_top(12)
        self._clamp.set_margin_bottom(28)
        clamp = self._clamp

        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        clamp.set_child(column)
        scroll.set_child(clamp)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(self._header)
        toolbar.set_content(scroll)
        self.append(toolbar)

        intro = Gtk.Label(
            label=(
                "Use any AI you like (browser, app, or assistant) to invent a recipe, "
                "then paste its reply here. Pinch Bowl never calls an AI itself — "
                "it only imports the structured JSON from the paste."
            ),
            xalign=0,
            wrap=True,
        )
        intro.add_css_class("body")
        intro.add_css_class("dim-label")
        column.append(intro)

        idea_group = Adw.PreferencesGroup()
        idea_group.set_title("1. Describe an idea")
        idea_group.set_description("Optional. Included when you copy the prompt.")
        self.idea_row = Adw.EntryRow(title="Idea")
        self.idea_row.set_input_hints(Gtk.InputHints.SPELLCHECK)
        self.idea_row.connect("changed", self._on_idea_changed)
        idea_group.add(self.idea_row)
        column.append(idea_group)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        actions.set_halign(Gtk.Align.START)

        self.copy_btn = Gtk.Button(label="Copy prompt")
        self.copy_btn.add_css_class("suggested-action")
        self.copy_btn.set_tooltip_text("Copy a ready-made prompt for your AI")
        self.copy_btn.connect("clicked", self._on_copy_prompt)
        actions.append(self.copy_btn)

        self.open_btn = Gtk.Button(label="Open browser")
        self.open_btn.set_tooltip_text(
            "Open your default web browser (does not open a specific site)"
        )
        self.open_btn.connect("clicked", self._on_open_browser)
        actions.append(self.open_btn)
        column.append(actions)

        prompt_expander = Gtk.Expander(label="Show prompt")
        prompt_expander.set_margin_top(4)
        self.prompt_view = Gtk.TextView()
        self.prompt_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.prompt_view.set_top_margin(8)
        self.prompt_view.set_bottom_margin(8)
        self.prompt_view.set_left_margin(10)
        self.prompt_view.set_right_margin(10)
        self.prompt_view.add_css_class("pb-ai-prompt")
        self.prompt_buf = self.prompt_view.get_buffer()
        self.prompt_buf.set_text(build_ai_prompt(""))
        self.prompt_buf.connect("changed", self._on_prompt_edited)
        prompt_scroll = Gtk.ScrolledWindow()
        prompt_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        prompt_scroll.set_min_content_height(120)
        prompt_scroll.set_max_content_height(200)
        prompt_scroll.set_child(self.prompt_view)
        prompt_card = _ai_text_card(prompt_scroll)
        prompt_card.set_margin_top(8)
        prompt_expander.set_child(prompt_card)
        column.append(prompt_expander)

        tips_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        tips_heading = Gtk.Label(label="Tips", xalign=0)
        tips_heading.add_css_class("heading")
        tips_box.append(tips_heading)
        for tip in (
            "Paste the Schema.org Recipe JSON from the AI reply — that’s all "
            "Pinch Bowl needs to import.",
            "Imported recipes won’t include a photo. If your AI shows sample "
            "images, copy one and add it on the recipe page (click Add photo).",
            "Any AI works: ChatGPT, Gemini, Claude, a local model, etc.",
        ):
            tip_label = Gtk.Label(label=f"• {tip}", xalign=0, wrap=True)
            tip_label.add_css_class("body")
            tip_label.add_css_class("dim-label")
            tips_box.append(tip_label)
        column.append(tips_box)

        paste_heading = Gtk.Label(label="2. Paste the result", xalign=0)
        paste_heading.add_css_class("heading")
        column.append(paste_heading)

        paste_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        paste_sub = Gtk.Label(
            label="Paste the Schema.org Recipe JSON (a fenced block from a longer reply is fine).",
            xalign=0,
            wrap=True,
        )
        paste_sub.add_css_class("dim-label")
        paste_sub.set_hexpand(True)
        paste_header.append(paste_sub)
        self.clipboard_btn = Gtk.Button(label="Paste from clipboard")
        self.clipboard_btn.add_css_class("flat")
        self.clipboard_btn.connect("clicked", self._on_paste_clipboard)
        paste_header.append(self.clipboard_btn)
        column.append(paste_header)

        self.paste_view = Gtk.TextView()
        self.paste_view.set_wrap_mode(Gtk.WrapMode.CHAR)
        self.paste_view.set_monospace(True)
        self.paste_view.set_top_margin(10)
        self.paste_view.set_bottom_margin(10)
        self.paste_view.set_left_margin(10)
        self.paste_view.set_right_margin(10)
        self.paste_view.set_accepts_tab(False)
        self.paste_view.add_css_class("pb-ai-paste")
        self.paste_buf = self.paste_view.get_buffer()
        self.paste_buf.connect("changed", self._on_paste_changed)

        paste_scroll = Gtk.ScrolledWindow()
        paste_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        paste_scroll.set_min_content_height(160)
        paste_scroll.set_max_content_height(280)
        paste_scroll.set_child(self.paste_view)
        column.append(_ai_text_card(paste_scroll))

        self.error_label = Gtk.Label(xalign=0, wrap=True)
        self.error_label.add_css_class("error")
        self.error_label.set_visible(False)
        column.append(self.error_label)

        preview_heading = Gtk.Label(label="3. Preview", xalign=0)
        preview_heading.add_css_class("heading")
        column.append(preview_heading)

        self.preview_title = Gtk.Label(xalign=0, wrap=True)
        self.preview_title.add_css_class("title-3")
        self.preview_meta = Gtk.Label(xalign=0, wrap=True)
        self.preview_meta.add_css_class("dim-label")
        self.preview_meta.add_css_class("caption")
        self.preview_ingredients = Gtk.Label(xalign=0, wrap=True)
        self.preview_ingredients.set_selectable(True)
        self.preview_instructions = Gtk.Label(xalign=0, wrap=True)
        self.preview_instructions.set_selectable(True)

        preview_pad = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        preview_pad.set_margin_start(12)
        preview_pad.set_margin_end(12)
        preview_pad.set_margin_top(10)
        preview_pad.set_margin_bottom(12)
        preview_pad.append(self.preview_title)
        preview_pad.append(self.preview_meta)
        ing_h = Gtk.Label(label="Ingredients", xalign=0)
        ing_h.add_css_class("heading")
        preview_pad.append(ing_h)
        preview_pad.append(self.preview_ingredients)
        dir_h = Gtk.Label(label="Directions", xalign=0)
        dir_h.add_css_class("heading")
        preview_pad.append(dir_h)
        preview_pad.append(self.preview_instructions)
        preview_frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        preview_frame.add_css_class("card")
        preview_frame.append(preview_pad)

        self.preview_empty = Gtk.Label(
            label="Paste JSON above to see a preview.",
            xalign=0,
            wrap=True,
        )
        self.preview_empty.add_css_class("dim-label")

        self.preview_stack = Gtk.Stack()
        # Don't reserve space for the full preview while showing the empty hint.
        self.preview_stack.set_hhomogeneous(False)
        self.preview_stack.set_vhomogeneous(False)
        self.preview_stack.add_named(self.preview_empty, "empty")
        self.preview_stack.add_named(preview_frame, "ready")
        self.preview_stack.set_visible_child_name("empty")
        column.append(self.preview_stack)

        safety = Gtk.Label(
            label=(
                "Generated recipes may be inaccurate or unsafe. "
                "Check times, temperatures, and techniques before cooking."
            ),
            xalign=0,
            wrap=True,
        )
        safety.add_css_class("caption")
        safety.add_css_class("dim-label")
        column.append(safety)

        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        footer.set_halign(Gtk.Align.END)

        self.clear_btn = Gtk.Button(label="Clear")
        self.clear_btn.add_css_class("flat")
        self.clear_btn.connect("clicked", self._on_clear)
        footer.append(self.clear_btn)

        self.add_btn = Gtk.Button(label="Add to library")
        self.add_btn.add_css_class("suggested-action")
        self.add_btn.set_sensitive(False)
        self.add_btn.connect("clicked", self._on_add)
        footer.append(self.add_btn)
        column.append(footer)

    def pack_nav_toggle(self, button: Gtk.Button) -> None:
        self._header.pack_start(button)

    def set_compact(self, compact: bool) -> None:
        if compact == self._compact:
            return
        self._compact = compact
        margin = 12 if compact else 24
        bottom = 16 if compact else 28
        self._clamp.set_margin_start(margin)
        self._clamp.set_margin_end(margin)
        self._clamp.set_margin_bottom(bottom)

    def set_menu_model(self, model: Gio.MenuModel) -> None:
        self.menu_btn.set_menu_model(model)

    def clear(self) -> None:
        """Reset idea, prompt, paste, and preview."""
        self._on_clear()

    def _idea_text(self) -> str:
        return self.idea_row.get_text().strip()

    def _paste_text(self) -> str:
        start = self.paste_buf.get_start_iter()
        end = self.paste_buf.get_end_iter()
        return self.paste_buf.get_text(start, end, False)

    def _prompt_text(self) -> str:
        start = self.prompt_buf.get_start_iter()
        end = self.prompt_buf.get_end_iter()
        return self.prompt_buf.get_text(start, end, False)

    def _set_prompt_text(self, text: str) -> None:
        self._syncing_prompt = True
        self.prompt_buf.set_text(text)
        self._syncing_prompt = False

    def _on_idea_changed(self, *_args: object) -> None:
        if not self._prompt_dirty:
            self._set_prompt_text(build_ai_prompt(self._idea_text()))

    def _on_prompt_edited(self, *_args: object) -> None:
        if self._syncing_prompt:
            return
        self._prompt_dirty = True

    def _on_copy_prompt(self, *_args: object) -> None:
        text = self._prompt_text().strip()
        if not text:
            text = build_ai_prompt(self._idea_text())
            self._set_prompt_text(text)
            self._prompt_dirty = False
        copy_text(self, text)
        if self.on_toast:
            self.on_toast("Prompt copied — paste it into your AI")

    def _on_open_browser(self, *_args: object) -> None:
        if open_default_browser():
            return
        if self.on_toast:
            self.on_toast("Could not open a web browser")

    def _on_paste_clipboard(self, *_args: object) -> None:
        def _done(text: str | None) -> None:
            if not text:
                if self.on_toast:
                    self.on_toast("Clipboard has no text")
                return
            self.paste_buf.set_text(text)

        read_text(self, _done)

    def _on_paste_changed(self, *_args: object) -> None:
        if self._preview_timeout is not None:
            GLib.source_remove(self._preview_timeout)
            self._preview_timeout = None
        self._preview_timeout = GLib.timeout_add(280, self._run_preview)

    def _run_preview(self) -> bool:
        self._preview_timeout = None
        self._try_parse(show_empty_error=False)
        return GLib.SOURCE_REMOVE

    def _try_parse(self, *, show_empty_error: bool) -> None:
        raw = self._paste_text().strip()
        if not raw:
            self._parsed = None
            self.add_btn.set_sensitive(False)
            self.preview_stack.set_visible_child_name("empty")
            self.error_label.set_visible(False)
            if show_empty_error:
                self._set_error("Paste the JSON from your browser AI first.")
            return
        try:
            result = parse_ai_paste(raw)
        except PasteParseError as exc:
            self._parsed = None
            self.add_btn.set_sensitive(False)
            self.preview_stack.set_visible_child_name("empty")
            self._set_error(str(exc))
            return

        self._parsed = result
        self.error_label.set_visible(False)
        self.add_btn.set_sensitive(True)
        recipe = result.recipe
        self.preview_title.set_text(str(recipe.get("title") or "Untitled"))
        bits = [result.format_name]
        yield_text = str(recipe.get("yield_text") or "").strip()
        if yield_text:
            bits.append(yield_text)
        for key in ("total_time", "cook_time", "prep_time"):
            val = str(recipe.get(key) or "").strip()
            if val:
                bits.append(val)
                break
        self.preview_meta.set_text(" · ".join(bits))
        ingredients = str(recipe.get("ingredients") or "").strip() or "(none)"
        instructions = str(recipe.get("instructions") or "").strip() or "(none)"
        self.preview_ingredients.set_text(ingredients)
        self.preview_instructions.set_text(instructions)
        self.preview_stack.set_visible_child_name("ready")

    def _set_error(self, message: str) -> None:
        self.error_label.set_text(message)
        self.error_label.set_visible(True)

    def _on_clear(self, *_args: object) -> None:
        if self._preview_timeout is not None:
            GLib.source_remove(self._preview_timeout)
            self._preview_timeout = None
        self.paste_buf.set_text("")
        self.idea_row.set_text("")
        self._prompt_dirty = False
        self._set_prompt_text(build_ai_prompt(""))
        self._parsed = None
        self.add_btn.set_sensitive(False)
        self.preview_stack.set_visible_child_name("empty")
        self.error_label.set_visible(False)

    def _on_add(self, *_args: object) -> None:
        self._try_parse(show_empty_error=True)
        if self._parsed is None or self.on_add is None:
            return
        data = dict(self._parsed.recipe)
        data["ai_prompt"] = self._prompt_text().strip()
        self.on_add(data)


def _ai_text_card(child: Gtk.Widget) -> Gtk.Widget:
    """Wrap a text field so content stays inside the card’s rounded corners."""
    card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    card.add_css_class("card")
    card.add_css_class("pb-ai-field")
    card.append(child)
    return card
