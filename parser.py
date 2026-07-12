from dataclasses import dataclass, field
from typing import List, Optional, Tuple
import zipfile
import guitarpro


@dataclass
class BendPoint:
    pos: float
    value: float


@dataclass
class NoteEffects:
    velocity: int = 100
    duration_scale: float = 1.0
    let_ring: bool = False
    vibrato: bool = False
    palm_mute: bool = False
    hammer_on: bool = False
    dead: bool = False
    bend: List[BendPoint] = field(default_factory=list)
    slide_in: float = 0.0
    slide_out: float = 0.0


@dataclass
class BeatEvent:
    time_ms: float
    beat_num: int
    beats_per_bar: int


@dataclass
class NoteEvent:
    time_ms: float
    duration_ms: float
    string: int
    fret: int
    midi_pitch: int
    effects: Optional[NoteEffects] = None


def load_song(path: str):
    try:
        return guitarpro.parse(path)
    except Exception as primary_err:
        try:
            from gpif_parser import load_gpx
            return load_gpx(path)
        except zipfile.BadZipFile:
            raise ValueError(
                "Could not read this file. GP6 (BCFS container) and GP7 formats "
                "are not yet supported — try exporting as GP5 from Guitar Pro."
            ) from primary_err
        except Exception as gpif_err:
            raise ValueError(str(primary_err)) from gpif_err


def parse_beats(song) -> List[BeatEvent]:
    try:
        track = song.tracks[0]
    except (IndexError, AttributeError):
        return []
    tempo = song.tempo if getattr(song, 'tempo', None) and song.tempo > 0 else 120
    quarter_ms = 60_000.0 / tempo
    events: List[BeatEvent] = []
    current_ms = 0.0
    for measure in track.measures:
        tempo, quarter_ms = _check_measure_tempo(measure, tempo, quarter_ms)
        try:
            ts = measure.timeSignature
            bpb = ts.numerator
            denom = ts.denominator.value
        except Exception:
            bpb, denom = 4, 4
        beat_dur_ms = quarter_ms * (4.0 / denom)
        for i in range(bpb):
            events.append(BeatEvent(
                time_ms=current_ms + i * beat_dur_ms,
                beat_num=i + 1,
                beats_per_bar=bpb,
            ))
        current_ms += bpb * beat_dur_ms
    return events


def parse_sections(song) -> List[Tuple[float, str]]:
    try:
        track = song.tracks[0]
    except (IndexError, AttributeError):
        return []
    tempo = song.tempo if getattr(song, 'tempo', None) and song.tempo > 0 else 120
    quarter_ms = 60_000.0 / tempo
    sections = []
    current_ms = 0.0
    for measure in track.measures:
        tempo, quarter_ms = _check_measure_tempo(measure, tempo, quarter_ms)
        marker = getattr(measure, 'marker', None)
        if marker:
            title = getattr(marker, 'title', '').strip()
            if title:
                sections.append((current_ms, title))
        try:
            ts = measure.timeSignature
            bpb = ts.numerator
            denom = ts.denominator.value
        except Exception:
            bpb, denom = 4, 4
        current_ms += bpb * quarter_ms * (4.0 / denom)
    return sections


def parse_track(song, track_index: int) -> Tuple[List[NoteEvent], float]:
    from gpif_parser import _GpifSong, parse_gpif_track
    if isinstance(song, _GpifSong):
        return parse_gpif_track(song, track_index)
    track = song.tracks[track_index]
    tempo = song.tempo if song.tempo and song.tempo > 0 else 120
    quarter_ms = 60_000.0 / tempo

    events: List[NoteEvent] = []
    current_ms = 0.0
    num_strings = len(track.strings)

    for measure in track.measures:
        tempo, quarter_ms = _check_measure_tempo(measure, tempo, quarter_ms)
        voice = measure.voices[0] if measure.voices else None
        if voice is None:
            continue

        beat_ms = current_ms
        for beat in voice.beats:
            tempo, quarter_ms = _check_beat_tempo(beat, tempo, quarter_ms)
            try:
                dur_ms = _duration_ms(beat.duration, quarter_ms)
            except Exception:
                dur_ms = quarter_ms

            for note in beat.notes:
                try:
                    s = note.string
                    if not (1 <= s <= num_strings):
                        continue
                    open_pitch = track.strings[s - 1].value
                    midi = open_pitch + note.value
                    if not (0 <= midi <= 127):
                        continue
                    fx = _parse_note_effects(note, beat)
                    events.append(NoteEvent(
                        time_ms=beat_ms,
                        duration_ms=dur_ms * 0.85 * fx.duration_scale,
                        string=s,
                        fret=note.value,
                        midi_pitch=midi,
                        effects=fx,
                    ))
                except Exception:
                    continue

            beat_ms += dur_ms
        current_ms = beat_ms

    events.sort(key=lambda e: e.time_ms)
    return events, tempo


def _parse_note_effects(note, beat) -> NoteEffects:
    ne = note.effect
    be = beat.effect

    velocity = 100
    duration_scale = 1.0

    if getattr(ne, 'heavyAccentuatedNote', False) or getattr(ne, 'accentuatedNote', False):
        velocity = 120
    elif getattr(ne, 'hammer', False):
        velocity = 55
    if getattr(ne, 'palmMute', False):
        velocity = min(velocity, 65)
        duration_scale = 0.35
    if getattr(ne, 'staccato', False):
        duration_scale = min(duration_scale, 0.2)

    let_ring = bool(getattr(ne, 'letRing', False))
    vibrato = bool(getattr(ne, 'vibrato', False) or getattr(be, 'vibrato', False))
    palm_mute = bool(getattr(ne, 'palmMute', False))
    hammer_on = bool(getattr(ne, 'hammer', False))
    try:
        dead = getattr(note, 'type', None) is not None and note.type.name == 'dead'
    except Exception:
        dead = False

    bend = _parse_bend(ne)
    slide_in, slide_out = _parse_slides(ne)

    return NoteEffects(
        velocity=velocity,
        duration_scale=duration_scale,
        let_ring=let_ring,
        vibrato=vibrato,
        palm_mute=palm_mute,
        hammer_on=hammer_on,
        dead=dead,
        bend=bend,
        slide_in=slide_in,
        slide_out=slide_out,
    )


def _parse_bend(ne) -> List[BendPoint]:
    try:
        b = ne.bend
        if not b or not b.points:
            return []
        return [BendPoint(p.position / 12.0, p.value / 2.0) for p in b.points]
    except Exception:
        return []


def _parse_slides(ne) -> Tuple[float, float]:
    slide_in = 0.0
    slide_out = 0.0
    try:
        slides = ne.slides
        if not slides:
            return slide_in, slide_out
        for s in slides:
            name = s.name
            if name == 'intoFromBelow':
                slide_in = -2.5
            elif name == 'intoFromAbove':
                slide_in = 2.5
            elif name == 'outDownwards':
                slide_out = -2.5
            elif name == 'outUpwards':
                slide_out = 2.5
    except Exception:
        pass
    return slide_in, slide_out


def _check_measure_tempo(measure, tempo, quarter_ms):
    try:
        t = measure.tempo
        if t and t.value and t.value > 0:
            tempo = t.value
            quarter_ms = 60_000.0 / tempo
    except Exception:
        pass
    return tempo, quarter_ms


def _check_beat_tempo(beat, tempo, quarter_ms):
    try:
        mtc = beat.effect.mixTableChange
        if mtc and mtc.tempo and mtc.tempo.value > 0:
            tempo = mtc.tempo.value
            quarter_ms = 60_000.0 / tempo
    except Exception:
        pass
    return tempo, quarter_ms


def _duration_ms(duration, quarter_ms: float) -> float:
    ms = quarter_ms * 4.0 / duration.value
    if getattr(duration, 'isDotted', False):
        ms *= 1.5
    elif getattr(duration, 'isDoubleDotted', False):
        ms *= 1.75
    t = duration.tuplet
    if t.enters != 1:
        ms *= t.times / t.enters
    return ms
