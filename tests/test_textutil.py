from __future__ import annotations

from pinchbowl.textutil import clean_description, decode_entities


def test_decode_entities() -> None:
    assert decode_entities("A &amp; B") == "A & B"
    assert decode_entities("&#38") == "&"
    assert decode_entities("plain") == "plain"


def test_clean_description() -> None:
    raw = "Recipe video above. Soft bread.Next sentence"
    out = clean_description(raw)
    assert not out.lower().startswith("recipe video")
    assert "bread. Next" in out
