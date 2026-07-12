# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the app

```bash
./fretboard.py                   # launches the app
./fretboard.py path/to/song.gp5  # open a specific file
```

The script is self-contained with inline `uv` metadata — no virtualenv setup needed. `uv` resolves and caches dependencies automatically.

## Syntax checking (no test suite)

```bash
python3 -c "import ast; ast.parse(open('fretboard.py').read()); print('OK')"
```

Run this for any file changed. There are no automated tests.

## Architecture

The app is a Guitar Pro file viewer/player built with PyQt6.

### Data pipeline

`parser.py` → `player.py` → `fretboard_widget.py`

1. **`parser.py`** — parses GP files via PyGuitarPro into internal dataclasses:
   - `NoteEvent(time_ms, duration_ms, string, fret, midi_pitch, effects)` — one per note
   - `NoteEffects` — bend, slide, vibrato, palm_mute, dead, hammer_on, let_ring
   - `BeatEvent(time_ms, beat_num, beats_per_bar)` — for the metronome
   - `BendPoint(pos, value)` — bend curve points (pos 0–1, value in semitones)
   - `load_song(path)` → PyGuitarPro song object (falls back to `gpif_parser` for GP6)
   - `parse_track(song, idx)` → `(List[NoteEvent], tempo_bpm)`
   - `parse_beats(song)` → `List[BeatEvent]`
   - `parse_sections(song)` → `List[Tuple[float, str]]` — section markers (Intro, Chorus, Solo, etc.) with timestamps

2. **`player.py`** — `Player(QObject)` drives playback via a 16ms QTimer tick:
   - Loads tracks as `List[NoteEvent]`; emits `notes_changed(dict)`, `position_changed(float)`, `finished()`
   - Synthesizes audio with pygame mixer: static tones cached by MIDI pitch (`_make_tone`), per-note waveforms for bend/slide/vibrato (`_make_fx_tone` via phase accumulation), a pitch-independent thud for dead notes (`_make_dead_tone`), and click tones for metronome
   - `notes_changed` payload: `{track_idx: {string: (fret, NoteEffects, bend_offset_semitones)}}`
   - Supports: pitch offset (semitones), speed multiplier, per-track mute, seek, metronome on/off
   - Loop: `set_loop(start_ms, end_ms)` / `clear_loop()` — enforced in `_tick` before any note processing

3. **`fretboard_widget.py`** — `FretboardWidget(QWidget)` paints one fretboard per track:
   - `set_notes(dict)` — currently sounding notes (from `notes_changed`)
   - `set_context_notes(list)` — nearby notes for visual context; tuples are `(string, fret, is_upcoming, dist_frac, note_tag)` where `note_tag` is `'dead'`, `'palm'`, or `''`
   - Active notes: red dot (normal), brown PM dot (palm mute), hollow red ring (hammer-on), grey hollow x (dead)
   - Context notes: amber (past), green (upcoming), grey x (dead), brown (palm mute); alpha scales with `dist_frac`

4. **`fretboard.py`** — `MainWindow(QMainWindow)` ties everything together:
   - Per-file state (position, enabled tracks, muted tracks, pitch, speed, loop markers) persisted to `~/.fretboard.json`
   - Reopens last used file on startup
   - Recent files menu (max 5)
   - Beat indicator widget (`_BeatIndicator`) flashes circles matching the time signature
   - `_LoopBar` — custom widget below the seek slider: colored segments between bar-snapped markers, section dots, beat/bar grid ticks, red playhead; supports click-to-seek, double-click to add/remove markers, drag to reposition
   - `_GProTabDialog` — search dialog for gprotab.net: `_SearchWorker` fetches search results + artist page, results shown as a grouped `QTreeWidget` (songs collapsed, versions as children), `_RatingWorker` fetches rating on single-click, `_DownloadWorker` downloads the selected tab
   - Keyboard shortcuts (all use `ApplicationShortcut` so they fire regardless of focus): Space, L, M, D, ←→ (bar), ↑↓ (segment), ⌘←→ (move/create marker)

### GP6/GPX fallback

`gpif_parser.py` handles Guitar Pro 6 `.gpx` files (ZIP + GPIF XML). It exposes a `_GpifSong` duck-typed to match PyGuitarPro's song object, used transparently by `parse_track`.

### Audio synthesis

All synthesis is numpy-based, producing `int16` buffers for `pygame.mixer.Sound`. Amplitude target is `8000` (out of 32767) to avoid clipping when multiple notes play simultaneously. Phase accumulation is used for pitch-varying notes so frequency transitions are continuous.
