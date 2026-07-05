import glob
import os
import time
import platform
from typing import List, Dict, Tuple
from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from parser import NoteEvent

TICK_MS = 16
_STRINGS = 6
_MAX_PG_TRACKS = 8
_MIDI_CHANNELS = [c for c in range(16) if c != 9]
_GUITAR_PRESET_DEFAULT = 25   # GM 0-indexed: Acoustic Steel Guitar
_SAMPLE_RATE = 44100
_TONE_SECS = 2.5

_APP_DIR = os.path.dirname(os.path.abspath(__file__))


def _sf2_candidates() -> list:
    local = sorted(glob.glob(os.path.join(_APP_DIR, '*.sf2')))
    system = [
        os.path.expanduser('~/.fluidsynth/default.sf2'),
        '/usr/share/sounds/sf2/FluidR3_GM.sf2',
        '/usr/share/sounds/sf2/TimGM6mb.sf2',
        '/usr/local/share/sounds/sf2/FluidR3_GM.sf2',
        '/opt/homebrew/share/sounds/sf2/FluidR3_GM.sf2',
    ]
    return local + system


# ── pygame / numpy backend ────────────────────────────────────────────────────
try:
    import numpy as np
    import pygame.mixer
    pygame.mixer.pre_init(44100, -16, 1, 1024)
    pygame.mixer.init()
    _PG_AUDIO = True
except Exception:
    _PG_AUDIO = False

# ── FluidSynth backend ────────────────────────────────────────────────────────
try:
    import fluidsynth as _fs_mod
    _FS_AUDIO = True
except Exception:
    _fs_mod = None
    _FS_AUDIO = False


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


def _audio_driver() -> str:
    return 'coreaudio' if platform.system() == 'Darwin' else 'alsa'


class Player(QObject):
    notes_changed = pyqtSignal(dict)
    position_changed = pyqtSignal(float)
    finished = pyqtSignal()
    soundfont_changed = pyqtSignal(str)   # path, or '' on failure

    def __init__(self, parent=None):
        super().__init__(parent)
        self.total_ms = 0.0
        self.is_playing = False
        self._speed = 1.0
        self._offset_ms = 0.0
        self._wall_start = 0.0
        self._mode = 'synth'          # 'synth' | 'fluid'
        self._guitar_preset = _GUITAR_PRESET_DEFAULT

        self._tracks: Dict[int, List[NoteEvent]] = {}
        self._event_idx: Dict[int, int] = {}
        # [track_idx][string] = (fret, off_ms, midi_pitch)
        self._active: Dict[int, Dict[int, Tuple]] = {}

        # pygame slots
        self._pg_slot: Dict[int, int] = {}
        self._free_pg: List[int] = list(range(_MAX_PG_TRACKS))
        self._tone_cache: Dict[int, "pygame.mixer.Sound"] = {}
        if _PG_AUDIO:
            pygame.mixer.set_num_channels(_MAX_PG_TRACKS * _STRINGS)

        # FluidSynth channels
        self._midi_ch: Dict[int, int] = {}
        self._free_midi_ch: List[int] = list(_MIDI_CHANNELS)
        self._fs = None
        self._sfid = -1
        if _FS_AUDIO:
            self._init_synth()

        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._tick)

    # ----------------------------------------------------------------- synth

    def _init_synth(self):
        try:
            self._fs = _fs_mod.Synth()
            self._fs.start(driver=_audio_driver())
            for path in _sf2_candidates():
                if os.path.exists(path):
                    self._load_sf2(path)
                    break
        except Exception:
            self._fs = None

    def _load_sf2(self, path: str) -> bool:
        if self._fs is None:
            return False
        try:
            sfid = self._fs.sfload(path)
            if sfid < 0:
                return False
            self._sfid = sfid
            for ch in _MIDI_CHANNELS:
                self._fs.program_select(ch, self._sfid, 0, self._guitar_preset)
            return True
        except Exception:
            return False

    def load_soundfont(self, path: str) -> bool:
        ok = self._load_sf2(path)
        self.soundfont_changed.emit(path if ok else '')
        return ok

    @property
    def has_soundfont(self) -> bool:
        return self._sfid >= 0

    # ----------------------------------------------------------------- mode

    def set_mode(self, mode: str):
        if mode == self._mode:
            return
        was_playing = self.is_playing
        if was_playing:
            self._timer.stop()
        for idx in self._tracks:
            self._silence_track(idx)
        self._mode = mode
        if was_playing:
            self._timer.start()

    def set_guitar_preset(self, preset: int):
        self._guitar_preset = preset
        if self._fs and self._sfid >= 0:
            for ch in self._midi_ch.values():
                self._fs.program_select(ch, self._sfid, 0, preset)

    # ----------------------------------------------------------------- tracks

    def load_track(self, track_idx: int, events: List[NoteEvent]):
        self._silence_track(track_idx)
        if track_idx not in self._pg_slot and self._free_pg:
            self._pg_slot[track_idx] = self._free_pg.pop(0)
        if track_idx not in self._midi_ch and self._free_midi_ch:
            ch = self._free_midi_ch.pop(0)
            self._midi_ch[track_idx] = ch
            if self._fs and self._sfid >= 0:
                self._fs.program_select(ch, self._sfid, 0, self._guitar_preset)
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
        ch = self._midi_ch.pop(track_idx, None)
        if ch is not None:
            self._free_midi_ch.append(ch)
            self._free_midi_ch.sort()
        self._refresh_total()

    def clear_tracks(self):
        for idx in list(self._tracks):
            self._silence_track(idx)
        self._tracks.clear()
        self._event_idx.clear()
        self._active.clear()
        self._pg_slot.clear()
        self._free_pg = list(range(_MAX_PG_TRACKS))
        self._midi_ch.clear()
        self._free_midi_ch = list(_MIDI_CHANNELS)
        self.total_ms = 0.0

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

            done = [s for s, (_, off_ms, _p) in self._active[track_idx].items()
                    if now >= off_ms]
            for s in done:
                _, _, pitch = self._active[track_idx].pop(s)
                self._note_off(track_idx, s, pitch)

            updates[track_idx] = {s: fret for s, (fret, _, _p) in self._active[track_idx].items()}

        self.notes_changed.emit(updates)

        if self._tracks and all(
            self._event_idx.get(i, 0) >= len(evs) and not self._active.get(i)
            for i, evs in self._tracks.items()
        ):
            self._timer.stop()
            self.is_playing = False
            self.finished.emit()

    def _note_on(self, track_idx: int, ev: NoteEvent):
        if ev.string in self._active[track_idx]:
            _, _, old_pitch = self._active[track_idx][ev.string]
            self._note_off(track_idx, ev.string, old_pitch)

        if self._mode == 'synth' and _PG_AUDIO:
            slot = self._pg_slot.get(track_idx)
            if slot is not None and ev.midi_pitch in self._tone_cache:
                ch = pygame.mixer.Channel(slot * _STRINGS + (ev.string - 1))
                if ch.get_busy():
                    ch.stop()
                ch.play(self._tone_cache[ev.midi_pitch])
        elif self._mode == 'fluid' and self._fs and self._sfid >= 0:
            midi_ch = self._midi_ch.get(track_idx)
            if midi_ch is not None:
                self._fs.noteon(midi_ch, ev.midi_pitch, 100)

        self._active[track_idx][ev.string] = (ev.fret, ev.time_ms + ev.duration_ms, ev.midi_pitch)

    def _note_off(self, track_idx: int, string: int, pitch: int):
        if self._mode == 'synth' and _PG_AUDIO:
            slot = self._pg_slot.get(track_idx)
            if slot is not None:
                pygame.mixer.Channel(slot * _STRINGS + (string - 1)).fadeout(60)
        elif self._mode == 'fluid' and self._fs:
            midi_ch = self._midi_ch.get(track_idx)
            if midi_ch is not None:
                self._fs.noteoff(midi_ch, pitch)

    def _silence_track(self, track_idx: int):
        slot = self._pg_slot.get(track_idx)
        if _PG_AUDIO and slot is not None:
            for s in range(_STRINGS):
                pygame.mixer.Channel(slot * _STRINGS + s).fadeout(80)
        midi_ch = self._midi_ch.get(track_idx)
        if self._fs and midi_ch is not None:
            for _, _, pitch in self._active.get(track_idx, {}).values():
                self._fs.noteoff(midi_ch, pitch)
        self._active[track_idx] = {}

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
        try:
            if self._fs:
                self._fs.delete()
        except Exception:
            pass


def _find_idx(events: List[NoteEvent], ms: float) -> int:
    return next((i for i, e in enumerate(events) if e.time_ms >= ms), len(events))
