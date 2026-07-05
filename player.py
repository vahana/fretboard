import time
from typing import List, Dict, Optional
from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from parser import NoteEvent

try:
    import numpy as np
    import pygame.mixer
    pygame.mixer.pre_init(44100, -16, 1, 1024)
    pygame.mixer.init()
    pygame.mixer.set_num_channels(16)
    _AUDIO = True
except Exception:
    _AUDIO = False

TICK_MS = 16
_SAMPLE_RATE = 44100
_TONE_SECS = 2.5


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
    notes_changed = pyqtSignal(dict)
    position_changed = pyqtSignal(float)
    finished = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.events: List[NoteEvent] = []
        self.is_playing = False
        self._speed = 1.0
        self._offset_ms = 0.0
        self._wall_start = 0.0
        self._event_idx = 0
        self._active: Dict[int, tuple] = {}   # string → (fret, off_ms, channel)
        self._tone_cache: Dict[int, "pygame.mixer.Sound"] = {}
        # one dedicated mixer channel per guitar string (strings 1-6)
        self._channels: Dict[int, Optional["pygame.mixer.Channel"]] = {}
        if _AUDIO:
            for s in range(1, 7):
                self._channels[s] = pygame.mixer.Channel(s - 1)

        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._tick)

    # ----------------------------------------------------------------- public

    def load(self, events: List[NoteEvent], _tempo: float):
        self._all_notes_off()
        self._cache_tones(events)
        self.events = events
        self.total_ms = max((e.time_ms + e.duration_ms for e in events), default=0.0)
        self.reset()

    def switch_events(self, events: List[NoteEvent]):
        """Replace event list at the current playback position without stopping."""
        now = self._now() if self.is_playing else self._offset_ms
        self._all_notes_off()
        self._cache_tones(events)
        self.events = events
        self.total_ms = max((e.time_ms + e.duration_ms for e in events), default=0.0)
        self._event_idx = next(
            (i for i, e in enumerate(events) if e.time_ms >= now),
            len(events),
        )
        self._offset_ms = now
        if self.is_playing:
            self._wall_start = time.monotonic() * 1000.0
        self._active = {}
        self.notes_changed.emit({})

    def play(self):
        if not self.events:
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
        self._all_notes_off()

    def reset(self):
        if self.is_playing:
            self.pause()
        self._offset_ms = 0.0
        self._event_idx = 0
        self._active = {}
        self.is_playing = False
        self.notes_changed.emit({})
        self.position_changed.emit(0.0)

    def seek(self, ms: float):
        was_playing = self.is_playing
        if was_playing:
            self._timer.stop()
        self._all_notes_off()
        self._offset_ms = ms
        self._wall_start = time.monotonic() * 1000.0
        self._event_idx = next(
            (i for i, e in enumerate(self.events) if e.time_ms >= ms),
            len(self.events),
        )
        self._active = {}
        self.notes_changed.emit({})
        if was_playing:
            self._timer.start()

    # ----------------------------------------------------------------- private

    def set_speed(self, speed: float):
        if self.is_playing:
            self._offset_ms = self._now()
            self._wall_start = time.monotonic() * 1000.0
        self._speed = max(0.1, speed)

    def _now(self) -> float:
        return self._offset_ms + (time.monotonic() * 1000.0 - self._wall_start) * self._speed

    def _tick(self):
        now = self._now()
        self.position_changed.emit(now)

        while (self._event_idx < len(self.events)
               and self.events[self._event_idx].time_ms <= now):
            self._note_on(self.events[self._event_idx])
            self._event_idx += 1

        done = []
        for string, (fret, off_ms, ch) in self._active.items():
            if now >= off_ms:
                if ch:
                    ch.fadeout(60)
                done.append(string)
        for s in done:
            del self._active[s]

        self.notes_changed.emit({s: fret for s, (fret, _, _) in self._active.items()})

        if self._event_idx >= len(self.events) and not self._active:
            self._timer.stop()
            self.is_playing = False
            self.finished.emit()

    def _note_on(self, ev: NoteEvent):
        ch = self._channels.get(ev.string)
        if _AUDIO and ch and ev.midi_pitch in self._tone_cache:
            if ch.get_busy():
                ch.stop()
            ch.play(self._tone_cache[ev.midi_pitch])
        self._active[ev.string] = (ev.fret, ev.time_ms + ev.duration_ms, ch)

    def _cache_tones(self, events: List[NoteEvent]):
        if _AUDIO:
            for pitch in {e.midi_pitch for e in events}:
                if pitch not in self._tone_cache:
                    self._tone_cache[pitch] = _make_tone(_midi_freq(pitch))

    def _all_notes_off(self):
        if _AUDIO:
            for ch in self._channels.values():
                if ch:
                    ch.fadeout(80)
        self._active = {}
