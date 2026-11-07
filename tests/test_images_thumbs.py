from __future__ import annotations

from pathlib import Path

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf

from pinchbowl import images
from pinchbowl.paths import thumbs_dir


def _solid_png(path: Path, width: int = 400, height: int = 300) -> None:
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, width, height)
    pixbuf.fill(0xCC4422FF)
    pixbuf.savev(str(path), "png", [], [])


def test_ensure_list_thumb_creates_small_jpeg(tmp_xdg: Path) -> None:
    src = tmp_xdg / "hero.png"
    _solid_png(src)
    thumb = images.ensure_list_thumb(src)
    assert thumb
    thumb_path = Path(thumb)
    assert thumb_path.is_file()
    assert thumb_path.stat().st_size < src.stat().st_size
    assert thumb_path.parent == thumbs_dir()
    loaded = GdkPixbuf.Pixbuf.new_from_file(thumb)
    assert loaded.get_width() == images.LIST_THUMB_SIDE
    assert loaded.get_height() == images.LIST_THUMB_SIDE


def test_ensure_list_thumb_is_idempotent(tmp_xdg: Path) -> None:
    src = tmp_xdg / "dish.png"
    _solid_png(src)
    first = images.ensure_list_thumb(src)
    mtime = Path(first).stat().st_mtime_ns
    second = images.ensure_list_thumb(src)
    assert first == second
    assert Path(second).stat().st_mtime_ns == mtime


def test_forget_list_thumb_removes_disk_file(tmp_xdg: Path) -> None:
    src = tmp_xdg / "gone.png"
    _solid_png(src)
    thumb = Path(images.ensure_list_thumb(src))
    assert thumb.is_file()
    images.forget_list_thumb(src)
    assert not thumb.exists()


def test_clear_list_thumbs(tmp_xdg: Path) -> None:
    src = tmp_xdg / "clear-me.png"
    _solid_png(src)
    images.ensure_list_thumb(src)
    assert any(thumbs_dir().iterdir())
    images.clear_list_thumbs()
    assert list(thumbs_dir().iterdir()) == []


def test_save_image_bytes_warms_thumb(tmp_xdg: Path) -> None:
    src = tmp_xdg / "upload.png"
    _solid_png(src)
    stored = images.save_image_bytes(src.read_bytes(), stem="upload")
    assert stored
    thumb = images.list_thumb_path(stored)
    assert thumb is not None and thumb.is_file()


def test_store_delete_removes_thumb(store, tmp_xdg: Path) -> None:
    src = tmp_xdg / "linked.png"
    _solid_png(src)
    stored = images.save_image_bytes(src.read_bytes(), stem="linked")
    recipe = store.upsert_recipe(title="T", ingredients="x", image_path=stored)
    thumb = images.list_thumb_path(stored)
    assert thumb is not None and thumb.is_file()
    store.delete(recipe.id)
    assert not Path(stored).exists()
    assert not thumb.exists()
