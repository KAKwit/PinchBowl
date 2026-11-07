from __future__ import annotations

from pinchbowl.urls import (
    canonicalize_url,
    clean_url,
    openable_url,
    source_display,
)


def test_strips_utm_and_tracking() -> None:
    raw = "https://www.Example.com/recipe/soup?utm_source=x&fbclid=1&keep=yes"
    assert canonicalize_url(raw) == "example.com/recipe/soup?keep=yes"
    assert clean_url(raw) == "https://example.com/recipe/soup?keep=yes"


def test_trailing_slash_and_www() -> None:
    assert canonicalize_url("https://www.site.test/path/") == "site.test/path"


def test_schemeless_gets_https() -> None:
    assert clean_url("recipetineats.com/foo") == "https://recipetineats.com/foo"
    assert canonicalize_url("recipetineats.com/foo") == "recipetineats.com/foo"


def test_empty() -> None:
    assert canonicalize_url("") == ""
    assert clean_url("") == ""



def test_openable_url() -> None:
    assert openable_url("https://a.test/r", "") == "https://a.test/r"
    assert openable_url("", "b.test/path") == "https://b.test/path"
    assert openable_url("", "My Cookbook") == ""


def test_source_display() -> None:
    assert source_display("https://www.recipetineats.com/x") == (
        "recipetineats.com",
        True,
    )
    assert source_display("", "AI generated") == ("AI generated", False)
    assert source_display("", "vero.cooking/pasta") == ("vero.cooking", True)
