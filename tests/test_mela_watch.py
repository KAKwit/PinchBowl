from __future__ import annotations

import json
import zipfile
from pathlib import Path

from pinchbowl.mela import MelaImportSession, import_mela_path
from pinchbowl.mela_watch import file_fingerprint, fingerprint_unchanged, remember_fingerprint
from pinchbowl.store import Store


class _FakeSettings:
    def __init__(self) -> None:
        self.mela_watch_path = ""
        self.mela_watch_mtime = 0.0
        self.mela_watch_size = 0

    def update(self, notify: bool = True, **values: object) -> None:
        for key, value in values.items():
            setattr(self, key, value)


def _write_archive(path: Path, recipes: list[dict]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for i, data in enumerate(recipes):
            zf.writestr(f"{i}.melarecipe", json.dumps(data))


def test_new_only_skips_existing_uid(tmp_xdg: Path, store: Store, tmp_path: Path) -> None:
    store.upsert_recipe(
        uid="keep-1",
        title="Old Title",
        ingredients="a",
        instructions="b",
    )
    archive = tmp_path / "lib.melarecipes"
    _write_archive(
        archive,
        [
            {
                "id": "keep-1",
                "title": "Updated Title",
                "ingredients": "x",
                "instructions": "y",
            },
            {
                "id": "new-2",
                "title": "Brand New",
                "ingredients": "c",
                "instructions": "d",
            },
        ],
    )
    assert import_mela_path(archive, store, new_only=True) == 1
    recipes = {r.uid: r for r in store.list_recipes()}
    assert recipes["keep-1"].title == "Old Title"
    assert recipes["new-2"].title == "Brand New"


def test_new_only_session_counts_inserts_only(
    tmp_xdg: Path, store: Store, tmp_path: Path
) -> None:
    store.upsert_recipe(uid="s1", title="Soup", ingredients="stock", instructions="simmer")
    archive = tmp_path / "lib.melarecipes"
    _write_archive(
        archive,
        [
            {"id": "s1", "title": "Soup", "ingredients": "stock", "instructions": "simmer"},
            {"id": "s2", "title": "Salad", "ingredients": "lettuce", "instructions": "toss"},
        ],
    )
    with MelaImportSession(archive, store, new_only=True) as session:
        assert session.total == 2
        first = session.import_next()
        assert first is not None
        assert first[1] == "Salad"
        assert session.import_next() is None
        assert session.imported == 1


def test_fingerprint_skip_and_remember(tmp_path: Path) -> None:
    settings = _FakeSettings()
    path = tmp_path / "watch.melarecipes"
    path.write_text("placeholder", encoding="utf-8")
    assert file_fingerprint(path) is not None
    assert not fingerprint_unchanged(settings, path)

    remember_fingerprint(settings, path)
    assert fingerprint_unchanged(settings, path)
    assert settings.mela_watch_path == str(path)

    path.write_text("placeholder-changed", encoding="utf-8")
    assert not fingerprint_unchanged(settings, path)


def test_new_only_skips_by_canonical_url(
    tmp_xdg: Path, store: Store, tmp_path: Path
) -> None:
    store.upsert_recipe(
        uid="cake-1",
        title="Cake",
        ingredients="flour",
        instructions="bake",
        source_url="https://example.test/cake",
    )
    # Same recipe identity via link, different Mela id — still treat as present.
    path = tmp_path / "cake.melarecipe"
    path.write_text(
        json.dumps(
            {
                "id": "other-id",
                "title": "Cake Remix",
                "ingredients": "flour",
                "instructions": "bake",
                "link": "https://www.example.test/cake?utm_source=x",
            }
        ),
        encoding="utf-8",
    )
    assert import_mela_path(path, store, new_only=True) == 0
    assert len(store.list_recipes()) == 1
    assert store.list_recipes()[0].title == "Cake"
