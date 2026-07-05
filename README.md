# Fretboard Viewer

A Guitar Pro file player and fretboard visualiser built with PyQt6.

## Features

### Playback
- Play, pause, stop, and seek through Guitar Pro files (`.gp3`, `.gp4`, `.gp5`, `.gpx`, `.gp`)
- Speed control (25%–100%)
- Pitch shift (±24 semitones)
- Metronome with audio clicks (accented downbeat) and visual beat indicator matching the file's time signature

### Fretboard display
- One fretboard per enabled track, stacked vertically
- Active notes highlighted with technique-specific visuals:
  - **Normal** — red filled dot
  - **Palm mute** — brown dot labelled `PM`
  - **Hammer-on / pull-off** — hollow red ring
  - **Dead note** — grey hollow circle with `x`
  - **Bend** — arrow showing bend direction and amount
  - **Slide** — angled line into/out of the note
  - **Vibrato** — `~` marker above the dot
- Context window shows upcoming and recently played notes as faded circles — green for upcoming, amber for past, dimming with distance from the playhead

### Audio
- Additive sine synthesis with per-note bend, slide, and vibrato baked in via phase accumulation
- Dead notes play a short percussive thud
- Per-track mute (silences audio while keeping fretboard visuals active)

### File management
- Recent files menu (last 5 opened)
- Per-file state saved across sessions: playback position, enabled tracks, muted tracks, pitch offset, and speed

## Running

Requires [uv](https://github.com/astral-sh/uv). Dependencies are declared inline — no setup needed.

```bash
./fretboard.py                   # open file picker
./fretboard.py path/to/song.gp5  # open directly
```
