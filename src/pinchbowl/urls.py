from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Common tracking / analytics query parameters to strip on import.
_TRACKING_KEYS = {
    "fbclid",
    "gclid",
    "gbraid",
    "wbraid",
    "mc_cid",
    "mc_eid",
    "msclkid",
    "igshid",
    "si",
    "feature",
    "ref",
    "ref_src",
    "ref_url",
    "spm",
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "utm_id",
    "utm_name",
    "utm_reader",
    "utm_place",
    "utm_pubreferrer",
    "utm_swu",
    "utm_viz_id",
    "yclid",
    "_hsenc",
    "_hsmi",
    "hsa_cam",
    "hsa_grp",
    "hsa_mt",
    "hsa_src",
    "hsa_ad",
    "hsa_acc",
    "hsa_net",
    "hsa_kw",
    "hsa_tgt",
    "hsa_ver",
}


def _split_clean(url: str) -> tuple[str, str, str, str] | None:
    raw = (url or "").strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = "https://" + raw
    parts = urlsplit(raw)
    scheme = (parts.scheme or "https").lower()
    host = (parts.hostname or "").lower().removeprefix("www.")
    if not host:
        return None
    netloc = host
    if parts.port:
        netloc = f"{host}:{parts.port}"
    path = parts.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    kept = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in _TRACKING_KEYS and not k.lower().startswith("utm_")
    ]
    query = urlencode(kept, doseq=True)
    return scheme, netloc, path, query


def canonicalize_url(url: str) -> str:
    """Normalize a recipe URL for duplicate detection (Mela-style: no scheme)."""
    parts = _split_clean(url)
    if parts is None:
        return ""
    _scheme, netloc, path, query = parts
    identity = netloc + path
    if query:
        identity += "?" + query
    return identity


def clean_url(url: str) -> str:
    """Return a browseable URL with tracking parameters removed."""
    parts = _split_clean(url)
    if parts is None:
        return ""
    scheme, netloc, path, query = parts
    return urlunsplit((scheme, netloc, path, query, ""))


def openable_url(source_url: str, source: str = "") -> str:
    """Prefer an explicit URL; fall back if `source` looks like one."""
    for candidate in (source_url, source):
        text = (candidate or "").strip()
        if text.startswith("http://") or text.startswith("https://"):
            return clean_url(text)
        if "." in text and " " not in text and "/" in text:
            return clean_url(text)
    return ""


def source_display(source_url: str = "", source: str = "") -> tuple[str, bool]:
    """Return ``(label, is_website)`` for UI chrome.

    Web sources show the bare host (e.g. ``recipetineats.com``). Non-URL
    labels such as a cookbook name are returned unchanged with ``False``.
    """
    url = (source_url or "").strip()
    if url:
        parts = _split_clean(url)
        if parts is not None:
            host = parts[1].split(":")[0]
            if host:
                return host, True

    text = (source or "").strip()
    if not text:
        return "", False

    head = text.split("/", 1)[0]
    if " " not in text and "." in head:
        parts = _split_clean(text)
        if parts is not None:
            host = parts[1].split(":")[0]
            if host:
                return host, True

    return text, False
