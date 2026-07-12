import math
import time
from typing import Dict, List, Optional, Tuple
from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from parser import NoteEvent, NoteEffects, BendPoint, BeatEvent

TICK_MS = 16
_STRINGS = 6
_MAX_PG_TRACKS = 8
_SAMPLE_RATE = 44100
_TONE_SECS = 2.5
_VIBRATO_HZ = 5.5
_VIBRATO_DEPTH = 0.4

# ── pygame / numpy backend ────────────────────────────────────────────────────
try:
    import numpy as np
    import pygame.mixer
    pygame.mixer.pre_init(44100, -16, 1, 1024)
    pygame.mixer.init()
    _PG_AUDIO = True
except Exception:
    _PG_AUDIO = False


def _interp_bend_vec(points: List[BendPoint], t_arr: "np.ndarray") -> "np.ndarray":
    result = np.zeros(len(t_arr))
    if not points:
        return result
    result[:] = points[-1].value
    result[t_arr <= points[0].pos] = points[0].value
    for i in range(len(points) - 1):
        p0, p1 = points[i], points[i + 1]
        span = p1.pos - p0.pos
        if span <= 0:
            continue
        mask = (t_arr > p0.pos) & (t_arr <= p1.pos)
        frac = (t_arr[mask] - p0.pos) / span
        result[mask] = p0.value + frac * (p1.value - p0.value)
    return result


def _make_fx_tone(base_pitch: int, duration_ms: float, fx: "NoteEffects") -> "pygame.mixer.Sound":
    duration_s = min(duration_ms / 1000.0, _TONE_SECS)
    n = max(int(_SAMPLE_RATE * duration_s), 1)
    t = np.linspace(0, duration_s, n, endpoint=False)
    t_norm = t / duration_s

    semitones = np.zeros(n)
    if fx.bend:
        semitones += _interp_bend_vec(fx.bend, t_norm)
    if fx.slide_in:
        mask = t_norm < 0.25
        semitones[mask] += fx.slide_in * (1.0 - t_norm[mask] / 0.25)
    if fx.slide_out:
        mask = t_norm > 0.7
        semitones[mask] += fx.slide_out * ((t_norm[mask] - 0.7) / 0.3)
    if fx.vibrato:
        semitones += np.sin(2 * np.pi * _VIBRATO_HZ * t) * _VIBRATO_DEPTH

    freqs = _midi_freq(base_pitch) * (2.0 ** (semitones / 12.0))
    phase = np.cumsum(2 * np.pi * freqs / _SAMPLE_RATE)

    env = np.exp(-t * 3.8)
    wave = env * (
        np.sin(phase)
        + 0.38 * np.sin(2 * phase)
        + 0.14 * np.sin(3 * phase)
        + 0.06 * np.sin(4 * phase)
    )
    peak = np.abs(wave).max()
    if peak:
        wave /= peak
    return pygame.mixer.Sound(buffer=(wave * 8000).astype(np.int16).tobytes())


def _make_click(freq: float, duration: float = 0.022) -> "pygame.mixer.Sound":
    n = int(_SAMPLE_RATE * duration)
    t = np.linspace(0, duration, n, endpoint=False)
    env = np.exp(-t * 160)
    wave = env * np.sin(2 * np.pi * freq * t)
    peak = np.abs(wave).max()
    if peak:
        wave /= peak
    return pygame.mixer.Sound(buffer=(wave * 11000).astype(np.int16).tobytes())


def _make_dead_tone() -> "pygame.mixer.Sound":
    n = int(_SAMPLE_RATE * 0.08)
    t = np.linspace(0, 0.08, n, endpoint=False)
    env = np.exp(-t * 60)
    wave = env * (
        np.sin(2 * np.pi * 180 * t)
        + 0.5 * np.sin(2 * np.pi * 320 * t)
    )
    peak = np.abs(wave).max()
    if peak:
        wave /= peak
    return pygame.mixer.Sound(buffer=(wave * 6000).astype(np.int16).tobytes())


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
    return pygame.mixer.Sound(buffer=(wave * 8000).astype(np.int16).tobytes())


def _midi_freq(pitch: int) -> float:
    return 440.0 * 2.0 ** ((pitch - 69) / 12.0)


def _interp_bend(points: List[BendPoint], t: float) -> float:
    if not points:
        return 0.0
    if t <= points[0].pos:
        return points[0].value
    if t >= points[-1].pos:
        return points[-1].value
    for i in range(len(points) - 1):
        p0, p1 = points[i], points[i + 1]
        if p0.pos <= t <= p1.pos:
            span = p1.pos - p0.pos
            frac = (t - p0.pos) / span if span else 1.0
            return p0.value + frac * (p1.value - p0.value)
    return points[-1].value


def _compute_pitch_offset(fx: Optional[NoteEffects], now: float, start_ms: float, off_ms: float) -> float:
    if fx is None:
        return 0.0
    duration = off_ms - start_ms
    t = (now - start_ms) / duration if duration > 0 else 1.0
    t = max(0.0, min(1.0, t))

    semitones = 0.0

    if fx.bend:
        semitones += _interp_bend(fx.bend, t)

    if fx.slide_in and t < 0.25:
        semitones += fx.slide_in * (1.0 - t / 0.25)

    if fx.slide_out and t > 0.7:
        semitones += fx.slide_out * ((t - 0.7) / 0.3)

    if fx.vibrato:
        semitones += math.sin(2 * math.pi * _VIBRATO_HZ * now / 1000.0) * _VIBRATO_DEPTH

    return semitones


# _active[track_idx][string] = (fret, off_ms, midi_pitch, start_ms, effects)
_ActiveNote = Tuple[int, float, int, float, Optional[NoteEffects]]


class Player(QObject):
    notes_changed = pyqtSignal(dict)
    position_changed = pyqtSignal(float)
    finished = pyqtSignal()
    beat_changed = pyqtSignal(int, int)  # beat_num, beats_per_bar

    def __init__(self, parent=None):
        super().__init__(parent)
        self.total_ms = 0.0
        self.is_playing = False
        self._speed = 1.0
        self._offset_ms = 0.0
        self._wall_start = 0.0

        self.pitch_offset: int = 0
        self._muted: set = set()

        self._tracks: Dict[int, List[NoteEvent]] = {}
        self._event_idx: Dict[int, int] = {}
        self._active: Dict[int, Dict[int, _ActiveNote]] = {}

        self._pg_slot: Dict[int, int] = {}
        self._free_pg: List[int] = list(range(_MAX_PG_TRACKS))
        self._tone_cache: Dict[int, "pygame.mixer.Sound"] = {}
        self._dead_tone: "pygame.mixer.Sound | None" = None
        self._metro_events: List[BeatEvent] = []
        self._metro_idx: int = 0
        self._metro_on: bool = False
        if _PG_AUDIO:
            pygame.mixer.set_num_channels(_MAX_PG_TRACKS * _STRINGS + 1)
            self._dead_tone = _make_dead_tone()
            self._click_hi = _make_click(1400)
            self._click_lo = _make_click(900)
            self._click_ch = pygame.mixer.Channel(_MAX_PG_TRACKS * _STRINGS)

        self._loop_start_ms: Optional[float] = None
        self._loop_end_ms: Optional[float] = None

        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._tick)

    # ----------------------------------------------------------------- tracks

    def load_track(self, track_idx: int, events: List[NoteEvent]):
        self._silence_track(track_idx)
        if track_idx not in self._pg_slot and self._free_pg:
            self._pg_slot[track_idx] = self._free_pg.pop(0)
        if _PG_AUDIO:
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
        slot = self._pg_slot.pop(track_idx, None)
        if slot is not None:
            self._free_pg.append(slot)
            self._free_pg.sort()
        self._refresh_total()

    def clear_tracks(self):
        for idx in list(self._tracks):
            self._silence_track(idx)
        self._tracks.clear()
        self._event_idx.clear()
        self._active.clear()
        self._pg_slot.clear()
        self._free_pg = list(range(_MAX_PG_TRACKS))
        self._muted.clear()
        self.total_ms = 0.0

    def mute_track(self, track_idx: int, muted: bool):
        if muted:
            self._muted.add(track_idx)
            self._silence_track(track_idx)
        else:
            self._muted.discard(track_idx)

    def load_metronome(self, events: List[BeatEvent]):
        self._metro_events = events
        now = self._now() if self.is_playing else self._offset_ms
        self._metro_idx = _find_idx(events, now)

    def set_metronome(self, on: bool):
        self._metro_on = on

    # ----------------------------------------------------------------- playback

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
        self._metro_idx = 0
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
        self._metro_idx = _find_idx(self._metro_events, ms)
        self._offset_ms = ms
        self._wall_start = time.monotonic() * 1000.0
        self.notes_changed.emit({idx: {} for idx in self._tracks})
        self.position_changed.emit(ms)
        if was_playing:
            self._timer.start()

    def set_pitch_offset(self, semitones: int):
        self.pitch_offset = max(-24, min(24, semitones))

    def set_loop(self, start_ms: float, end_ms: float):
        self._loop_start_ms = start_ms
        self._loop_end_ms = end_ms

    def clear_loop(self):
        self._loop_start_ms = None
        self._loop_end_ms = None

    def set_speed(self, speed: float):
        if self.is_playing:
            self._offset_ms = self._now()
            self._wall_start = time.monotonic() * 1000.0
        self._speed = max(0.1, speed)

    # ----------------------------------------------------------------- tick

    def _now(self) -> float:
        return self._offset_ms + (time.monotonic() * 1000.0 - self._wall_start) * self._speed

    def _tick(self):
        now = self._now()
        if self._loop_end_ms is not None and now >= self._loop_end_ms:
            self.seek(self._loop_start_ms)
            return
        self.position_changed.emit(now)
        updates: Dict[int, Dict] = {}

        for track_idx, events in self._tracks.items():
            idx = self._event_idx.get(track_idx, len(events))
            while idx < len(events) and events[idx].time_ms <= now:
                self._note_on(track_idx, events[idx], now)
                idx += 1
            self._event_idx[track_idx] = idx

            done = [s for s, (_, off_ms, _p, _t, _fx) in self._active[track_idx].items()
                    if now >= off_ms]
            for s in done:
                _, _, pitch, _, _ = self._active[track_idx].pop(s)
                self._note_off(track_idx, s, pitch)

            updates[track_idx] = {
                s: (fret, fx, _compute_pitch_offset(fx, now, start_ms, off_ms))
                for s, (fret, off_ms, _, start_ms, fx) in self._active[track_idx].items()
            }

        self.notes_changed.emit(updates)

        if self._metro_on and self._metro_events:
            while (self._metro_idx < len(self._metro_events) and
                   self._metro_events[self._metro_idx].time_ms <= now):
                ev = self._metro_events[self._metro_idx]
                self._metro_idx += 1
                if _PG_AUDIO:
                    sound = self._click_hi if ev.beat_num == 1 else self._click_lo
                    self._click_ch.stop()
                    self._click_ch.play(sound)
                self.beat_changed.emit(ev.beat_num, ev.beats_per_bar)

        if self._tracks and all(
            self._event_idx.get(i, 0) >= len(evs) and not self._active.get(i)
            for i, evs in self._tracks.items()
        ):
            self._timer.stop()
            self.is_playing = False
            self.finished.emit()

    # ----------------------------------------------------------------- note on/off

    def _note_on(self, track_idx: int, ev: NoteEvent, now: float):
        if ev.string in self._active[track_idx]:
            _, _, old_pitch, _, _ = self._active[track_idx][ev.string]
            self._note_off(track_idx, ev.string, old_pitch)

        fx = ev.effects

        if _PG_AUDIO and track_idx not in self._muted:
            is_dead = fx and fx.dead
            if is_dead:
                sound = self._dead_tone
            else:
                pitch = max(0, min(127, ev.midi_pitch + self.pitch_offset))
                has_pitch_fx = fx and (fx.bend or fx.slide_in or fx.slide_out or fx.vibrato)
                if has_pitch_fx:
                    sound = _make_fx_tone(pitch, ev.duration_ms, fx)
                else:
                    if pitch not in self._tone_cache:
                        self._tone_cache[pitch] = _make_tone(_midi_freq(pitch))
                    sound = self._tone_cache[pitch]
            slot = self._pg_slot.get(track_idx)
            if slot is not None:
                ch = pygame.mixer.Channel(slot * _STRINGS + (ev.string - 1))
                ch.stop()
                ch.set_volume(1.0)
                ch.play(sound, maxtime=int(ev.duration_ms))

        off_ms = ev.time_ms + ev.duration_ms
        if fx and fx.let_ring:
            off_ms = ev.time_ms + ev.duration_ms * 4.0
        self._active[track_idx][ev.string] = (ev.fret, off_ms, ev.midi_pitch, now, fx)

    def _note_off(self, track_idx: int, string: int, pitch: int):
        if _PG_AUDIO:
            slot = self._pg_slot.get(track_idx)
            if slot is not None:
                pygame.mixer.Channel(slot * _STRINGS + (string - 1)).fadeout(60)

    # ----------------------------------------------------------------- silence

    def _silence_track(self, track_idx: int):
        slot = self._pg_slot.get(track_idx)
        if _PG_AUDIO and slot is not None:
            for s in range(_STRINGS):
                pygame.mixer.Channel(slot * _STRINGS + s).fadeout(80)
        self._active[track_idx] = {}

    # ----------------------------------------------------------------- helpers

    def _cache_tones(self, events: List[NoteEvent]):
        if _PG_AUDIO:
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
            if _PG_AUDIO:
                pygame.mixer.quit()
        except Exception:
            pass


def _find_idx(events: List[NoteEvent], ms: float) -> int:
    return next((i for i, e in enumerate(events) if e.time_ms >= ms), len(events))
