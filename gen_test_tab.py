#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["PyGuitarPro>=0.7.2"]
# ///
"""
Generates technique_test.gp5 — one measure per technique:
  1  Normal notes
  2  Half-step bend (1 semitone up, hold)
  3  Full bend + release (2 semitones up, back down)
  4  Vibrato
  5  Palm mute (16th-note chug on low E)
  6  Hammer-on sequence
  7  Slide in from below
  8  Slide out downward
  9  Let ring (open strings)
 10  Staccato
 11  Bend + vibrato at peak
 12  Mix: palm mute → hammer → bend
"""

import guitarpro
from guitarpro.models import (
    Song, Track, Measure, MeasureHeader, Voice, Beat, Note,
    NoteEffect, BeatEffect, Duration, GuitarString, TimeSignature,
    BendEffect, BendPoint, NoteType, BeatStatus, BendType, SlideType,
    MidiChannel, Color,
)

OUT = 'technique_test.gp5'
TICKS_PER_BEAT = 960   # quarter note
MEASURE_TICKS  = TICKS_PER_BEAT * 4

STRINGS = [
    GuitarString(1, 64),  # high e
    GuitarString(2, 59),  # B
    GuitarString(3, 55),  # G
    GuitarString(4, 50),  # D
    GuitarString(5, 45),  # A
    GuitarString(6, 40),  # low E
]


# ── helpers ───────────────────────────────────────────────────────────────────

def dur(value=4, dotted=False):
    d = Duration()
    d.value = value
    d.isDotted = dotted
    return d

def note(beat, string, fret, ne=None, vel=95):
    n = Note(beat)
    n.value = fret
    n.string = string
    n.velocity = vel
    n.type = NoteType.normal
    if ne:
        n.effect = ne
    return n

def beat(voice, dur_val=4, notes_spec=None, be=None):
    """notes_spec: list of (string, fret) or (string, fret, NoteEffect)"""
    b = Beat(voice)
    b.duration = dur(dur_val)
    b.status = BeatStatus.normal
    if be:
        b.effect = be
    if notes_spec:
        for spec in notes_spec:
            if len(spec) == 2:
                s, f = spec
                ne = None
            else:
                s, f, ne = spec
            b.notes.append(note(b, s, f, ne))
    return b

def bend_effect(semitones, release=False):
    """semitones up; if release=True comes back to 0."""
    raw = int(semitones * 2)   # our parser divides by 2
    gp_val = int(semitones * 50)
    if release:
        pts = [BendPoint(0, 0), BendPoint(4, raw), BendPoint(8, raw), BendPoint(12, 0)]
        btype = BendType.bendRelease
    else:
        pts = [BendPoint(0, 0), BendPoint(6, raw), BendPoint(12, raw)]
        btype = BendType.bend
    return BendEffect(type=btype, value=gp_val, points=pts)

def ne_bend(semitones, release=False):
    ne = NoteEffect()
    ne.bend = bend_effect(semitones, release)
    return ne

def ne_vibrato():
    ne = NoteEffect()
    ne.vibrato = True
    return ne

def ne_palm():
    ne = NoteEffect()
    ne.palmMute = True
    return ne

def ne_hammer():
    ne = NoteEffect()
    ne.hammer = True
    return ne

def ne_slide_in(from_below=True):
    ne = NoteEffect()
    ne.slides = [SlideType.intoFromBelow if from_below else SlideType.intoFromAbove]
    return ne

def ne_slide_out(down=True):
    ne = NoteEffect()
    ne.slides = [SlideType.outDownwards if down else SlideType.outUpwards]
    return ne

def ne_let_ring():
    ne = NoteEffect()
    ne.letRing = True
    return ne

def ne_staccato():
    ne = NoteEffect()
    ne.staccato = True
    return ne

def ne_bend_vib(semitones):
    ne = NoteEffect()
    ne.bend = bend_effect(semitones)
    ne.vibrato = True
    return ne


# ── measure builders ──────────────────────────────────────────────────────────

def m1_normal(voice):
    """E pentatonic fragments, no effects."""
    return [
        beat(voice, 4, [(1, 0)]),
        beat(voice, 4, [(1, 3)]),
        beat(voice, 4, [(1, 5)]),
        beat(voice, 4, [(1, 7)]),
    ]

def m2_half_bend(voice):
    """Half-step (1 semitone) bend, held."""
    return [
        beat(voice, 2, [(1, 7, ne_bend(1.0))]),
        beat(voice, 2, [(1, 7)]),
    ]

def m3_full_bend_release(voice):
    """Full bend (2 semitones) up then release."""
    return [
        beat(voice, 2, [(1, 7, ne_bend(2.0, release=True))]),
        beat(voice, 2, [(1, 7)]),
    ]

def m4_vibrato(voice):
    """Long vibrato note."""
    return [
        beat(voice, 2, [(1, 9, ne_vibrato())]),
        beat(voice, 2, [(1, 9, ne_vibrato())]),
    ]

def m5_palm_mute(voice):
    """16th-note power-chug on low E + A."""
    pm = ne_palm()
    beats = []
    for _ in range(8):
        beats.append(beat(voice, 8, [(6, 0, ne_palm()), (5, 2, ne_palm())]))
    return beats

def m6_hammer(voice):
    """Hammer-on legato run."""
    return [
        beat(voice, 4, [(2, 5)]),
        beat(voice, 4, [(2, 7, ne_hammer())]),
        beat(voice, 4, [(2, 9, ne_hammer())]),
        beat(voice, 4, [(2, 10, ne_hammer())]),
    ]

def m7_slide_in(voice):
    """Slide into notes from below and above."""
    return [
        beat(voice, 2, [(1, 7, ne_slide_in(from_below=True))]),
        beat(voice, 2, [(1, 5, ne_slide_in(from_below=False))]),
    ]

def m8_slide_out(voice):
    """Slide out downward and upward."""
    return [
        beat(voice, 2, [(1, 9, ne_slide_out(down=True))]),
        beat(voice, 2, [(1, 5, ne_slide_out(down=False))]),
    ]

def m9_let_ring(voice):
    """Open chord, all strings let ring."""
    lr = ne_let_ring()
    return [
        beat(voice, 1, [
            (1, 0, ne_let_ring()),
            (2, 0, ne_let_ring()),
            (3, 0, ne_let_ring()),
            (4, 2, ne_let_ring()),
            (5, 2, ne_let_ring()),
            (6, 0, ne_let_ring()),
        ]),
    ]

def m10_staccato(voice):
    """Staccato 8th notes."""
    return [beat(voice, 8, [(1, f, ne_staccato())]) for f in [0, 3, 5, 7, 5, 3, 0, 3]]

def m11_bend_vibrato(voice):
    """Bend up 1.5 semitones, vibrato at peak."""
    return [
        beat(voice, 2, [(1, 9, ne_bend_vib(1.5))]),
        beat(voice, 2, [(1, 7)]),
    ]

def m12_mix(voice):
    """Palm mute run → hammer-on → full bend."""
    return [
        beat(voice, 8, [(6, 0, ne_palm())]),
        beat(voice, 8, [(6, 0, ne_palm())]),
        beat(voice, 8, [(2, 5)]),
        beat(voice, 8, [(2, 7, ne_hammer())]),
        beat(voice, 4, [(1, 7, ne_bend(2.0))]),
        beat(voice, 4, [(1, 7)]),
    ]

MEASURE_BUILDERS = [
    m1_normal, m2_half_bend, m3_full_bend_release, m4_vibrato,
    m5_palm_mute, m6_hammer, m7_slide_in, m8_slide_out,
    m9_let_ring, m10_staccato, m11_bend_vibrato, m12_mix,
]

LABELS = [
    "1: Normal", "2: Half Bend", "3: Full Bend+Release", "4: Vibrato",
    "5: Palm Mute", "6: Hammer-on", "7: Slide In", "8: Slide Out",
    "9: Let Ring", "10: Staccato", "11: Bend+Vib", "12: Mix",
]


# ── assemble song ─────────────────────────────────────────────────────────────

def build():
    song = Song()
    song.title = "Technique Test"
    song.artist = "fretboard"
    song.tempo = 80

    n_measures = len(MEASURE_BUILDERS)
    headers = []
    for i, label in enumerate(LABELS):
        h = MeasureHeader()
        h.number = i + 1
        h.start = TICKS_PER_BEAT + i * MEASURE_TICKS
        h.timeSignature = TimeSignature()
        h.timeSignature.numerator = 4
        h.timeSignature.denominator.value = 4
        from guitarpro.models import Marker
        h.marker = Marker(title=label, color=Color(255, 0, 0))
        headers.append(h)
    song.measureHeaders = headers

    channel = MidiChannel()
    channel.channel = 0
    channel.effectChannel = 1
    channel.instrument = 25  # acoustic steel guitar

    track = Track(song, number=1, strings=STRINGS, name="Guitar",
                  fretCount=24, channel=channel)

    for i, (header, builder) in enumerate(zip(headers, MEASURE_BUILDERS)):
        measure = Measure(track, header)
        voice = Voice(measure, beats=[])
        voice.beats = builder(voice)
        empty = Voice(measure, beats=[])
        measure.voices = [voice, empty]
        track.measures.append(measure)

    song.tracks = [track]
    return song


if __name__ == '__main__':
    song = build()
    guitarpro.write(song, OUT)
    print(f"Written: {OUT}")
