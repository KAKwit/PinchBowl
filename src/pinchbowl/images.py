from __future__ import annotations

import base64
import hashlib
import re
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen

from pinchbowl.paths import images_dir, thumbs_dir, version
from pinchbowl.urls import canonicalize_url

MAX_BYTES = 8 * 1024 * 1024
# Square list thumbs (2× the 56px row widget for HiDPI).
LIST_THUMB_SIDE = 112
_SAFE_STEM = re.compile(r"[^a-zA-Z0-9._-]+")
_MIME_BY_EXT = {
    "jpg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
}
_EXT_BY_MIME = {v: k for k, v in _MIME_BY_EXT.items()}
_EXT_BY_MIME["image/jpg"] = "jpg"

# In-memory Gdk.Texture cache keyed by disk-thumb identity (path + mtime + size).
_texture_cache: dict[str, Any] = {}
# Pending async thumb jobs: source path -> callbacks.
_thumb_queue: OrderedDict[str, list[Callable[[str, Any], None]]] = OrderedDict()
_thumb_idle_scheduled = False


def sniff_image(data: bytes) -> str | None:
    if len(data) < 12:
        return None
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def absolute_image_url(image_url: str, page_url: str = "") -> str:
    raw = (image_url or "").strip()
    if not raw:
        return ""
    if raw.startswith("//"):
        scheme = urlsplit(page_url).scheme or "https"
        return f"{scheme}:{raw}"
    if page_url and not urlsplit(raw).scheme:
        return urljoin(page_url, raw)
    return raw


def _store_image_bytes(data: bytes, stem: str = "") -> str:
    """Write sniffed image bytes into the images dir; return path or \"\"."""
    if len(data) > MAX_BYTES:
        return ""
    ext = sniff_image(data)
    if ext is None:
        return ""
    digest = hashlib.sha256(data).hexdigest()[:16]
    label = _SAFE_STEM.sub("_", stem).strip("._")[:48] or "recipe"
    out = images_dir() / f"{label}-{digest}.{ext}"
    if not out.exists():
        out.write_bytes(data)
    # Warm the list thumb so the next library paint is cheap.
    try:
        ensure_list_thumb(str(out))
    except Exception:
        pass
    return str(out)


def _thumb_cache_key(source: Path) -> str:
    """Stable id for a stored image path (content-addressed filenames in images/)."""
    try:
        resolved = str(source.resolve())
    except OSError:
        resolved = str(source)
    return hashlib.sha256(resolved.encode()).hexdigest()[:20]


def list_thumb_path(image_path: str | Path) -> Path | None:
    """Return the on-disk thumb path for ``image_path``, or None if blank."""
    source = Path(image_path)
    if not str(image_path).strip():
        return None
    return thumbs_dir() / f"{_thumb_cache_key(source)}.jpg"


def cover_crop_pixbuf(
    src: Any, width: int, height: int, *, bilinear: Any
) -> Any | None:
    """Scale and center-crop a pixbuf to exactly ``width``×``height``."""
    sw, sh = src.get_width(), src.get_height()
    if sw <= 0 or sh <= 0 or width <= 0 or height <= 0:
        return None
    scale = max(width / sw, height / sh)
    scaled = src.scale_simple(
        max(1, int(round(sw * scale))),
        max(1, int(round(sh * scale))),
        bilinear,
    )
    x = max(0, (scaled.get_width() - width) // 2)
    y = max(0, (scaled.get_height() - height) // 2)
    return scaled.new_subpixbuf(
        x, y, min(width, scaled.get_width() - x), min(height, scaled.get_height() - y)
    )


def _cover_square_pixbuf(src: Any, side: int, *, bilinear: Any) -> Any:
    """Scale-and-crop a pixbuf to an exact ``side``×``side`` square."""
    return cover_crop_pixbuf(src, side, side, bilinear=bilinear)


def ensure_list_thumb(image_path: str | Path) -> str:
    """Create (if needed) a small JPEG list thumb; return its path or \"\"."""
    source = Path(image_path)
    if not source.is_file():
        return ""
    thumb = list_thumb_path(source)
    if thumb is None:
        return ""
    key = thumb.stem
    if thumb.is_file():
        return str(thumb)

    try:
        import gi

        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf
    except Exception:
        return ""

    tmp = thumb.with_suffix(".jpg.tmp")
    try:
        src = GdkPixbuf.Pixbuf.new_from_file(str(source))
        pixbuf = _cover_square_pixbuf(
            src, LIST_THUMB_SIDE, bilinear=GdkPixbuf.InterpType.BILINEAR
        )
        if pixbuf is None:
            return ""
        pixbuf.savev(str(tmp), "jpeg", ["quality"], ["82"])
        tmp.replace(thumb)
    except Exception:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        return ""
    _texture_cache.pop(key, None)
    return str(thumb)


def get_list_thumb_texture(image_path: str | Path) -> Any | None:
    """Return a cached ``Gdk.Texture`` for the list thumb, or None if not ready."""
    thumb = list_thumb_path(image_path)
    if thumb is None or not thumb.is_file():
        return None
    key = thumb.stem
    cached = _texture_cache.get(key)
    if cached is not None:
        return cached
    try:
        import gi

        gi.require_version("Gdk", "4.0")
        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import Gdk, GdkPixbuf

        pixbuf = GdkPixbuf.Pixbuf.new_from_file(str(thumb))
        texture = Gdk.Texture.new_for_pixbuf(pixbuf)
    except Exception:
        return None
    _texture_cache[key] = texture
    return texture


def request_list_thumb(
    image_path: str,
    callback: Callable[[str, Any], None],
) -> None:
    """Invoke ``callback(path, texture)`` now if cached, else after idle generate."""
    path = str(image_path or "")
    if not path:
        callback(path, None)
        return
    texture = get_list_thumb_texture(path)
    if texture is not None:
        callback(path, texture)
        return

    global _thumb_idle_scheduled
    if path in _thumb_queue:
        _thumb_queue[path].append(callback)
    else:
        _thumb_queue[path] = [callback]
    if _thumb_idle_scheduled:
        return
    try:
        from gi.repository import GLib
    except Exception:
        ensure_list_thumb(path)
        texture = get_list_thumb_texture(path)
        for cb in _thumb_queue.pop(path, []):
            cb(path, texture)
        return
    _thumb_idle_scheduled = True
    GLib.idle_add(_process_thumb_queue)


def _process_thumb_queue() -> bool:
    """Idle handler: build one missing thumb per tick so the UI stays responsive."""
    global _thumb_idle_scheduled
    if not _thumb_queue:
        _thumb_idle_scheduled = False
        return False
    path, callbacks = _thumb_queue.popitem(last=False)
    ensure_list_thumb(path)
    texture = get_list_thumb_texture(path)
    for cb in callbacks:
        try:
            cb(path, texture)
        except Exception:
            pass
    if not _thumb_queue:
        _thumb_idle_scheduled = False
        return False
    return True


def forget_list_thumb(image_path: str | Path) -> None:
    """Drop memory + disk thumb for a source image (e.g. on delete/replace)."""
    if not str(image_path).strip():
        return
    source = Path(image_path)
    key = _thumb_cache_key(source)
    _texture_cache.pop(key, None)
    thumb = thumbs_dir() / f"{key}.jpg"
    try:
        if thumb.is_file():
            thumb.unlink()
    except OSError:
        pass
    _thumb_queue.pop(str(image_path), None)


def clear_list_thumbs() -> None:
    """Remove every list thumb (memory + disk). Used when clearing the library."""
    global _thumb_idle_scheduled
    _texture_cache.clear()
    _thumb_queue.clear()
    _thumb_idle_scheduled = False
    try:
        for entry in thumbs_dir().iterdir():
            if entry.is_file():
                try:
                    entry.unlink()
                except OSError:
                    pass
    except OSError:
        pass


def save_image_bytes(data: bytes, *, stem: str = "") -> str:
    """Persist clipboard or downloaded image bytes; return local path or \"\"."""
    return _store_image_bytes(data, stem)


def import_image_file(path: str | Path, *, stem: str = "") -> str:
    """Copy a local image file into the images dir; return path or \"\"."""
    file_path = Path(path)
    if not file_path.is_file():
        return ""
    try:
        data = file_path.read_bytes()
    except OSError:
        return ""
    if len(data) > MAX_BYTES + 1:
        return ""
    label = stem or file_path.stem
    return _store_image_bytes(data, label)


def download_image(image_url: str, *, page_url: str = "", stem: str = "") -> str:
    """Fetch a remote recipe image and return its local path, or \"\"."""
    url = absolute_image_url(image_url, page_url)
    if not url.startswith("http://") and not url.startswith("https://"):
        return ""
    req = Request(
        url,
        headers={"User-Agent": f"PinchBowl/{version()} (GNOME recipe app)"},
    )
    with urlopen(req, timeout=20) as resp:
        data = resp.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        return ""
    return _store_image_bytes(data, stem)


def image_data_uri(path: str | Path) -> str:
    """Encode a local image as a ``data:`` URI, or \"\" if unreadable."""
    file_path = Path(path)
    if not file_path.is_file():
        return ""
    try:
        data = file_path.read_bytes()
    except OSError:
        return ""
    if len(data) > MAX_BYTES:
        return ""
    ext = sniff_image(data)
    if ext is None:
        return ""
    mime = _MIME_BY_EXT.get(ext, "application/octet-stream")
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def save_data_uri_image(uri: str, *, stem: str = "") -> str:
    """Decode a ``data:image/…;base64,…`` URI into the images dir."""
    raw = (uri or "").strip()
    if not raw.lower().startswith("data:"):
        return ""
    try:
        header, payload = raw.split(",", 1)
    except ValueError:
        return ""
    meta = header[5:]  # strip "data:"
    parts = meta.split(";")
    mime = (parts[0] or "").lower() or "application/octet-stream"
    is_base64 = any(p.lower() == "base64" for p in parts[1:])
    try:
        data = base64.b64decode(payload) if is_base64 else payload.encode("utf-8")
    except Exception:
        return ""
    if sniff_image(data) is None:
        # Fall back to MIME hint when sniff fails on truncated payloads.
        if mime not in _EXT_BY_MIME:
            return ""
    return _store_image_bytes(data, stem)


def resolve_remote_image(image_url: str, *, page_url: str = "", stem: str = "") -> str:
    """Resolve an http(s) or ``data:`` image URL to a local path, or \"\"."""
    raw = (image_url or "").strip()
    if not raw:
        return ""
    if raw.lower().startswith("data:"):
        return save_data_uri_image(raw, stem=stem)
    return download_image(raw, page_url=page_url, stem=stem)


def apply_mapped_image(mapped: dict, *, default_page_url: str = "") -> dict:
    """Pop ``_image_url`` / ``_page_url`` and set ``image_path`` on a mapped recipe dict."""
    out = dict(mapped)
    image_url = str(out.pop("_image_url", "") or "")
    page_url = str(out.pop("_page_url", "") or default_page_url or "")
    if not image_url:
        out["image_path"] = out.get("image_path") or ""
        return out
    stem = canonicalize_url(page_url) or "recipe"
    out["image_path"] = resolve_remote_image(image_url, page_url=page_url, stem=stem)
    return out
