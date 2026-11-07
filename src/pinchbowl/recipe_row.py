from __future__ import annotations

from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")

from gi.repository import Gtk, Pango

from pinchbowl.images import request_list_thumb
from pinchbowl.models import Recipe
from pinchbowl.urls import source_display


def _meta_part(icon_name: str, text: str) -> Gtk.Widget:
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
    box.add_css_class("pb-row-meta-part")
    icon = Gtk.Image.new_from_icon_name(icon_name)
    icon.set_pixel_size(12)
    icon.set_valign(Gtk.Align.CENTER)
    label = Gtk.Label(label=text, xalign=0)
    label.add_css_class("caption")
    label.set_ellipsize(Pango.EllipsizeMode.END)
    label.set_valign(Gtk.Align.CENTER)
    box.append(icon)
    box.append(label)
    return box


class RecipeRow(Gtk.ListBoxRow):
    def __init__(self, recipe: Recipe) -> None:
        super().__init__()
        self.recipe_id = recipe.id
        # Cached for ListBox filter/sort without hitting SQLite again.
        self.favorite = recipe.favorite
        self.tags = list(recipe.tags)
        self.sort_title = recipe.title.casefold()
        self.created_at = recipe.created_at
        self.updated_at = recipe.updated_at
        self.sort_minutes = recipe.sort_minutes
        self._image_path = ""
        self.add_css_class("pb-recipe-row")

        root = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        root.set_margin_start(12)
        root.set_margin_end(12)
        root.set_margin_top(10)
        root.set_margin_bottom(10)

        self.thumb = Gtk.Picture()
        self.thumb.set_size_request(56, 56)
        self.thumb.set_content_fit(Gtk.ContentFit.COVER)
        self.thumb.set_can_shrink(True)
        self.thumb.set_hexpand(False)
        self.thumb.set_vexpand(False)
        self.thumb.add_css_class("pb-thumb")
        placeholder = Gtk.Box()
        placeholder.set_size_request(56, 56)
        placeholder.set_hexpand(False)
        placeholder.set_vexpand(False)
        placeholder.add_css_class("pb-thumb-placeholder")
        self._thumb_host = Gtk.Overlay()
        self._thumb_host.set_child(placeholder)
        self._thumb_host.add_overlay(self.thumb)
        self._thumb_host.set_size_request(56, 56)
        self._thumb_host.set_hexpand(False)
        self._thumb_host.set_vexpand(False)
        self._thumb_host.set_valign(Gtk.Align.CENTER)
        self._thumb_host.set_halign(Gtk.Align.START)
        self._thumb_host.set_overflow(Gtk.Overflow.HIDDEN)
        self._thumb_host.add_css_class("pb-thumb-host")
        root.append(self._thumb_host)
        self._set_image(recipe.image_path)

        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        col.set_hexpand(True)

        title = Gtk.Label(label=recipe.title, xalign=0)
        title.add_css_class("heading")
        title.set_ellipsize(Pango.EllipsizeMode.END)
        title.set_wrap(False)

        meta = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        meta.add_css_class("pb-row-meta")

        if recipe.time_label:
            meta.append(_meta_part("alarm-symbolic", recipe.time_label))

        if recipe.tags:
            meta.append(
                _meta_part("user-bookmarks-symbolic", ", ".join(recipe.tags))
            )

        label, is_web = source_display(recipe.source_url, recipe.source)
        if label:
            icon = (
                "insert-link-symbolic" if is_web else "x-office-address-book-symbolic"
            )
            meta.append(_meta_part(icon, label))

        col.append(title)
        if meta.get_first_child() is not None:
            col.append(meta)

        description = " ".join((recipe.description or "").split())
        if description:
            snippet = Gtk.Label(label=description, xalign=0)
            snippet.add_css_class("dim-label")
            snippet.add_css_class("caption")
            snippet.set_wrap(True)
            snippet.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            snippet.set_lines(2)
            snippet.set_ellipsize(Pango.EllipsizeMode.END)
            snippet.set_hexpand(True)
            snippet.set_halign(Gtk.Align.FILL)
            col.append(snippet)

        root.append(col)
        self.set_child(root)

    def _set_image(self, path: str) -> None:
        self._image_path = path or ""
        if not path:
            self.thumb.set_visible(False)
            return
        file_path = Path(path)
        if not file_path.is_file():
            self.thumb.set_visible(False)
            return
        # Placeholder until a cached/disk thumb is ready; idle generate if cold.
        self.thumb.set_visible(False)
        request_list_thumb(path, self._on_thumb_ready)

    def _on_thumb_ready(self, path: str, texture: object | None) -> None:
        if path != self._image_path or texture is None:
            return
        self.thumb.set_paintable(texture)
        self.thumb.set_visible(True)
