from __future__ import annotations

import base64
import json
import re
import zipfile
from pathlib import Path
from typing import Any

from pinchbowl.images import save_image_bytes
from pinchbowl.models import Recipe
from pinchbowl.store import Store
from pinchbowl.urls import canonicalize_url, clean_url

_SAFE_FILENAME = re.compile(r"[^\w\s.-]+", re.UNICODE)


def _as_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _write_images(images: list[str], stem: str) -> str:
    """Decode the first Mela base64 image and return a local path, if any."""
    if not images:
        return ""
    raw = images[0]
    try:
        data = base64.b64decode(raw, validate=False)
    except Exception:
        return ""
    return save_image_bytes(data, stem=stem)


def parse_melarecipe(data: dict[str, Any]) -> dict[str, Any]:
    """Map a Mela JSON object to Pinch Bowl upsert kwargs."""
    uid = _as_str(data.get("id")).strip()
    link = _as_str(data.get("link")).strip()
    source_url = clean_url(link) if link.startswith("http") else ""
    if not uid:
        uid = canonicalize_url(source_url) or ""
    images = data.get("images") or []
    if not isinstance(images, list):
        images = []
    image_path = _write_images([str(x) for x in images if x], uid.replace("/", "_") or "recipe")
    categories = data.get("categories") or []
    if not isinstance(categories, list):
        categories = []
    return {
        "uid": uid or None,
        "title": _as_str(data.get("title")).strip() or "Untitled recipe",
        "description": _as_str(data.get("text")),
        "ingredients": _as_str(data.get("ingredients")),
        "instructions": _as_str(data.get("instructions")),
        "notes": _as_str(data.get("notes")),
        "nutrition": _as_str(data.get("nutrition")),
        "yield_text": _as_str(data.get("yield")),
        "prep_time": _as_str(data.get("prepTime")),
        "cook_time": _as_str(data.get("cookTime")),
        "total_time": _as_str(data.get("totalTime")),
        "source": link if not source_url else link.replace("https://", "").replace("http://", ""),
        "source_url": source_url,
        "image_path": image_path,
        "tags": [str(c) for c in categories if str(c).strip()],
        "favorite": bool(data.get("favorite", False)),
        "want_to_cook": bool(data.get("wantToCook", False)),
    }


def recipe_to_melarecipe(recipe: Recipe) -> dict[str, Any]:
    """Serialize a recipe to a Mela ``.melarecipe`` JSON object."""
    link = (recipe.source_url or "").strip()
    if not link and recipe.source.strip():
        source = recipe.source.strip()
        # Keep non-URL labels (e.g. "AI generated", cookbook names) as-is.
        # Prefixing https:// turns them into bogus hosts like "ai generated".
        if source.startswith("http://") or source.startswith("https://"):
            link = source
        elif "." in source.split("/", 1)[0] and " " not in source:
            link = f"https://{source}"
        else:
            link = source
    images: list[str] = []
    if recipe.image_path:
        try:
            data = Path(recipe.image_path).read_bytes()
        except OSError:
            data = b""
        if data:
            images.append(base64.b64encode(data).decode("ascii"))
    return {
        "id": recipe.uid or "",
        "title": recipe.title,
        "text": recipe.description,
        "ingredients": recipe.ingredients,
        "instructions": recipe.instructions,
        "notes": recipe.notes,
        "nutrition": recipe.nutrition,
        "yield": recipe.yield_text,
        "prepTime": recipe.prep_time,
        "cookTime": recipe.cook_time,
        "totalTime": recipe.total_time,
        "link": link,
        "images": images,
        "categories": list(recipe.tags),
        "favorite": bool(recipe.favorite),
        "wantToCook": bool(recipe.want_to_cook),
    }


def _is_recipe_member(name: str) -> bool:
    if name.endswith("/") or name.startswith("__MACOSX"):
        return False
    lower = name.lower()
    return lower.endswith(".melarecipe") or lower.endswith(".json")


def _melarecipe_member_name(recipe: Recipe, used: set[str]) -> str:
    """Build a unique ``.melarecipe`` archive member name from the title."""
    stem = _SAFE_FILENAME.sub("", (recipe.title or "").strip())
    stem = re.sub(r"\s+", " ", stem).strip(" .") or "recipe"
    stem = stem[:80]
    name = f"{stem}.melarecipe"
    if name not in used:
        used.add(name)
        return name
    suffix = (recipe.uid or str(recipe.id)).replace("/", "_")[:24] or "dup"
    name = f"{stem}-{suffix}.melarecipe"
    if name not in used:
        used.add(name)
        return name
    n = 2
    while True:
        name = f"{stem}-{suffix}-{n}.melarecipe"
        if name not in used:
            used.add(name)
            return name
        n += 1


def _melarecipe_json_bytes(recipe: Recipe) -> bytes:
    return (
        json.dumps(recipe_to_melarecipe(recipe), indent=2, ensure_ascii=False) + "\n"
    ).encode("utf-8")


class MelaExportSession:
    """Write recipes to a ``.melarecipes`` zip one at a time for progress UI."""

    def __init__(self, path: Path, recipes: list[Recipe]) -> None:
        self.path = path
        self._recipes = list(recipes)
        self._index = 0
        self._exported = 0
        self._used_names: set[str] = set()
        self._discard = False
        self._closed = False
        path.parent.mkdir(parents=True, exist_ok=True)
        self._zf = zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED)

    @property
    def total(self) -> int:
        return len(self._recipes)

    @property
    def exported(self) -> int:
        return self._exported

    def export_next(self) -> tuple[int, str] | None:
        """Export one recipe. Returns ``(1-based index, title)`` or ``None`` if done."""
        if self._index >= len(self._recipes):
            return None
        recipe = self._recipes[self._index]
        self._index += 1
        name = _melarecipe_member_name(recipe, self._used_names)
        self._zf.writestr(name, _melarecipe_json_bytes(recipe))
        self._exported += 1
        return self._index, recipe.title

    def discard(self) -> None:
        """Mark the archive for deletion on close (cancel / failure)."""
        self._discard = True

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._zf.close()
        finally:
            if self._discard:
                try:
                    self.path.unlink(missing_ok=True)
                except OSError:
                    pass

    def __enter__(self) -> MelaExportSession:
        return self

    def __exit__(self, exc_type: object, *_exc: object) -> None:
        if exc_type is not None:
            self.discard()
        self.close()


def export_melarecipes_archive(path: Path, recipes: list[Recipe]) -> int:
    """Write recipes to a ``.melarecipes`` zip. Returns recipes exported."""
    with MelaExportSession(path, recipes) as session:
        while session.export_next() is not None:
            pass
        return session.exported


def _mela_identity(data: dict[str, Any]) -> tuple[str | None, str]:
    """Return ``(uid or None, source_url)`` without decoding images."""
    uid = _as_str(data.get("id")).strip()
    link = _as_str(data.get("link")).strip()
    source_url = clean_url(link) if link.startswith("http") else ""
    if not uid:
        uid = canonicalize_url(source_url) or ""
    return (uid or None), source_url


class MelaImportSession:
    """Import recipes from a Mela path one at a time for progress UI."""

    def __init__(self, path: Path, store: Store, *, new_only: bool = False) -> None:
        self.path = path
        self.store = store
        self.new_only = new_only
        self._zf: zipfile.ZipFile | None = None
        self._single = False
        self._members: list[str] = []
        self._index = 0
        self._imported = 0
        suffix = path.suffix.lower()
        # .melarecipes is a zip; a lone .melarecipe is JSON (not zip).
        if suffix == ".melarecipe":
            self._single = True
            self._members = [path.name]
        elif suffix == ".melarecipes" or zipfile.is_zipfile(path):
            self._zf = zipfile.ZipFile(path)
            self._members = [n for n in self._zf.namelist() if _is_recipe_member(n)]
        else:
            self._single = True
            self._members = [path.name]

    @property
    def total(self) -> int:
        return len(self._members)

    @property
    def imported(self) -> int:
        return self._imported

    def _read_member(self, name: str) -> Any:
        if self._zf is not None:
            return json.loads(self._zf.read(name).decode("utf-8"))
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _already_present(self, data: dict[str, Any]) -> bool:
        uid, source_url = _mela_identity(data)
        if uid and self.store.find_by_uid(uid) is not None:
            return True
        canonical = canonicalize_url(source_url)
        if canonical and self.store.find_by_canonical_url(canonical) is not None:
            return True
        return False

    def import_next(self) -> tuple[int, str] | None:
        """Import one recipe. Returns ``(1-based index, title)`` or ``None`` if done."""
        while self._index < len(self._members):
            name = self._members[self._index]
            self._index += 1
            data = self._read_member(name)
            if not isinstance(data, dict):
                if self._single:
                    raise ValueError("Invalid .melarecipe file")
                continue
            if self.new_only and self._already_present(data):
                continue
            mapped = parse_melarecipe(data)
            self.store.upsert_recipe(**mapped, update_existing=not self.new_only)
            self._imported += 1
            return self._index, str(mapped["title"])
        return None

    def close(self) -> None:
        if self._zf is not None:
            self._zf.close()
            self._zf = None

    def __enter__(self) -> MelaImportSession:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def import_mela_path(path: Path, store: Store, *, new_only: bool = False) -> int:
    """Import a `.melarecipe` or `.melarecipes` path. Returns recipes imported."""
    with MelaImportSession(path, store, new_only=new_only) as session:
        while session.import_next() is not None:
            pass
        return session.imported


def import_melarecipe_file(path: Path, store: Store, *, new_only: bool = False) -> int:
    """Import a single `.melarecipe` (or JSON) file. Alias of :func:`import_mela_path`."""
    return import_mela_path(path, store, new_only=new_only)


def import_melarecipes_archive(path: Path, store: Store, *, new_only: bool = False) -> int:
    """Import a `.melarecipes` archive. Alias of :func:`import_mela_path`."""
    return import_mela_path(path, store, new_only=new_only)
