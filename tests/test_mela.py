from __future__ import annotations

import base64
import json
from pathlib import Path

from pinchbowl.images import MAX_BYTES
from pinchbowl.mela import parse_melarecipe, recipe_to_melarecipe
from pinchbowl.models import Recipe
from pinchbowl.store import Store


def _tiny_png() -> bytes:
    # 1x1 PNG
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )


def test_parse_melarecipe_fields(tmp_xdg: Path) -> None:
    data = {
        "id": "mela-1",
        "title": "Mela Cake",
        "text": "Nice cake",
        "ingredients": "flour",
        "instructions": "bake",
        "yield": "8",
        "prepTime": "10min",
        "cookTime": "30min",
        "link": "https://example.test/cake?utm_medium=share",
        "categories": ["Dessert", "Baking"],
        "favorite": True,
        "wantToCook": True,
        "images": [base64.b64encode(_tiny_png()).decode("ascii")],
    }
    mapped = parse_melarecipe(data)
    assert mapped["uid"] == "mela-1"
    assert mapped["title"] == "Mela Cake"
    assert mapped["want_to_cook"] is True
    assert mapped["tags"] == ["Dessert", "Baking"]
    assert "utm_medium" not in mapped["source_url"]
    assert mapped["image_path"]
    assert Path(mapped["image_path"]).is_file()


def test_oversized_image_rejected(tmp_xdg: Path) -> None:
    huge = b"\x89PNG\r\n\x1a\n" + b"\x00" * (MAX_BYTES + 10)
    mapped = parse_melarecipe(
        {
            "title": "Huge",
            "ingredients": "x",
            "instructions": "y",
            "images": [base64.b64encode(huge).decode("ascii")],
        }
    )
    assert mapped["image_path"] == ""


def test_roundtrip_melarecipe(tmp_xdg: Path, store: Store) -> None:
    mapped = parse_melarecipe(
        {
            "id": "rt-1",
            "title": "Roundtrip",
            "ingredients": "a",
            "instructions": "b",
            "wantToCook": True,
        }
    )
    recipe = store.upsert_recipe(**mapped)
    exported = recipe_to_melarecipe(recipe)
    assert exported["title"] == "Roundtrip"
    assert exported["wantToCook"] is True
    assert json.dumps(exported)


def test_export_preserves_ai_label_without_fake_url() -> None:
    recipe = Recipe(
        id=1,
        uid="ai-1",
        title="Pie",
        ingredients="beef",
        instructions="bake",
        source="AI generated",
        ai_prompt="make a pie",
    )
    exported = recipe_to_melarecipe(recipe)
    assert exported["link"] == "AI generated"
    mapped = parse_melarecipe(exported)
    assert mapped["source"] == "AI generated"
    assert mapped["source_url"] == ""


def test_import_file(tmp_xdg: Path, store: Store, tmp_path: Path) -> None:
    from pinchbowl.mela import import_melarecipe_file

    path = tmp_path / "cake.melarecipe"
    path.write_text(
        json.dumps(
            {
                "title": "File Cake",
                "ingredients": "sugar",
                "instructions": "mix",
            }
        ),
        encoding="utf-8",
    )
    assert import_melarecipe_file(path, store) == 1
    assert store.list_recipes(query="File")[0].title == "File Cake"


def test_import_session_archive(tmp_xdg: Path, store: Store, tmp_path: Path) -> None:
    import zipfile

    from pinchbowl.mela import MelaImportSession, import_mela_path

    archive = tmp_path / "library.melarecipes"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(
            "one.melarecipe",
            json.dumps(
                {
                    "id": "s1",
                    "title": "Soup",
                    "ingredients": "stock",
                    "instructions": "simmer",
                }
            ),
        )
        zf.writestr(
            "two.melarecipe",
            json.dumps(
                {
                    "id": "s2",
                    "title": "Salad",
                    "ingredients": "lettuce",
                    "instructions": "toss",
                }
            ),
        )
        zf.writestr("__MACOSX/._junk", "nope")

    with MelaImportSession(archive, store) as session:
        assert session.total == 2
        first = session.import_next()
        second = session.import_next()
        assert first == (1, "Soup")
        assert second == (2, "Salad")
        assert session.import_next() is None
        assert session.imported == 2

    titles = {r.title for r in store.list_recipes()}
    assert "Soup" in titles
    assert "Salad" in titles
    assert import_mela_path(archive, store) == 2


def test_export_session_archive(tmp_xdg: Path, store: Store, tmp_path: Path) -> None:
    import zipfile

    from pinchbowl.mela import MelaExportSession, export_melarecipes_archive, import_mela_path

    store.upsert_recipe(
        uid="e1",
        title="Export Soup",
        ingredients="stock",
        instructions="simmer",
        tags=["Soup"],
        favorite=True,
    )
    store.upsert_recipe(
        uid="e2",
        title="Export Salad",
        ingredients="lettuce",
        instructions="toss",
        want_to_cook=True,
    )
    # Same title should get a unique archive member name.
    store.upsert_recipe(
        uid="e3",
        title="Export Soup",
        ingredients="broth",
        instructions="boil",
    )

    recipes = store.list_recipes(sort="title", ascending=True)
    archive = tmp_path / "out.melarecipes"
    with MelaExportSession(archive, recipes) as session:
        assert session.total == 3
        assert session.export_next() is not None
        assert session.export_next() is not None
        assert session.export_next() is not None
        assert session.export_next() is None
        assert session.exported == 3

    with zipfile.ZipFile(archive) as zf:
        names = [n for n in zf.namelist() if n.endswith(".melarecipe")]
        assert len(names) == 3
        assert len(set(names)) == 3
        payloads = [json.loads(zf.read(n)) for n in names]
        titles = {p["title"] for p in payloads}
        assert titles == {"Export Soup", "Export Salad"}
        soup = next(p for p in payloads if p["id"] == "e1")
        assert soup["favorite"] is True
        assert soup["categories"] == ["Soup"]
        salad = next(p for p in payloads if p["id"] == "e2")
        assert salad["wantToCook"] is True

    store.clear_library()
    assert import_mela_path(archive, store) == 3
    reimported = store.list_recipes()
    assert len(reimported) == 3
    assert {r.title for r in reimported} == {"Export Soup", "Export Salad"}
    assert {r.uid for r in reimported} == {"e1", "e2", "e3"}

    again = tmp_path / "again.melarecipes"
    assert export_melarecipes_archive(again, reimported) == 3
    assert again.is_file()


def test_export_session_discard(tmp_xdg: Path, store: Store, tmp_path: Path) -> None:
    from pinchbowl.mela import MelaExportSession

    store.upsert_recipe(title="A", ingredients="a", instructions="b")
    archive = tmp_path / "partial.melarecipes"
    session = MelaExportSession(archive, store.list_recipes())
    assert session.export_next() is not None
    session.discard()
    session.close()
    assert not archive.exists()
