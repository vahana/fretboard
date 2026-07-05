"""
Parser for Guitar Pro 6 GPX files (ZIP + GPIF XML format).
Used as fallback when PyGuitarPro can't read the file.
"""

import zipfile
import xml.etree.ElementTree as ET
from typing import List, Tuple
from parser import NoteEvent

RHYTHM_VALUES = {
    'Whole': 1, 'Half': 2, 'Quarter': 4, 'Eighth': 8,
    'Sixteenth': 16, 'ThirtySecond': 32, 'SixtyFourth': 64,
}
DEFAULT_TUNING = [64, 59, 55, 50, 45, 40]  # standard, high-e first


def load_gpx(path: str):
    """
    Returns a lightweight song-like object with .tracks and .tempo,
    compatible with the rest of the app.
    """
    root = _extract_gpif(path)
    return _GpifSong(root)


class _GpifSong:
    def __init__(self, root):
        self._root = root
        self.tempo = _initial_tempo(root)
        self.tracks = [
            _GpifTrack(t, i)
            for i, t in enumerate(root.findall('Tracks/Track'))
        ]


class _GpifTrack:
    def __init__(self, el, index):
        self._index = index
        self.name = el.get('name', f'Track {index + 1}')
        self.strings = _parse_tuning(el)


class _GpifString:
    def __init__(self, pitch):
        self.value = pitch


def parse_gpif_track(song: _GpifSong, track_index: int) -> Tuple[List[NoteEvent], float]:
    root = song._root
    track = song.tracks[track_index]
    tuning = [s.value for s in track.strings]

    tempo = _initial_tempo(root)
    quarter_ms = 60_000.0 / tempo

    rhythms = {r.get('id'): r for r in root.findall('Rhythms/Rhythm')}
    notes   = {n.get('id'): n for n in root.findall('Notes/Note')}
    beats   = {b.get('id'): b for b in root.findall('Beats/Beat')}
    voices  = {v.get('id'): v for v in root.findall('Voices/Voice')}
    bars    = {b.get('id'): b for b in root.findall('Bars/Bar')}

    events: List[NoteEvent] = []
    current_ms = 0.0

    for mb in root.findall('MasterBars/MasterBar'):
        tempo, quarter_ms = _mb_tempo(mb, tempo, quarter_ms)

        bar_ids = (mb.findtext('Bars') or '').split()
        if track_index >= len(bar_ids):
            current_ms += _mb_duration(mb, quarter_ms)
            continue

        bar_id = bar_ids[track_index]
        if bar_id == '-1' or bar_id not in bars:
            current_ms += _mb_duration(mb, quarter_ms)
            continue

        voice_ids = (bars[bar_id].findtext('Voices') or '').split()
        primary = next((v for v in voice_ids if v != '-1'), None)
        if primary is None or primary not in voices:
            current_ms += _mb_duration(mb, quarter_ms)
            continue

        beat_ms = current_ms
        for beat_id in (voices[primary].findtext('Beats') or '').split():
            if beat_id not in beats:
                continue
            beat = beats[beat_id]

            rhythm_ref = beat.find('Rhythm')
            rid = rhythm_ref.get('ref') if rhythm_ref is not None else None
            dur_ms = _rhythm_ms(rhythms.get(rid), quarter_ms) if rid else quarter_ms

            for nid in (beat.findtext('Notes') or '').split():
                if nid not in notes:
                    continue
                ev = _parse_note(notes[nid], beat_ms, dur_ms, tuning)
                if ev:
                    events.append(ev)

            beat_ms += dur_ms
        current_ms = beat_ms

    events.sort(key=lambda e: e.time_ms)
    return events, tempo


# ------------------------------------------------------------------ helpers

def _extract_gpif(path: str) -> ET.Element:
    with zipfile.ZipFile(path) as zf:
        candidates = [n for n in zf.namelist()
                      if n.lower().endswith('.gpif') or n.lower().endswith('score.gpif')]
        if not candidates:
            raise ValueError("No GPIF score file found inside GPX archive")
        with zf.open(candidates[0]) as f:
            return ET.parse(f).getroot()


def _initial_tempo(root: ET.Element) -> float:
    for auto in root.findall('.//Automation'):
        atype = auto.get('type') or auto.findtext('Type') or ''
        if 'Tempo' in atype:
            v = (auto.findtext('Value') or '').split()
            if v:
                return float(v[0])
    return 120.0


def _mb_tempo(mb: ET.Element, tempo: float, quarter_ms: float):
    for auto in mb.findall('Automations/Automation'):
        atype = auto.get('type') or auto.findtext('Type') or ''
        if 'Tempo' in atype:
            v = (auto.findtext('Value') or '').split()
            if v:
                tempo = float(v[0])
                quarter_ms = 60_000.0 / tempo
    return tempo, quarter_ms


def _mb_duration(mb: ET.Element, quarter_ms: float) -> float:
    time_sig = mb.findtext('Time') or '4/4'
    try:
        num, den = time_sig.split('/')
        return quarter_ms * 4.0 * int(num) / int(den)
    except Exception:
        return quarter_ms * 4.0


def _parse_tuning(track_el: ET.Element) -> List['_GpifString']:
    for prop in track_el.findall('.//Property'):
        if prop.get('name') == 'Tuning':
            pitches_text = prop.findtext('Pitches') or ''
            pitches = pitches_text.split()
            if pitches:
                return [_GpifString(int(p)) for p in pitches]
    return [_GpifString(p) for p in DEFAULT_TUNING]


def _rhythm_ms(rhythm: ET.Element, quarter_ms: float) -> float:
    if rhythm is None:
        return quarter_ms
    value = RHYTHM_VALUES.get(rhythm.findtext('NoteValue') or 'Quarter', 4)
    ms = quarter_ms * 4.0 / value

    dot = rhythm.find('AugmentationDot')
    if dot is not None:
        count = int(dot.get('count', '0'))
        if count == 1:
            ms *= 1.5
        elif count >= 2:
            ms *= 1.75

    tuplet = rhythm.find('PrimaryTuplet')
    if tuplet is not None:
        num = int(tuplet.get('num', '1'))
        den = int(tuplet.get('den', '1'))
        if num != den and den:
            ms *= den / num

    return ms


def _parse_note(note_el: ET.Element, beat_ms: float,
                dur_ms: float, tuning: List[int]) -> 'NoteEvent | None':
    props = {p.get('name'): p for p in note_el.findall('Properties/Property')}

    if 'Muted' in props or 'PalmMuted' in props:
        return None

    str_el  = props.get('String')
    fret_el = props.get('Fret')
    if str_el is None or fret_el is None:
        return None

    try:
        string_0 = int((str_el.find('Number') or str_el).text)
        fret     = int((fret_el.find('Number') or fret_el).text)
    except (TypeError, ValueError):
        return None

    if string_0 >= len(tuning):
        return None

    midi = tuning[string_0] + fret
    if not (0 <= midi <= 127):
        return None

    return NoteEvent(
        time_ms=beat_ms,
        duration_ms=dur_ms * 0.85,
        string=string_0 + 1,
        fret=fret,
        midi_pitch=midi,
    )
