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

### GProTab search

Built-in search dialog connects to [gprotab.net](https://gprotab.net):

- Search by artist or song name
- Results grouped by song, multiple versions collapsed under one entry
- Click a group to expand; click a tab to fetch its rating
- Double-click or press **Download && Open** to download and play immediately

## Installation

### Prerequisites

- Python 3.11+
- [uv](https://github.com/astral-sh/uv) — fast Python package runner

Install `uv` if you don't have it:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

On macOS with Homebrew:

```bash
brew install uv
```

### Get the app

```bash
git clone https://github.com/vahana/fretboard.git
cd fretboard
chmod +x fretboard.py
```

No virtualenv or `pip install` needed — `uv` resolves and caches dependencies automatically on first run.

## Running

```bash
./fretboard.py                   # open file picker
./fretboard.py path/to/song.gp5  # open a specific file
```

On macOS you can also double-click `fretboard.py` in Finder if your system associates `.py` files with `uv`.

## Supported formats

`.gp3`, `.gp4`, `.gp5`, `.gpx`, `.gp`
