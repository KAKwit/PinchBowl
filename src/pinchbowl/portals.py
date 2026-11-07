"""XDG Desktop Portal helpers for opening and sharing URIs.

Gtk.UriLauncher goes through the OpenURI portal when sandboxed. A dedicated
Share portal is not available yet; ``share_uri`` uses OpenURI with ``ask``
so the system app chooser can hand the link to another app.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from gi.repository import Gdk, Gio, GLib, Gtk


def open_uri(parent: Gtk.Window | None, uri: str) -> None:
    """Open ``uri`` in the default handler (OpenURI portal when sandboxed)."""
    launcher = Gtk.UriLauncher.new(uri)
    launcher.launch(parent, None, None, None)


def open_default_browser() -> bool:
    """Launch the default web browser without opening a specific URL.

    Returns whether a browser app was started. Does not navigate anywhere —
    useful when the user will use whatever AI (or site) they already prefer.
    """
    app_info = Gio.AppInfo.get_default_for_uri_scheme("https")
    if app_info is None:
        app_info = Gio.AppInfo.get_default_for_uri_scheme("http")
    if app_info is None:
        return False
    try:
        return bool(app_info.launch([], None))
    except GLib.Error:
        return False


def share_uri(parent: Gtk.Window | None, uri: str) -> None:
    """Offer ``uri`` via the OpenURI portal app chooser (ask=True)."""
    try:
        proxy = Gio.DBusProxy.new_for_bus_sync(
            Gio.BusType.SESSION,
            Gio.DBusProxyFlags.NONE,
            None,
            "org.freedesktop.portal.Desktop",
            "/org/freedesktop/portal/desktop",
            "org.freedesktop.portal.OpenURI",
            None,
        )
    except GLib.Error:
        open_uri(parent, uri)
        return

    # Empty parent_window is accepted; prefer ask so the user picks the app.
    try:
        proxy.call_sync(
            "OpenURI",
            GLib.Variant(
                "(ssa{sv})",
                ("", uri, {"ask": GLib.Variant("b", True)}),
            ),
            Gio.DBusCallFlags.NONE,
            -1,
            None,
        )
    except GLib.Error:
        open_uri(parent, uri)


def _clipboard_for(widget: Gtk.Widget) -> Gdk.Clipboard | None:
    display = widget.get_display()
    if display is None:
        display = Gdk.Display.get_default()
    if display is None:
        return None
    return display.get_clipboard()


def copy_text(widget: Gtk.Widget, text: str) -> None:
    """Copy ``text`` to the clipboard for the widget's display."""
    clipboard = _clipboard_for(widget)
    if clipboard is None:
        return
    clipboard.set(text)


def read_text(
    widget: Gtk.Widget,
    callback: Callable[[str | None], None],
) -> None:
    """Read text from the clipboard asynchronously; ``callback`` gets the string or None."""
    clipboard = _clipboard_for(widget)
    if clipboard is None:
        callback(None)
        return

    def _done(clip: Gdk.Clipboard, result: Gio.AsyncResult) -> None:
        try:
            text = clip.read_text_finish(result)
        except GLib.Error:
            callback(None)
            return
        callback(text)

    clipboard.read_text_async(None, _done)


def read_image_png(
    widget: Gtk.Widget,
    callback: Callable[[bytes | None], None],
) -> None:
    """Read an image from the clipboard as PNG bytes; ``callback`` gets bytes or None."""
    clipboard = _clipboard_for(widget)
    if clipboard is None:
        callback(None)
        return

    def _done(clip: Gdk.Clipboard, result: Gio.AsyncResult) -> None:
        try:
            texture = clip.read_texture_finish(result)
        except GLib.Error:
            callback(None)
            return
        if texture is None:
            callback(None)
            return
        try:
            png = texture.save_to_png_bytes()
        except GLib.Error:
            callback(None)
            return
        callback(bytes(png.get_data()) if png is not None else None)

    clipboard.read_texture_async(None, _done)


def copy_image(widget: Gtk.Widget, path: str | Path) -> bool:
    """Copy an image file to the clipboard as PNG. Returns whether it worked."""
    clipboard = _clipboard_for(widget)
    if clipboard is None:
        return False
    file_path = Path(path)
    if not file_path.is_file():
        return False
    try:
        texture = Gdk.Texture.new_from_filename(str(file_path))
        png = texture.save_to_png_bytes()
    except GLib.Error:
        return False
    provider = Gdk.ContentProvider.new_for_bytes("image/png", png)
    return bool(clipboard.set_content(provider))
