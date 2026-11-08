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


def test_grocery_items_have_created_at_and_sort(store: Store) -> None:
    store.add_grocery_items(["milk", "eggs", "apples"])
    items = store.list_grocery_items(sort="created", ascending=True)
    assert [i.text for i in items] == ["milk", "eggs", "apples"]
    assert items[0].created_at > 0
    assert items[0].created_at <= items[1].created_at <= items[2].created_at

    newest_first = store.list_grocery_items(sort="created", ascending=False)
    assert [i.text for i in newest_first] == ["apples", "eggs", "milk"]

    by_name = store.list_grocery_items(sort="text", ascending=True)
    assert [i.text for i in by_name] == ["apples", "eggs", "milk"]


def test_update_grocery_text(store: Store) -> None:
    store.add_grocery_items(["milk"])
    item = store.list_grocery_items()[0]
    assert store.update_grocery_text(item.id, "  oat milk  ") is True
    updated = store.list_grocery_items()[0]
    assert updated.text == "oat milk"
    assert store.update_grocery_text(item.id, "   ") is False
    assert store.list_grocery_items()[0].text == "oat milk"


def test_grocery_created_at_migration(tmp_xdg: Path) -> None:
    """Existing DBs without created_at get the column and a stable backfill."""
    import sqlite3

    from pinchbowl.paths import recipes_db_path

    db = recipes_db_path()
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE grocery_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            checked INTEGER NOT NULL DEFAULT 0,
            position INTEGER NOT NULL DEFAULT 0
        );
        INSERT INTO grocery_items (text, checked, position) VALUES ('old', 0, 0);
        INSERT INTO grocery_items (text, checked, position) VALUES ('newer', 0, 1);
        """
    )
    conn.commit()
    conn.close()

    store = Store(db)
    cols = {
        str(r["name"])
        for r in store.conn.execute("PRAGMA table_info(grocery_items)").fetchall()
    }
    assert "created_at" in cols
    items = store.list_grocery_items(sort="created", ascending=True)
    assert [i.text for i in items] == ["old", "newer"]
    assert items[0].created_at < items[1].created_at
    store.close()


def test_fts_prefix_query_helper() -> None:
    assert Store._fts_prefix_query('hello "world"') == "hello* world*"
    assert Store._fts_prefix_query("   ") is None
    assert Store._fts_prefix_query("---") is None


def test_repairs_bogus_ai_generated_urls(tmp_xdg: Path) -> None:
    from pinchbowl.paths import recipes_db_path

    db = recipes_db_path()
    first = Store(db)
    recipe = first.upsert_recipe(
        title="Pie",
        ingredients="beef",
        instructions="bake",
        source="AI generated",
        source_url="",
        ai_prompt="",
    )
    first.conn.execute(
        """
        UPDATE recipes
        SET source_url = ?, canonical_url = ?
        WHERE id = ?
        """,
        ("https://ai generated/", "ai generated/", recipe.id),
    )
    first.conn.commit()
    first.close()

    repaired = Store(db)
    row = repaired.get(recipe.id)
    assert row is not None
    assert row.source == "AI generated"
    assert row.source_url == ""
    assert repaired.conn.execute(
        "SELECT canonical_url FROM recipes WHERE id = ?", (recipe.id,)
    ).fetchone()[0] == ""
    repaired.close()
