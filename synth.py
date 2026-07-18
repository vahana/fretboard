from typing import Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from parser import BendPoint, NoteEffects

SAMPLE_RATE = 44100
TONE_SECS = 2.5
VIBRATO_HZ = 5.5
VIBRATO_DEPTH = 0.4
AMP = 4000

# ── pygame / numpy backend ────────────────────────────────────────────────────
try:
    import numpy as np
    import pygame.mixer
    pygame.mixer.pre_init(SAMPLE_RATE, -16, 1, 1024)
    pygame.mixer.init()
    PG_AUDIO = True
except Exception:
    PG_AUDIO = False


# ── helpers ───────────────────────────────────────────────────────────────────

def midi_freq(pitch: int) -> float:
    return 440.0 * 2.0 ** ((pitch - 69) / 12.0)


def interp_bend(points: "List[BendPoint]", t: float) -> float:
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


def _interp_bend_vec(points: "List[BendPoint]", t_arr: "np.ndarray") -> "np.ndarray":
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


_DRUM_RNG: Dict[int, "np.ndarray"] = {}


def _drum_noise(seed: int, n: int) -> "np.ndarray":
    if seed not in _DRUM_RNG:
        _DRUM_RNG[seed] = np.random.default_rng(seed).standard_normal(65536).astype(np.float32)
    buf = _DRUM_RNG[seed]
    if n <= len(buf):
        return buf[:n]
    reps = (n // len(buf)) + 1
    return np.tile(buf, reps)[:n]


# ── synthesis ─────────────────────────────────────────────────────────────────

def make_tone(freq: float) -> "pygame.mixer.Sound":
    t = np.linspace(0, TONE_SECS, int(SAMPLE_RATE * TONE_SECS), endpoint=False)
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
    return pygame.mixer.Sound(buffer=(wave * AMP).astype(np.int16).tobytes())


def make_fx_tone(base_pitch: int, duration_ms: float, fx: "NoteEffects") -> "pygame.mixer.Sound":
    duration_s = min(duration_ms / 1000.0, TONE_SECS)
    n = max(int(SAMPLE_RATE * duration_s), 1)
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
        semitones += np.sin(2 * np.pi * VIBRATO_HZ * t) * VIBRATO_DEPTH

    freqs = midi_freq(base_pitch) * (2.0 ** (semitones / 12.0))
    phase = np.cumsum(2 * np.pi * freqs / SAMPLE_RATE)
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
    return pygame.mixer.Sound(buffer=(wave * AMP).astype(np.int16).tobytes())


def make_bass_tone(freq: float) -> "pygame.mixer.Sound":
    t = np.linspace(0, TONE_SECS, int(SAMPLE_RATE * TONE_SECS), endpoint=False)
    env = np.exp(-t * 1.8)
    attack = np.exp(-t * 80) * 0.3
    wave = (env + attack) * (
        np.sin(2 * np.pi * freq * t)
        + 0.18 * np.sin(4 * np.pi * freq * t)
        + 0.05 * np.sin(6 * np.pi * freq * t)
    )
    peak = np.abs(wave).max()
    if peak:
        wave /= peak
    return pygame.mixer.Sound(buffer=(wave * int(AMP * 1.8)).astype(np.int16).tobytes())


def make_dead_tone() -> "pygame.mixer.Sound":
    n = int(SAMPLE_RATE * 0.08)
    t = np.linspace(0, 0.08, n, endpoint=False)
    env = np.exp(-t * 60)
    wave = env * (
        np.sin(2 * np.pi * 180 * t)
        + 0.5 * np.sin(2 * np.pi * 320 * t)
    )
    peak = np.abs(wave).max()
    if peak:
        wave /= peak
    return pygame.mixer.Sound(buffer=(wave * int(AMP * 0.75)).astype(np.int16).tobytes())


def make_drum_tone(midi_pitch: int) -> "pygame.mixer.Sound":
    sr = SAMPLE_RATE
    if midi_pitch in (35, 36):
        dur, decay = 0.30, 18
        n = int(sr * dur)
        t = np.linspace(0, dur, n, endpoint=False)
        freq = 120 * np.exp(-t * 22)
        phase = np.cumsum(2 * np.pi * freq / sr)
        env = np.exp(-t * decay)
        wave = env * (np.sin(phase) + 0.25 * _drum_noise(midi_pitch, n) * np.exp(-t * 60))
    elif midi_pitch in (37, 38, 39, 40):
        dur = 0.14
        n = int(sr * dur)
        t = np.linspace(0, dur, n, endpoint=False)
        env = np.exp(-t * 28)
        noise = _drum_noise(midi_pitch, n)
        tone = np.sin(2 * np.pi * 190 * t) + 0.4 * np.sin(2 * np.pi * 160 * t)
        wave = env * (0.55 * noise + 0.45 * tone)
    elif midi_pitch in (42, 44):
        dur = 0.045
        n = int(sr * dur)
        t = np.linspace(0, dur, n, endpoint=False)
        env = np.exp(-t * 90)
        wave = env * _drum_noise(midi_pitch, n)
    elif midi_pitch == 46:
        dur = 0.22
        n = int(sr * dur)
        t = np.linspace(0, dur, n, endpoint=False)
        env = np.exp(-t * 14)
        wave = env * _drum_noise(midi_pitch, n)
    elif midi_pitch in (49, 52, 55, 57):
        dur = 0.55
        n = int(sr * dur)
        t = np.linspace(0, dur, n, endpoint=False)
        env = np.exp(-t * 7)
        shimmer = 0.2 * np.sin(2 * np.pi * 7800 * t) + 0.1 * np.sin(2 * np.pi * 5200 * t)
        wave = env * (_drum_noise(midi_pitch, n) + shimmer)
    elif midi_pitch in (51, 53, 59):
        dur = 0.40
        n = int(sr * dur)
        t = np.linspace(0, dur, n, endpoint=False)
        env = np.exp(-t * 9)
        shimmer = 0.35 * np.sin(2 * np.pi * 9500 * t)
        wave = env * (0.65 * _drum_noise(midi_pitch, n) + shimmer)
    else:
        freq_map = {41: 78, 43: 92, 45: 110, 47: 130, 48: 150, 50: 170}
        freq = freq_map.get(midi_pitch, 115)
        dur = 0.18
        n = int(sr * dur)
        t = np.linspace(0, dur, n, endpoint=False)
        env = np.exp(-t * 22)
        wave = env * (np.sin(2 * np.pi * freq * t) + 0.25 * _drum_noise(midi_pitch, n))

    peak = np.abs(wave).max()
    if peak:
        wave /= peak
    return pygame.mixer.Sound(buffer=(wave * int(AMP * 0.875)).astype(np.int16).tobytes())


def make_click(freq: float, duration: float = 0.022) -> "pygame.mixer.Sound":
    n = int(SAMPLE_RATE * duration)
    t = np.linspace(0, duration, n, endpoint=False)
    env = np.exp(-t * 160)
    wave = env * np.sin(2 * np.pi * freq * t)
    peak = np.abs(wave).max()
    if peak:
        wave /= peak
    return pygame.mixer.Sound(buffer=(wave * int(AMP * 1.4)).astype(np.int16).tobytes())
