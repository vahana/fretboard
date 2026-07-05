from dataclasses import dataclass
from typing import List, Tuple
import zipfile
import guitarpro


@dataclass
class NoteEvent:
    time_ms: float
    duration_ms: float
    string: int       # 1-6, 1 = high e
    fret: int         # 0 = open
    midi_pitch: int


def load_song(path: str):
    try:
        return guitarpro.parse(path)
    except Exception as primary_err:
        # Fall back to our own ZIP+GPIF parser for GP6 .gpx files
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
        # Pick up tempo changes stored on the measure (GP4/5/X)
        tempo, quarter_ms = _check_measure_tempo(measure, tempo, quarter_ms)

        voice = measure.voices[0] if measure.voices else None
        if voice is None:
            continue

        beat_ms = current_ms
        for beat in voice.beats:
            # Pick up mid-beat tempo changes (mix-table, common in GPX)
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
                    events.append(NoteEvent(
                        time_ms=beat_ms,
                        duration_ms=dur_ms * 0.85,
                        string=s,
                        fret=note.value,
                        midi_pitch=midi,
                    ))
                except Exception:
                    continue

            beat_ms += dur_ms
        current_ms = beat_ms

    events.sort(key=lambda e: e.time_ms)
    return events, tempo


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
