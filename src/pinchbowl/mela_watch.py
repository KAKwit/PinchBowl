"""Watch a Mela export file and quietly import recipes that are not yet in the library."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from pinchbowl.mela import MelaImportSession
from pinchbowl.store import Store

if TYPE_CHECKING:
    from gi.repository import Gio
    from pinchbowl.settings import Settings

IsBusy = Callable[[], bool]
OnImported = Callable[[int], None]
OnError = Callable[[str], None]


class _WatchSettings(Protocol):
    mela_watch_path: str
    mela_watch_mtime: float
    mela_watch_size: int

    def update(self, notify: bool = True, **values: object) -> None: ...

# Debounce Syncthing-style write bursts / delete+create overwrites.
_DEBOUNCE_MS = 1000


def file_fingerprint(path: Path) -> tuple[float, int] | None:
    """Return ``(mtime, size)`` for ``path``, or ``None`` if unreadable."""
    try:
        st = path.stat()
    except OSError:
        return None
    return (float(st.st_mtime), int(st.st_size))


def fingerprint_unchanged(settings: _WatchSettings, path: Path) -> bool:
    """True when ``path`` matches the last successfully processed fingerprint."""
    fp = file_fingerprint(path)
    if fp is None:
        return False
    mtime, size = fp
    return (
        settings.mela_watch_path == str(path)
        and float(settings.mela_watch_mtime) == mtime
        and int(settings.mela_watch_size) == size
    )


def remember_fingerprint(settings: _WatchSettings, path: Path) -> None:
    """Persist the current fingerprint for ``path`` (and the path itself)."""
    fp = file_fingerprint(path)
    if fp is None:
        return
    mtime, size = fp
    settings.update(
        notify=False,
        mela_watch_path=str(path),
        mela_watch_mtime=mtime,
        mela_watch_size=size,
    )


class MelaWatch:
    """Monitor ``settings.mela_watch_path`` and import new recipes on change."""

    def __init__(
        self,
        settings: Settings,
        store: Store,
        *,
        is_busy: IsBusy,
        on_imported: OnImported,
        on_error: OnError,
    ) -> None:
        import gi

        gi.require_version("Gio", "2.0")
        gi.require_version("GLib", "2.0")
        from gi.repository import Gio, GLib

        self._Gio = Gio
        self._GLib = GLib
        self.settings = settings
        self.store = store
        self._is_busy = is_busy
        self._on_imported = on_imported
        self._on_error = on_error
        self._monitor: Any = None
        self._monitor_file: Any = None
        self._debounce_id: int | None = None
        self._import_gen = 0
        self._importing = False
        self._stopped = False

    @property
    def importing(self) -> bool:
        return self._importing

    def restart(self) -> None:
        """Re-arm the file monitor from the current settings path."""
        self._cancel_debounce()
        self._stop_monitor()
        if self._stopped:
            return
        raw = (self.settings.mela_watch_path or "").strip()
        if not raw:
            return
        path = Path(raw)
        gfile = self._Gio.File.new_for_path(str(path))
        try:
            monitor = gfile.monitor_file(self._Gio.FileMonitorFlags.NONE, None)
        except self._GLib.Error:
            # Parent may not exist yet (Syncthing not synced); retry on next check.
            return
        monitor.connect("changed", self._on_changed)
        self._monitor = monitor
        self._monitor_file = gfile

    def stop(self) -> None:
        self._stopped = True
        self._import_gen += 1
        self._cancel_debounce()
        self._stop_monitor()

    def check_now(self) -> None:
        """Import if the watched file exists and its fingerprint changed."""
        if self._stopped:
            return
        self._cancel_debounce()
        self._run_import()

    def _stop_monitor(self) -> None:
        if self._monitor is not None:
            try:
                self._monitor.cancel()
            except Exception:
                pass
        self._monitor = None
        self._monitor_file = None

    def _cancel_debounce(self) -> None:
        if self._debounce_id is not None:
            self._GLib.source_remove(self._debounce_id)
            self._debounce_id = None

    def _on_changed(
        self,
        _monitor: Gio.FileMonitor,
        _file: Gio.File,
        _other: Gio.File | None,
        event: Gio.FileMonitorEvent,
    ) -> None:
        if self._stopped:
            return
        Gio = self._Gio
        # Re-arm after Syncthing-style delete + create of the same path.
        if event in (
            Gio.FileMonitorEvent.DELETED,
            Gio.FileMonitorEvent.MOVED_OUT,
            Gio.FileMonitorEvent.RENAMED,
        ):
            self.restart()
        if event not in (
            Gio.FileMonitorEvent.CHANGES_DONE_HINT,
            Gio.FileMonitorEvent.CREATED,
            Gio.FileMonitorEvent.CHANGED,
            Gio.FileMonitorEvent.ATTRIBUTE_CHANGED,
            Gio.FileMonitorEvent.MOVED_IN,
            Gio.FileMonitorEvent.RENAMED,
        ):
            return
        self._cancel_debounce()
        self._debounce_id = self._GLib.timeout_add(_DEBOUNCE_MS, self._debounce_fire)

    def _debounce_fire(self) -> bool:
        self._debounce_id = None
        self._run_import()
        return False

    def _run_import(self) -> None:
        if self._stopped or self._importing or self._is_busy():
            return
        raw = (self.settings.mela_watch_path or "").strip()
        if not raw:
            return
        path = Path(raw)
        if not path.is_file():
            # File may reappear after a replace; keep monitoring.
            self.restart()
            return
        if fingerprint_unchanged(self.settings, path):
            return

        try:
            session = MelaImportSession(path, self.store, new_only=True)
        except Exception as exc:
            self._on_error(f"Mela watch import failed: {exc}")
            return
        if session.total == 0:
            session.close()
            remember_fingerprint(self.settings, path)
            return

        self._importing = True
        self._import_gen += 1
        gen = self._import_gen

        def finish(*, ok: bool, message: str | None = None) -> None:
            session.close()
            self._importing = False
            if self._stopped or gen != self._import_gen:
                return
            if ok:
                remember_fingerprint(self.settings, path)
                n = session.imported
                if n:
                    self._on_imported(n)
            elif message:
                self._on_error(message)
            self.restart()

        def step() -> bool:
            if self._stopped or gen != self._import_gen:
                session.close()
                self._importing = False
                return False
            try:
                result = session.import_next()
            except Exception as exc:
                finish(ok=False, message=f"Mela watch import failed: {exc}")
                return False
            if result is None:
                finish(ok=True)
                return False
            return True

        self._GLib.idle_add(step)
