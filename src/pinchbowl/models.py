from __future__ import annotations

from dataclasses import dataclass, field

from pinchbowl.duration import format_duration_text, parse_minutes


@dataclass
class GroceryItem:
    """One line on the shared groceries checklist."""

    id: int
    text: str
    checked: bool = False
    position: int = 0


@dataclass
class Recipe:
    """Recipe model aligned with Schema.org / Recipe and Mela export fields."""

    id: int
    uid: str
    title: str
    description: str = ""
    ingredients: str = ""
    instructions: str = ""
    notes: str = ""
    nutrition: str = ""
    yield_text: str = ""
    prep_time: str = ""
    cook_time: str = ""
    total_time: str = ""
    source: str = ""
    source_url: str = ""
    image_path: str = ""
    ai_prompt: str = ""
    favorite: bool = False
    want_to_cook: bool = False
    created_at: float = 0.0
    updated_at: float = 0.0
    tags: list[str] = field(default_factory=list)

    @property
    def time_label(self) -> str:
        for value in (self.total_time, self.cook_time, self.prep_time):
            if value:
                return format_duration_text(value)
        return ""

    @property
    def sort_minutes(self) -> int | None:
        for value in (self.total_time, self.cook_time, self.prep_time):
            minutes = parse_minutes(value)
            if minutes is not None:
                return minutes
        return None
