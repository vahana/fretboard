import time
from typing import List, Dict, Optional, Tuple
from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from parser import NoteEvent

try:
    import numpy as np
    import pygame.mixer
    pygame.mixer.pre_init(44100, -16, 1, 1024)
    pygame.mixer.init()
    _AUDIO = True
except Exception:
    _AUDIO = False

TICK_MS = 16
_SAMPLE_RATE = 44100
_TONE_SECS = 2.5
_MAX_TRACKS = 8
_STRINGS = 6


def _make_tone(freq: float) -> "pygame.mixer.Sound":
    t = np.linspace(0, _TONE_SECS, int(_SAMPLE_RATE * _TONE_SECS), endpoint=False)
    env = np.exp(-t * 3.8)
    wave = env * (
        np.sin(2 * np.pi * freq * t)
        + 0.38 * np.sin(4 * np.pi * freq * t)
        + 0.14 * np.sin(6 * np.pi * freq * t)
        + 0.06 * np.sin(8 * np.pi * freq * t)
    )
    peak = np.abs(wave).max()
    if peak:
        wave /= peak
    return pygame.mixer.Sound(buffer=(wave * 32767).astype(np.int16).tobytes())


def _midi_freq(pitch: int) -> float:
    return 440.0 * 2.0 ** ((pitch - 69) / 12.0)


class Player(QObject):
    notes_changed = pyqtSignal(dict)       # {track_idx: {string: fret}}
    position_changed = pyqtSignal(float)
    finished = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.total_ms = 0.0
        self.is_playing = False
        self._speed = 1.0
        self._offset_ms = 0.0
        self._wall_start = 0.0

        self._tracks: Dict[int, List[NoteEvent]] = {}
        self._event_idx: Dict[int, int] = {}
        self._active: Dict[int, Dict[int, Tuple]] = {}
        self._slots: Dict[int, int] = {}
        self._free_slots: List[int] = list(range(_MAX_TRACKS))
        self._tone_cache: Dict[int, "pygame.mixer.Sound"] = {}

        if _AUDIO:
            pygame.mixer.set_num_channels(_MAX_TRACKS * _STRINGS)

        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._tick)

    # ----------------------------------------------------------------- public

    def load_track(self, track_idx: int, events: List[NoteEvent]):
        self._silence_track(track_idx)
        if track_idx not in self._slots:
            if not self._free_slots:
                return
            self._slots[track_idx] = self._free_slots.pop(0)
        self._cache_tones(events)
        self._tracks[track_idx] = events
        now = self._now() if self.is_playing else self._offset_ms
        self._event_idx[track_idx] = _find_idx(events, now)
        self._active[track_idx] = {}
        self._refresh_total()

    def remove_track(self, track_idx: int):
        self._silence_track(track_idx)
        self._tracks.pop(track_idx, None)
        self._event_idx.pop(track_idx, None)
        self._active.pop(track_idx, None)
        slot = self._slots.pop(track_idx, None)
        if slot is not None:
            self._free_slots.append(slot)
            self._free_slots.sort()
        self._refresh_total()

    def clear_tracks(self):
        for idx in list(self._tracks):
            self._silence_track(idx)
        self._tracks.clear()
        self._event_idx.clear()
        self._active.clear()
        self._slots.clear()
        self._free_slots = list(range(_MAX_TRACKS))
        self.total_ms = 0.0

    def play(self):
        if not self._tracks:
            return
        self._wall_start = time.monotonic() * 1000.0
        self.is_playing = True
        self._timer.start()

    def pause(self):
        if not self.is_playing:
            return
        self._offset_ms = self._now()
        self.is_playing = False
        self._timer.stop()
        for idx in self._tracks:
            self._silence_track(idx)

    def reset(self):
        if self.is_playing:
            self.pause()
        self._offset_ms = 0.0
        for idx in self._tracks:
            self._event_idx[idx] = 0
            self._active[idx] = {}
        self.is_playing = False
        self.notes_changed.emit({idx: {} for idx in self._tracks})
        self.position_changed.emit(0.0)

    def seek(self, ms: float):
        was_playing = self.is_playing
        if was_playing:
            self._timer.stop()
        for idx, evs in self._tracks.items():
            self._silence_track(idx)
            self._event_idx[idx] = _find_idx(evs, ms)
            self._active[idx] = {}
        self._offset_ms = ms
        self._wall_start = time.monotonic() * 1000.0
        self.notes_changed.emit({idx: {} for idx in self._tracks})
        if was_playing:
            self._timer.start()

    def set_speed(self, speed: float):
        if self.is_playing:
            self._offset_ms = self._now()
            self._wall_start = time.monotonic() * 1000.0
        self._speed = max(0.1, speed)

    # ----------------------------------------------------------------- private

    def _now(self) -> float:
        return self._offset_ms + (time.monotonic() * 1000.0 - self._wall_start) * self._speed

    def _tick(self):
        now = self._now()
        self.position_changed.emit(now)
        updates: Dict[int, Dict[int, int]] = {}

        for track_idx, events in self._tracks.items():
            idx = self._event_idx.get(track_idx, len(events))
            while idx < len(events) and events[idx].time_ms <= now:
                self._note_on(track_idx, events[idx])
                idx += 1
            self._event_idx[track_idx] = idx

            done = [s for s, (_, off_ms, _ch) in self._active[track_idx].items()
                    if now >= off_ms]
            for s in done:
                _, _, ch = self._active[track_idx].pop(s)
                if ch:
                    ch.fadeout(60)

            updates[track_idx] = {s: fret for s, (fret, _, _) in self._active[track_idx].items()}

        self.notes_changed.emit(updates)

        if self._tracks and all(
            self._event_idx.get(i, 0) >= len(evs) and not self._active.get(i)
            for i, evs in self._tracks.items()
        ):
            self._timer.stop()
            self.is_playing = False
            self.finished.emit()

    def _note_on(self, track_idx: int, ev: NoteEvent):
        ch = self._channel(track_idx, ev.string)
        if _AUDIO and ch and ev.midi_pitch in self._tone_cache:
            if ch.get_busy():
                ch.stop()
            ch.play(self._tone_cache[ev.midi_pitch])
        self._active[track_idx][ev.string] = (ev.fret, ev.time_ms + ev.duration_ms, ch)

    def _channel(self, track_idx: int, string: int) -> Optional["pygame.mixer.Channel"]:
        slot = self._slots.get(track_idx)
        if slot is None or not _AUDIO:
            return None
        return pygame.mixer.Channel(slot * _STRINGS + (string - 1))

    def _silence_track(self, track_idx: int):
        for _, _, ch in self._active.get(track_idx, {}).values():
            if ch:
                ch.fadeout(80)
        self._active[track_idx] = {}

    def _cache_tones(self, events: List[NoteEvent]):
        if _AUDIO:
            for pitch in {e.midi_pitch for e in events}:
                if pitch not in self._tone_cache:
                    self._tone_cache[pitch] = _make_tone(_midi_freq(pitch))

    def _refresh_total(self):
        self.total_ms = max(
            (max((e.time_ms + e.duration_ms for e in evs), default=0.0)
             for evs in self._tracks.values()),
            default=0.0,
        )

    def __del__(self):
        try:
            if _AUDIO:
                pygame.mixer.quit()
        except Exception:
            pass


def _find_idx(events: List[NoteEvent], ms: float) -> int:
    return next((i for i, e in enumerate(events) if e.time_ms >= ms), len(events))
