from __future__ import annotations

from pinchbowl.ingredients import grocery_candidate_lines, strip_parenthetical_notes


def test_strip_parenthetical_notes() -> None:
    assert strip_parenthetical_notes("2 cups flour (sifted)") == "2 cups flour"
    assert (
        strip_parenthetical_notes("1 onion, finely chopped (or 2 shallots)")
        == "1 onion, finely chopped"
    )
    assert (
        strip_parenthetical_notes("50g parmesan (finely grated (sub pecorino))")
        == "50g parmesan"
    )
    assert strip_parenthetical_notes("salt") == "salt"


def test_grocery_candidate_lines_strips_notes() -> None:
    text = "# Dough\n2 cups flour (sifted)\n1 tsp salt (to taste)\n"
    assert grocery_candidate_lines(text) == ["2 cups flour", "1 tsp salt"]
