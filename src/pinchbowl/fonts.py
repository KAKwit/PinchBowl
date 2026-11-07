from __future__ import annotations

from dataclasses import dataclass

import gi

gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")

from gi.repository import PangoCairo


@dataclass(frozen=True)
class RecipeFont:
    id: str
    name: str
    families: tuple[str, ...]
    kind: str


CURATED_FONTS: tuple[RecipeFont, ...] = (
    RecipeFont("default", "System UI", ("Adwaita Sans", "Cantarell", "sans-serif"), "sans"),
    RecipeFont("adwaita-sans", "Adwaita Sans", ("Adwaita Sans", "Cantarell", "sans-serif"), "sans"),
    RecipeFont("cantarell", "Cantarell", ("Cantarell", "sans-serif"), "sans"),
    RecipeFont("inter", "Inter", ("Inter", "sans-serif"), "sans"),
    RecipeFont("ibm-plex-sans", "IBM Plex Sans", ("IBM Plex Sans", "sans-serif"), "sans"),
    RecipeFont(
        "source-sans",
        "Source Sans",
        ("Source Sans 3", "Source Sans Pro", "sans-serif"),
        "sans",
    ),
    RecipeFont("noto-sans", "Noto Sans", ("Noto Sans", "sans-serif"), "sans"),
    RecipeFont(
        "liberation-sans", "Liberation Sans", ("Liberation Sans", "sans-serif"), "sans"
    ),
    RecipeFont("ubuntu", "Ubuntu", ("Ubuntu", "sans-serif"), "sans"),
    RecipeFont("literata", "Literata", ("Literata", "serif"), "serif"),
    RecipeFont(
        "source-serif",
        "Source Serif",
        ("Source Serif 4", "Source Serif Pro", "serif"),
        "serif",
    ),
    RecipeFont("noto-serif", "Noto Serif", ("Noto Serif", "serif"), "serif"),
    RecipeFont(
        "liberation-serif",
        "Liberation Serif",
        ("Liberation Serif", "serif"),
        "serif",
    ),
    RecipeFont("charter", "Charter", ("Bitstream Charter", "Charter", "serif"), "serif"),
    RecipeFont("adwaita-mono", "Adwaita Mono", ("Adwaita Mono", "monospace"), "mono"),
    RecipeFont(
        "source-code-pro", "Source Code Pro", ("Source Code Pro", "monospace"), "mono"
    ),
    RecipeFont("ibm-plex-mono", "IBM Plex Mono", ("IBM Plex Mono", "monospace"), "mono"),
    RecipeFont(
        "jetbrains-mono", "JetBrains Mono", ("JetBrains Mono", "monospace"), "mono"
    ),
    RecipeFont("ubuntu-mono", "Ubuntu Mono", ("Ubuntu Mono", "monospace"), "mono"),
)

FONT_BY_ID = {font.id: font for font in CURATED_FONTS}
GENERIC = {"sans-serif", "serif", "monospace", "system-ui"}
KIND_LABEL = {"sans": "Sans", "serif": "Serif", "mono": "Monospace"}

_installed: set[str] | None = None


def _normalize(name: str) -> str:
    return "".join(name.lower().split())


def installed_family_names() -> set[str]:
    global _installed
    if _installed is not None:
        return _installed
    fontmap = PangoCairo.font_map_get_default()
    names = {family.get_name() for family in fontmap.list_families()}
    _installed = {_normalize(name) for name in names}
    return _installed


def _family_present(name: str, installed: set[str]) -> bool:
    if name.lower() in GENERIC:
        return True
    return _normalize(name) in installed


def font_available(font: RecipeFont, installed: set[str] | None = None) -> bool:
    if font.id == "default":
        return True
    names = installed if installed is not None else installed_family_names()
    return any(
        _family_present(family, names)
        for family in font.families
        if family.lower() not in GENERIC
    )


def available_fonts() -> list[RecipeFont]:
    installed = installed_family_names()
    return [font for font in CURATED_FONTS if font_available(font, installed)]


def resolve_font(font_id: str) -> RecipeFont:
    font = FONT_BY_ID.get(font_id, FONT_BY_ID["default"])
    if not font_available(font):
        return FONT_BY_ID["default"]
    return font


def css_font_family(font_id: str) -> str:
    parts = []
    for family in resolve_font(font_id).families:
        if family.lower() in GENERIC:
            parts.append(family)
        else:
            parts.append(f'"{family}"')
    return ", ".join(parts)
