"""Narrow GLib log filters for known-benign platform noise."""

from __future__ import annotations

import ctypes

from gi.repository import GLib

# PyGObject keeps only a weak ref to the writer callback; pin it.
_KEEPALIVE: list[object] = []


def _field_text(field: GLib.LogField) -> str:
    """Decode a structured log field value to text."""
    if field.length == -1:
        raw = ctypes.cast(field.value, ctypes.c_char_p).value
        if not raw:
            return ""
        return raw.decode("utf-8", "replace")
    return ctypes.string_at(field.value, field.length).decode("utf-8", "replace")


def _field_key(field: GLib.LogField) -> str:
    key = field.key
    if isinstance(key, bytes):
        return key.decode("utf-8", "replace")
    return str(key)


def install_benign_gdk_filters() -> None:
    """Drop GDK's benign Vulkan suboptimal-swapchain warnings.

    Entering/leaving fullscreen often yields ``VK_SUBOPTIMAL_KHR``; GDK still
    presents successfully. Filter only that message so real Gdk warnings stay
    visible on the console.
    """

    def writer(
        log_level: GLib.LogLevelFlags,
        fields: list[GLib.LogField],
        _user_data: object = None,
    ) -> GLib.LogWriterOutput:
        domain = ""
        message = ""
        for field in fields:
            key = _field_key(field)
            if key == "GLIB_DOMAIN":
                domain = _field_text(field)
            elif key == "MESSAGE":
                message = _field_text(field)
        if domain == "Gdk" and "VK_SUBOPTIMAL_KHR" in message:
            return GLib.LogWriterOutput.HANDLED
        # OverlaySplitView / MultiLayoutView animate through widths slightly
        # below a child's reported minimum (often 1px). Layout still succeeds.
        if domain == "Gtk" and "Trying to measure" in message and "needs at least" in message:
            return GLib.LogWriterOutput.HANDLED
        return GLib.log_writer_default(log_level, fields, _user_data)

    _KEEPALIVE.append(writer)
    GLib.log_set_writer_func(writer)
