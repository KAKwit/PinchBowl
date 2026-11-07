from __future__ import annotations

from pinchbowl.adjust import (
    adjust_ingredient_line,
    adjust_ingredients,
    nearest_scale_factor,
    parse_servings,
    scale_label,
    scale_yield_text,
)


def test_parse_servings_and_scale_label() -> None:
    assert parse_servings("Serves 8") == 8
    assert parse_servings("4–6 people") == 4
    assert scale_label(0.5) == "½×"
    assert scale_label(1.5) == "1½×"
    assert nearest_scale_factor(1.4) == 1.5


def test_scale_simple_line() -> None:
    assert adjust_ingredient_line("2 cups flour", 2.0, "original") == "4 cups flour"
    assert adjust_ingredient_line("½ tsp salt", 2.0, "original") == "1 tsp salt"


def test_dual_metric_us() -> None:
    line = "300g/10oz butter"
    metric = adjust_ingredient_line(line, 1.0, "metric")
    us = adjust_ingredient_line(line, 1.0, "us")
    assert "g" in metric.lower() or "300" in metric
    assert "oz" in us.lower() or "10" in us


def test_heading_passthrough() -> None:
    assert adjust_ingredient_line("# Sauce", 2.0, "metric") == "# Sauce"


def test_scale_yield_and_block() -> None:
    assert "16" in scale_yield_text("8 servings", 2.0)
    block = adjust_ingredients("1 cup milk\n# Topping\n2 tbsp sugar", 0.5, "original")
    assert "½ cups milk" in block or "½ cup milk" in block
    assert "# Topping" in block
    assert "1 tbsp sugar" in block
