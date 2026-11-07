from __future__ import annotations

from pinchbowl.directions import parse_instructions, split_step


def test_split_step() -> None:
    assert split_step("1. Mix flour") == ("1.", "Mix flour")
    assert split_step("2) Whisk") == ("2)", "Whisk")
    assert split_step("Preheat oven") == (None, "Preheat oven")


def test_parse_headings_and_steps() -> None:
    text = "# Dough\n1. Mix\n## Bake\n2. Bake 20 min\n&amp; rest"
    items, max_digits = parse_instructions(text)
    assert max_digits >= 2
    assert ("heading", "Dough") in items
    assert ("step", "1.", "Mix") in items
    assert ("heading", "Bake") in items
    assert any(i[0] == "plain" and "&" in i[1] for i in items)
