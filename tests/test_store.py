from __future__ import annotations

from pathlib import Path

from pinchbowl.store import Store


def test_fresh_store_starts_empty(store: Store) -> None:
    assert store.count() == 0
    assert store.list_recipes() == []
    assert store.list_tags() == []


def test_upsert_dedupe_by_canonical_url(store: Store) -> None:
    first = store.upsert_recipe(
        title="Soup",
        ingredients="water",
        instructions="boil",
        source_url="https://example.test/soup?utm_source=x",
    )
    second = store.upsert_recipe(
        title="Soup Updated",
        ingredients="stock",
        instructions="simmer",
        source_url="https://www.example.test/soup",
        update_existing=True,
    )
    assert first.id == second.id
    assert store.count() == 1
    assert store.get(first.id).title == "Soup Updated"


def test_bare_source_does_not_dedupe(store: Store) -> None:
    store.upsert_recipe(title="A", ingredients="x", source="vero.cooking")
    store.upsert_recipe(title="B", ingredients="y", source="vero.cooking")
    assert store.count() == 2


def test_uid_dedupe(store: Store) -> None:
    a = store.upsert_recipe(title="One", ingredients="a", uid="same-uid")
    b = store.upsert_recipe(title="Two", ingredients="b", uid="same-uid")
    assert a.id == b.id
    assert store.count() == 1


def test_upsert_can_clear_favorite_and_want_to_cook(store: Store) -> None:
    first = store.upsert_recipe(
        uid="flags",
        title="Flagged",
        ingredients="x",
        favorite=True,
        want_to_cook=True,
    )
    assert first.favorite is True
    assert first.want_to_cook is True
    second = store.upsert_recipe(
        uid="flags",
        title="Flagged",
        ingredients="x",
        favorite=False,
        want_to_cook=False,
    )
    assert first.id == second.id
    assert second.favorite is False
    assert second.want_to_cook is False


def test_fts_prefix_and_special_chars(store: Store) -> None:
    store.upsert_recipe(
        title="Tomato Basil Pasta",
        ingredients="tomatoes",
        instructions="cook",
    )
    hits = store.list_recipes(query="Tomato")
    assert len(hits) == 1
    # Characters that would break raw FTS5 MATCH must not raise.
    assert store.list_recipes(query='pasta " OR') == []
    assert store.list_recipes(query="Tomato*")  # sanitized, still finds


def test_fts_rebuild_on_open(tmp_xdg: Path) -> None:
    from pinchbowl.paths import recipes_db_path

    db_path = recipes_db_path()
    s1 = Store(db_path)
    s1.clear_library()
    recipe = s1.upsert_recipe(title="Rebuild Me", ingredients="salt", instructions="mix")
    # Simulate drift: wipe FTS while leaving the recipe row.
    s1.conn.execute("DELETE FROM recipes_fts")
    s1.conn.commit()
    s1.close()

    s2 = Store(db_path)
    hits = s2.list_recipes(query="Rebuild")
    assert any(r.id == recipe.id for r in hits)
    s2.close()


def test_set_image_path_unlinks_old(store: Store, tmp_xdg: Path) -> None:
    from pinchbowl.paths import images_dir

    recipe = store.upsert_recipe(title="Pic", ingredients="x")
    img_dir = images_dir()
    old = img_dir / "old.jpg"
    new = img_dir / "new.jpg"
    old.write_bytes(b"\xff\xd8\xff" + b"\x00" * 20)
    new.write_bytes(b"\xff\xd8\xff" + b"\x00" * 20)
    store.set_image_path(recipe.id, str(old))
    store.set_image_path(recipe.id, str(new))
    assert not old.exists()
    assert new.exists()


def test_clear_library_clears_groceries(store: Store) -> None:
    store.upsert_recipe(title="R", ingredients="x")
    store.add_grocery_items(["milk", "eggs"])
    assert store.list_grocery_items()
    store.clear_library()
    assert store.count() == 0
    assert store.list_grocery_items() == []


def test_fts_prefix_query_helper() -> None:
    assert Store._fts_prefix_query('hello "world"') == "hello* world*"
    assert Store._fts_prefix_query("   ") is None
    assert Store._fts_prefix_query("---") is None
