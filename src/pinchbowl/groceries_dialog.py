from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk

from pinchbowl.ingredients import grocery_candidate_lines  # re-export for callers


def present_groceries_dialog(
    parent: Gtk.Widget,
    lines: list[str],
    *,
    on_add: Callable[[list[str]], None],
) -> None:
    """Checklist dialog: all lines ticked by default; Add to Groceries confirms."""
    dialog = Adw.AlertDialog(
        heading="Add to Groceries",
        body="Untick anything you already have, then add the rest.",
    )
    root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    root.set_margin_top(8)

    if not lines:
        empty = Gtk.Label(label="This recipe has no ingredients to add.", xalign=0)
        empty.add_css_class("dim-label")
        root.append(empty)
        dialog.set_extra_child(root)
        dialog.add_response("cancel", "Close")
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.present(parent)
        return

    checks_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    checks: list[tuple[str, Gtk.CheckButton]] = []
    for line in lines:
        check = Gtk.CheckButton(label=line)
        check.set_active(True)
        checks.append((line, check))
        checks_box.append(check)

    scroll = Gtk.ScrolledWindow()
    scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scroll.set_propagate_natural_height(True)
    scroll.set_min_content_height(80)
    scroll.set_max_content_height(360)
    scroll.set_child(checks_box)
    root.append(scroll)

    dialog.set_extra_child(root)
    dialog.add_response("cancel", "Cancel")
    dialog.add_response("add", "Add to Groceries")
    dialog.set_response_appearance("add", Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response("add")
    dialog.set_close_response("cancel")

    def on_response(_d: Adw.AlertDialog, response: str) -> None:
        if response != "add":
            return
        selected = [text for text, check in checks if check.get_active()]
        if selected:
            on_add(selected)

    dialog.connect("response", on_response)
    dialog.present(parent)
