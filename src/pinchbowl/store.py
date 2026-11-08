from __future__ import annotations

import re
import sqlite3
import time
import uuid
from pathlib import Path

from pinchbowl.images import clear_list_thumbs, forget_list_thumb
from pinchbowl.models import GroceryItem, Recipe
from pinchbowl.paths import images_dir
from pinchbowl.urls import canonicalize_url

# FTS5 operators / quoting that break MATCH when typed in the search box.
_FTS_SPECIAL = re.compile(r'["*^(){}[\]:~]+')

SCHEMA = """
CREATE TABLE IF NOT EXISTS recipes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    ingredients TEXT NOT NULL DEFAULT '',
    instructions TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    nutrition TEXT NOT NULL DEFAULT '',
    yield_text TEXT NOT NULL DEFAULT '',
    prep_time TEXT NOT NULL DEFAULT '',
    cook_time TEXT NOT NULL DEFAULT '',
    total_time TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL DEFAULT '',
    canonical_url TEXT NOT NULL DEFAULT '',
    image_path TEXT NOT NULL DEFAULT '',
    ai_prompt TEXT NOT NULL DEFAULT '',
    favorite INTEGER NOT NULL DEFAULT 0,
    want_to_cook INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS recipe_tags (
    recipe_id INTEGER NOT NULL,
    tag TEXT NOT NULL,
    PRIMARY KEY (recipe_id, tag),
    FOREIGN KEY (recipe_id) REFERENCES recipes(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS grocery_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    checked INTEGER NOT NULL DEFAULT 0,
    position INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_recipe_tags_tag ON recipe_tags(tag);
CREATE INDEX IF NOT EXISTS idx_recipes_updated ON recipes(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_recipes_canonical ON recipes(canonical_url);
CREATE INDEX IF NOT EXISTS idx_grocery_items_position ON grocery_items(position);

CREATE VIRTUAL TABLE IF NOT EXISTS recipes_fts USING fts5(
    title,
    description,
    ingredients,
    instructions,
    tags
);
"""


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self._ensure_columns()
        self._repair_ai_source_urls()
        self._ensure_fts()
        self.conn.commit()

    def _ensure_columns(self) -> None:
        """Add columns introduced after the initial schema (existing DBs)."""
        recipe_cols = {
            str(r["name"])
            for r in self.conn.execute("PRAGMA table_info(recipes)").fetchall()
        }
        if "ai_prompt" not in recipe_cols:
            self.conn.execute(
                "ALTER TABLE recipes ADD COLUMN ai_prompt TEXT NOT NULL DEFAULT ''"
            )

        grocery_cols = {
            str(r["name"])
            for r in self.conn.execute("PRAGMA table_info(grocery_items)").fetchall()
        }
        if "created_at" not in grocery_cols:
            self.conn.execute(
                "ALTER TABLE grocery_items ADD COLUMN created_at REAL NOT NULL DEFAULT 0"
            )
            # Preserve append order for existing rows (oldest first).
            rows = self.conn.execute(
                "SELECT id FROM grocery_items ORDER BY position ASC, id ASC"
            ).fetchall()
            base = time.time() - max(len(rows), 1)
            for i, row in enumerate(rows):
                self.conn.execute(
                    "UPDATE grocery_items SET created_at = ? WHERE id = ?",
                    (base + i, int(row["id"])),
                )

    def _repair_ai_source_urls(self) -> None:
        """Clear bogus URLs created by treating ``AI generated`` as a host."""
        # Older Mela export prefixed https:// onto the label; clean_url then
        # stored hosts like "ai generated". Restore the label and drop the URL.
        self.conn.execute(
            """
            UPDATE recipes SET
                source = 'AI generated',
                source_url = '',
                canonical_url = ''
            WHERE
                lower(trim(source, '/')) = 'ai generated'
                OR lower(source_url) IN (
                    'https://ai generated/',
                    'http://ai generated/',
                    'https://ai generated',
                    'http://ai generated'
                )
                OR lower(canonical_url) IN ('ai generated/', 'ai generated')
            """
        )

    def _ensure_fts(self) -> None:
        """Rebuild FTS if it drifted (e.g. recipes existed before the FTS table)."""
        n_recipes = self.count()
        row = self.conn.execute("SELECT COUNT(*) AS n FROM recipes_fts").fetchone()
        n_fts = int(row["n"]) if row else 0
        if n_fts == n_recipes:
            return
        self.conn.execute("DELETE FROM recipes_fts")
        for recipe_row in self.conn.execute("SELECT id FROM recipes").fetchall():
            self._reindex_fts(int(recipe_row["id"]))

    @staticmethod
    def _fts_prefix_query(raw: str) -> str | None:
        """Sanitize user text into an FTS5 prefix query, or None if empty."""
        parts: list[str] = []
        for token in raw.strip().split():
            cleaned = _FTS_SPECIAL.sub("", token).strip("-")
            if cleaned:
                parts.append(f"{cleaned}*")
        if not parts:
            return None
        return " ".join(parts)

    def close(self) -> None:
        self.conn.close()

    def count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS n FROM recipes").fetchone()
        return int(row["n"]) if row else 0

    def _tags_for(self, recipe_id: int) -> list[str]:
        rows = self.conn.execute(
            "SELECT tag FROM recipe_tags WHERE recipe_id = ? ORDER BY tag COLLATE NOCASE",
            (recipe_id,),
        ).fetchall()
        return [str(r["tag"]) for r in rows]

    def _row_to_recipe(self, row: sqlite3.Row) -> Recipe:
        return Recipe(
            id=int(row["id"]),
            uid=str(row["uid"]),
            title=str(row["title"]),
            description=str(row["description"] or ""),
            ingredients=str(row["ingredients"] or ""),
            instructions=str(row["instructions"] or ""),
            notes=str(row["notes"] or ""),
            nutrition=str(row["nutrition"] or ""),
            yield_text=str(row["yield_text"] or ""),
            prep_time=str(row["prep_time"] or ""),
            cook_time=str(row["cook_time"] or ""),
            total_time=str(row["total_time"] or ""),
            source=str(row["source"] or ""),
            source_url=str(row["source_url"] or ""),
            image_path=str(row["image_path"] or ""),
            ai_prompt=str(row["ai_prompt"] or "") if "ai_prompt" in row.keys() else "",
            favorite=bool(row["favorite"]),
            want_to_cook=bool(row["want_to_cook"]),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            tags=self._tags_for(int(row["id"])),
        )

    def _set_tags(self, recipe_id: int, tags: list[str]) -> None:
        self.conn.execute("DELETE FROM recipe_tags WHERE recipe_id = ?", (recipe_id,))
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in tags:
            tag = " ".join(str(raw).split())
            if not tag:
                continue
            key = tag.casefold()
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(tag)
            self.conn.execute(
                "INSERT INTO recipe_tags (recipe_id, tag) VALUES (?, ?)",
                (recipe_id, tag),
            )
        self._reindex_fts(recipe_id, tags=" ".join(cleaned))

    def _reindex_fts(self, recipe_id: int, *, tags: str | None = None) -> None:
        self.conn.execute("DELETE FROM recipes_fts WHERE rowid = ?", (recipe_id,))
        row = self.conn.execute(
            "SELECT title, description, ingredients, instructions FROM recipes WHERE id = ?",
            (recipe_id,),
        ).fetchone()
        if not row:
            return
        tag_text = " ".join(self._tags_for(recipe_id)) if tags is None else tags
        self.conn.execute(
            "INSERT INTO recipes_fts(rowid, title, description, ingredients, instructions, tags) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                recipe_id,
                row["title"],
                row["description"],
                row["ingredients"],
                row["instructions"],
                tag_text,
            ),
        )

    def get(self, recipe_id: int) -> Recipe | None:
        row = self.conn.execute("SELECT * FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
        return self._row_to_recipe(row) if row else None

    def find_by_uid(self, uid: str) -> Recipe | None:
        row = self.conn.execute("SELECT * FROM recipes WHERE uid = ?", (uid,)).fetchone()
        return self._row_to_recipe(row) if row else None

    def find_by_canonical_url(self, url: str) -> Recipe | None:
        key = canonicalize_url(url)
        if not key:
            return None
        row = self.conn.execute(
            "SELECT * FROM recipes WHERE canonical_url = ? OR uid = ?",
            (key, key),
        ).fetchall()
        return self._row_to_recipe(row[0]) if row else None

    def list_tags(self) -> list[tuple[str, int]]:
        rows = self.conn.execute(
            "SELECT tag, COUNT(*) AS n FROM recipe_tags "
            "GROUP BY tag COLLATE NOCASE ORDER BY tag COLLATE NOCASE"
        ).fetchall()
        return [(str(r["tag"]), int(r["n"])) for r in rows]

    def list_recipes(
        self,
        *,
        filter_key: str = "all",
        tag: str | None = None,
        query: str = "",
        sort: str = "created",
        ascending: bool = False,
    ) -> list[Recipe]:
        clauses: list[str] = []
        params: list[object] = []
        join = ""
        if filter_key == "favorites":
            clauses.append("favorite = 1")
        elif filter_key == "recent":
            pass
        elif filter_key == "tag" and tag:
            join = "INNER JOIN recipe_tags t ON t.recipe_id = recipes.id"
            clauses.append("t.tag = ? COLLATE NOCASE")
            params.append(tag)
        elif filter_key == "untagged":
            clauses.append(
                "NOT EXISTS (SELECT 1 FROM recipe_tags rt WHERE rt.recipe_id = recipes.id)"
            )

        match = self._fts_prefix_query(query) if query.strip() else None
        if match is not None:
            join += " INNER JOIN recipes_fts ON recipes_fts.rowid = recipes.id"
            clauses.append("recipes_fts MATCH ?")
            params.append(match)

        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""

        def run(sql: str) -> list[sqlite3.Row]:
            try:
                return self.conn.execute(sql, params).fetchall()
            except sqlite3.OperationalError:
                # Bad FTS syntax after sanitize should be rare; fail soft.
                return []

        # Most Recent keeps its own semantics regardless of user sort.
        if filter_key == "recent":
            order = "ORDER BY updated_at DESC LIMIT 20"
            sql = f"SELECT DISTINCT recipes.* FROM recipes {join}{where} {order}"
            return [self._row_to_recipe(r) for r in run(sql)]

        direction = "ASC" if ascending else "DESC"
        if sort == "title":
            order = f"ORDER BY title COLLATE NOCASE {direction}, id ASC"
            sql = f"SELECT DISTINCT recipes.* FROM recipes {join}{where} {order}"
            return [self._row_to_recipe(r) for r in run(sql)]

        if sort == "time":
            sql = f"SELECT DISTINCT recipes.* FROM recipes {join}{where}"
            recipes = [self._row_to_recipe(r) for r in run(sql)]

            def time_key(recipe: Recipe) -> tuple[int, int, int]:
                minutes = recipe.sort_minutes
                if minutes is None:
                    return (1, 0, recipe.id)
                return (0, minutes if ascending else -minutes, recipe.id)

            recipes.sort(key=time_key)
            return recipes

        order = f"ORDER BY created_at {direction}, id ASC"
        sql = f"SELECT DISTINCT recipes.* FROM recipes {join}{where} {order}"
        return [self._row_to_recipe(r) for r in run(sql)]

    def upsert_recipe(
        self,
        *,
        uid: str | None = None,
        title: str,
        description: str = "",
        ingredients: str = "",
        instructions: str = "",
        notes: str = "",
        nutrition: str = "",
        yield_text: str = "",
        prep_time: str = "",
        cook_time: str = "",
        total_time: str = "",
        source: str = "",
        source_url: str = "",
        image_path: str = "",
        ai_prompt: str = "",
        tags: list[str] | None = None,
        favorite: bool = False,
        want_to_cook: bool = False,
        update_existing: bool = True,
    ) -> Recipe:
        now = time.time()
        # Only treat real URLs as identity keys — bare source labels like
        # "vero.cooking" must not collide across unrelated recipes.
        canonical = canonicalize_url(source_url)
        if not canonical and ("://" in source or source.startswith("www.")):
            canonical = canonicalize_url(source)
        existing = None
        if uid:
            existing = self.find_by_uid(uid)
        if existing is None and canonical:
            existing = self.find_by_canonical_url(canonical)
        if existing is not None and not update_existing:
            return existing

        recipe_uid = uid or (existing.uid if existing else None) or canonical or str(uuid.uuid4())
        if existing is None:
            cur = self.conn.execute(
                """
                INSERT INTO recipes (
                    uid, title, description, ingredients, instructions, notes, nutrition,
                    yield_text, prep_time, cook_time, total_time, source, source_url,
                    canonical_url, image_path, ai_prompt, favorite, want_to_cook,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    recipe_uid,
                    title,
                    description,
                    ingredients,
                    instructions,
                    notes,
                    nutrition,
                    yield_text,
                    prep_time,
                    cook_time,
                    total_time,
                    source,
                    source_url,
                    canonical,
                    image_path,
                    ai_prompt,
                    int(favorite),
                    int(want_to_cook),
                    now,
                    now,
                ),
            )
            recipe_id = int(cur.lastrowid)
        else:
            recipe_id = existing.id
            # Empty image/ai_prompt keeps previous values (URL re-import without
            # a photo). Favorite / want_to_cook always take the incoming values
            # so a Mela re-import can clear them.
            self.conn.execute(
                """
                UPDATE recipes SET
                    uid = ?, title = ?, description = ?, ingredients = ?, instructions = ?,
                    notes = ?, nutrition = ?, yield_text = ?, prep_time = ?, cook_time = ?,
                    total_time = ?, source = ?, source_url = ?, canonical_url = ?,
                    image_path = COALESCE(NULLIF(?, ''), image_path),
                    ai_prompt = COALESCE(NULLIF(?, ''), ai_prompt),
                    favorite = ?, want_to_cook = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    recipe_uid,
                    title,
                    description,
                    ingredients,
                    instructions,
                    notes,
                    nutrition,
                    yield_text,
                    prep_time,
                    cook_time,
                    total_time,
                    source,
                    source_url,
                    canonical or canonicalize_url(existing.source_url) or "",
                    image_path,
                    ai_prompt,
                    int(favorite),
                    int(want_to_cook),
                    now,
                    recipe_id,
                ),
            )
        if tags is not None:
            self._set_tags(recipe_id, tags)
        elif existing is not None:
            self._set_tags(recipe_id, existing.tags)
        else:
            self._set_tags(recipe_id, [])
        self.conn.commit()
        recipe = self.get(recipe_id)
        assert recipe is not None
        return recipe

    def set_tags(self, recipe_id: int, tags: list[str]) -> None:
        self._set_tags(recipe_id, tags)
        self.conn.execute(
            "UPDATE recipes SET updated_at = ? WHERE id = ?",
            (time.time(), recipe_id),
        )
        self.conn.commit()

    def set_title(self, recipe_id: int, title: str) -> None:
        cleaned = " ".join(str(title).split())
        if not cleaned:
            return
        self.conn.execute(
            "UPDATE recipes SET title = ?, updated_at = ? WHERE id = ?",
            (cleaned, time.time(), recipe_id),
        )
        self._reindex_fts(recipe_id)
        self.conn.commit()

    def set_favorite(self, recipe_id: int, value: bool) -> None:
        self.conn.execute(
            "UPDATE recipes SET favorite = ?, updated_at = ? WHERE id = ?",
            (int(value), time.time(), recipe_id),
        )
        self.conn.commit()

    def set_image_path(self, recipe_id: int, image_path: str) -> None:
        row = self.conn.execute(
            "SELECT image_path FROM recipes WHERE id = ?", (recipe_id,)
        ).fetchone()
        old = str(row["image_path"] or "") if row else ""
        new = str(image_path or "")
        self.conn.execute(
            "UPDATE recipes SET image_path = ?, updated_at = ? WHERE id = ?",
            (new, time.time(), recipe_id),
        )
        self.conn.commit()
        if old and old != new:
            forget_list_thumb(old)
            path = Path(old)
            if path.is_file():
                try:
                    path.unlink()
                except OSError:
                    pass

    def delete(self, recipe_id: int) -> None:
        row = self.conn.execute(
            "SELECT image_path FROM recipes WHERE id = ?", (recipe_id,)
        ).fetchone()
        self.conn.execute("DELETE FROM recipes_fts WHERE rowid = ?", (recipe_id,))
        self.conn.execute("DELETE FROM recipes WHERE id = ?", (recipe_id,))
        self.conn.commit()
        if row and row["image_path"]:
            path_text = str(row["image_path"])
            forget_list_thumb(path_text)
            path = Path(path_text)
            if path.is_file():
                try:
                    path.unlink()
                except OSError:
                    pass

    def clear_library(self) -> int:
        """Delete every recipe, grocery item, and stored image. Returns recipe count removed."""
        rows = self.conn.execute("SELECT image_path FROM recipes").fetchall()
        count = self.count()
        self.conn.execute("DELETE FROM recipes_fts")
        self.conn.execute("DELETE FROM recipe_tags")
        self.conn.execute("DELETE FROM recipes")
        self.conn.execute("DELETE FROM grocery_items")
        self.conn.commit()
        for row in rows:
            path_text = str(row["image_path"] or "")
            if not path_text:
                continue
            path = Path(path_text)
            if path.is_file():
                try:
                    path.unlink()
                except OSError:
                    pass
        # Remove any leftover files in the images directory.
        try:
            for leftover in images_dir().iterdir():
                if leftover.is_file():
                    leftover.unlink()
        except OSError:
            pass
        clear_list_thumbs()
        return count

    def list_grocery_items(
        self, *, sort: str = "created", ascending: bool = True
    ) -> list[GroceryItem]:
        direction = "ASC" if ascending else "DESC"
        if sort == "text":
            order = f"ORDER BY lower(text) {direction}, id ASC"
        else:
            order = f"ORDER BY created_at {direction}, id ASC"
        rows = self.conn.execute(
            f"SELECT id, text, checked, position, created_at FROM grocery_items {order}"
        ).fetchall()
        return [
            GroceryItem(
                id=int(row["id"]),
                text=str(row["text"]),
                checked=bool(row["checked"]),
                position=int(row["position"]),
                created_at=float(row["created_at"]),
            )
            for row in rows
        ]

    def add_grocery_items(self, texts: list[str]) -> int:
        """Append grocery lines. Returns how many were added."""
        row = self.conn.execute(
            "SELECT COALESCE(MAX(position), -1) AS m FROM grocery_items"
        ).fetchone()
        position = int(row["m"]) + 1 if row else 0
        added = 0
        now = time.time()
        for raw in texts:
            text = " ".join(str(raw).split())
            if not text:
                continue
            self.conn.execute(
                "INSERT INTO grocery_items (text, checked, position, created_at) "
                "VALUES (?, 0, ?, ?)",
                (text, position, now),
            )
            position += 1
            now += 0.001
            added += 1
        if added:
            self.conn.commit()
        return added

    def update_grocery_text(self, item_id: int, text: str) -> bool:
        """Rename a grocery line. Returns True if a row was updated."""
        cleaned = " ".join(str(text).split())
        if not cleaned:
            return False
        cur = self.conn.execute(
            "UPDATE grocery_items SET text = ? WHERE id = ?",
            (cleaned, item_id),
        )
        self.conn.commit()
        return int(cur.rowcount or 0) > 0

    def set_grocery_checked(self, item_id: int, checked: bool) -> None:
        self.conn.execute(
            "UPDATE grocery_items SET checked = ? WHERE id = ?",
            (int(checked), item_id),
        )
        self.conn.commit()

    def clear_grocery_done(self) -> int:
        cur = self.conn.execute("DELETE FROM grocery_items WHERE checked = 1")
        self.conn.commit()
        return int(cur.rowcount or 0)

    def clear_grocery_all(self) -> int:
        cur = self.conn.execute("DELETE FROM grocery_items")
        self.conn.commit()
        return int(cur.rowcount or 0)
