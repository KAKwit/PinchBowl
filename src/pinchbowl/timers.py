"""In-process cook-mode countdown timers."""

from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from pinchbowl.paths import data_dir

log = logging.getLogger(__name__)

PRESET_MINUTES = (1, 5, 10, 15)

_ALARM_FILE = "alarm-clock-beep.wav"
_MAX_PLAYS = 3
_GAP_MS = 450

_gsound_ctx = None
_gsound_unavailable = False
_alarm_session: "_AlarmSession | None" = None


def _glib():
    from gi.repository import GLib

    return GLib


def _gio():
    from gi.repository import Gio

    return Gio


@dataclass
class CookTimer:
    id: str
    label: str
    duration_seconds: int
    remaining_seconds: int
    _source_id: int | None = field(default=None, repr=False)


def format_remaining(seconds: int) -> str:
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def label_for_seconds(seconds: int) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds} sec"
    minutes, secs = divmod(seconds, 60)
    if secs == 0:
        return f"{minutes} min"
    return f"{minutes} min {secs} sec"


class TimerManager:
    """Session-only timers driven by ``GLib.timeout_add_seconds``."""

    def __init__(self) -> None:
        self._timers: dict[str, CookTimer] = {}
        self._ids = itertools.count(1)
        self.on_changed: Callable[[], None] | None = None
        self.on_tick: Callable[[], None] | None = None
        self.on_finished: Callable[[CookTimer], None] | None = None

    @property
    def timers(self) -> list[CookTimer]:
        return list(self._timers.values())

    @property
    def count(self) -> int:
        return len(self._timers)

    def add(self, seconds: int, label: str | None = None) -> CookTimer | None:
        GLib = _glib()
        seconds = int(seconds)
        if seconds <= 0:
            return None
        timer_id = str(next(self._ids))
        timer = CookTimer(
            id=timer_id,
            label=label or label_for_seconds(seconds),
            duration_seconds=seconds,
            remaining_seconds=seconds,
        )
        timer._source_id = GLib.timeout_add_seconds(1, self._on_timeout, timer_id)
        self._timers[timer_id] = timer
        self._emit_changed()
        return timer

    def cancel(self, timer_id: str) -> None:
        timer = self._timers.pop(timer_id, None)
        if timer is None:
            return
        self._remove_source(timer)
        self._emit_changed()

    def clear(self) -> None:
        if not self._timers:
            return
        for timer in list(self._timers.values()):
            self._remove_source(timer)
        self._timers.clear()
        self._emit_changed()

    def _remove_source(self, timer: CookTimer) -> None:
        if timer._source_id is not None:
            _glib().source_remove(timer._source_id)
            timer._source_id = None

    def _on_timeout(self, timer_id: str) -> bool:
        GLib = _glib()
        timer = self._timers.get(timer_id)
        if timer is None:
            return GLib.SOURCE_REMOVE
        timer.remaining_seconds -= 1
        if timer.remaining_seconds > 0:
            if self.on_tick:
                self.on_tick()
            return GLib.SOURCE_CONTINUE

        timer._source_id = None
        self._timers.pop(timer_id, None)
        finished = CookTimer(
            id=timer.id,
            label=timer.label,
            duration_seconds=timer.duration_seconds,
            remaining_seconds=0,
        )
        self._emit_changed()
        if self.on_finished:
            try:
                self.on_finished(finished)
            except Exception:
                log.exception("Timer finished handler failed")
        return GLib.SOURCE_REMOVE

    def _emit_changed(self) -> None:
        if self.on_changed:
            self.on_changed()


def alarm_sound_path() -> Path | None:
    path = data_dir() / "sounds" / _ALARM_FILE
    return path if path.is_file() else None


def _gsound() -> tuple[object, object] | None:
    """Return ``(GSound module, Context)`` or None if GSound cannot init."""
    global _gsound_ctx, _gsound_unavailable
    if _gsound_unavailable:
        return None
    if _gsound_ctx is not None:
        return _gsound_ctx
    try:
        import gi

        gi.require_version("GSound", "1.0")
        from gi.repository import GSound

        ctx = GSound.Context()
        ctx.init()
        _gsound_ctx = (GSound, ctx)
        return _gsound_ctx
    except Exception:
        log.debug("GSound unavailable", exc_info=True)
        _gsound_unavailable = True
        return None


class _AlarmSession:
    """Play the bundled alarm up to ``_MAX_PLAYS`` times until stopped."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._cancellable = _gio().Cancellable()
        self._plays_done = 0
        self._gap_source: int | None = None
        self._active = True

    def start(self) -> None:
        self._play_once()

    def stop(self) -> None:
        GLib = _glib()
        if not self._active:
            return
        self._active = False
        if not self._cancellable.is_cancelled():
            self._cancellable.cancel()
        if self._gap_source is not None:
            GLib.source_remove(self._gap_source)
            self._gap_source = None

    def _play_once(self) -> None:
        if not self._active or self._plays_done >= _MAX_PLAYS:
            self._active = False
            return
        pair = _gsound()
        if pair is None:
            self._active = False
            return
        GSound, ctx = pair
        attrs = {
            GSound.ATTR_MEDIA_FILENAME: str(self._path),
            GSound.ATTR_MEDIA_ROLE: "event",
        }
        self._plays_done += 1
        try:
            ctx.play_full(attrs, self._cancellable, self._on_play_done)
        except Exception:
            log.debug("GSound play_full failed", exc_info=True)
            self._active = False

    def _on_play_done(self, source: object, result: object, *_args: object) -> None:
        GLib = _glib()
        try:
            source.play_full_finish(result)  # type: ignore[attr-defined]
        except GLib.Error as exc:
            # Cancelled when the user dismisses the alert.
            if self._cancellable.is_cancelled():
                return
            log.debug("GSound play finished with error: %s", exc)
            self._active = False
            return
        except Exception:
            log.debug("GSound play_full_finish failed", exc_info=True)
            self._active = False
            return

        if (
            not self._active
            or self._cancellable.is_cancelled()
            or self._plays_done >= _MAX_PLAYS
        ):
            self._active = False
            return

        self._gap_source = GLib.timeout_add(_GAP_MS, self._after_gap)

    def _after_gap(self) -> bool:
        GLib = _glib()
        self._gap_source = None
        if self._active and not self._cancellable.is_cancelled():
            self._play_once()
        return GLib.SOURCE_REMOVE


def start_timer_alarm() -> None:
    """Start the bundled alarm (max 3 plays). Replaces any ringing alarm."""
    global _alarm_session
    stop_timer_alarm()
    path = alarm_sound_path()
    if path is None:
        log.warning("Alarm sound missing: %s", data_dir() / "sounds" / _ALARM_FILE)
        return
    session = _AlarmSession(path)
    _alarm_session = session
    session.start()


def stop_timer_alarm() -> None:
    """Stop any in-progress timer alarm immediately."""
    global _alarm_session
    if _alarm_session is None:
        return
    _alarm_session.stop()
    _alarm_session = None
