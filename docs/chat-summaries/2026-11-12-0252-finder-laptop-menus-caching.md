# Finder: laptop layout, menus, Songsterr parts, caching

- Saved: 2026-11-12T02:52-07:00
- Source: current chat (continues 2026-11-12-0145-pedal-sim-chrome-tempo-match.md)
- Project: c:\Users\irsfr\OneDrive\Desktop\MidiTool\miditool

## Goal
Make the drum MIDI finder (`scripts/find_drum_midi.py`) portable to another Windows laptop, compact and menu-driven, able to open guitar and bass Songsterr tabs, free of the HTML chart page, debugged, and faster when switching songs.

## Outcome
All of it is done and pushed to GitHub as `538b3e4` on `main` (https://github.com/irsfrankland/miditool). After that push the user added Aerials (System of a Down) through the finder, so `songs/ready.json` has one uncommitted change. The finder is running from `Start Drum Finder.bat`.

## Decisions and constraints
- The HTML chart page is no longer wanted. `docs/charts` is deleted, and Add to chart is now Add to song list (it writes `songs/ready.json` only).
- `build_section_chart.py` stays as the MIDI parsing library. Run directly, it checks the song list: files exist and the eight `EXPECTED` songs keep their ranges.
- Collapse options into the top menus and dropdowns, cut explanatory text (moved to hover tooltips), and fit a laptop screen; the window can be enlarged.
- Each song is parsed when it is selected, never all songs at once. Per-song state is kept for the session.
- Caches and preview wavs stay out of OneDrive, in `%LOCALAPPDATA%\MidiTool`.
- Standing rules from earlier chats still apply:
  - never automate Rhythm Converter;
  - replace or delete the source MIDI only by explicit choice, and deleted files go to the Recycle Bin;
  - don't strip honk notes;
  - don't flatten Ain't Talkin' tempo;
  - don't play audio in tests;
  - don't kill unrelated python processes;
  - commit only when asked, with one-shot `-c user.name="irsfrankland" -c user.email="irsfrankland@users.noreply.github.com"`.

## Work done
- **Portable setup:**
  - `songs/ready.json` uses `"drumRoot": "../Drum Tracks/Artists"`, resolved against the project by `chart.drum_root()`;
  - `Start Drum Finder.bat` tries `pyw -3`, then `pythonw`, then the Python312 `pythonw.exe`.
- **Menus:**
  - File: open, save, add, copy, source and final folders, refresh, quit.
  - Edit: undo, redo, Selected bars, Song name….
  - View: zoom, Songsterr sections, text size.
  - Songsterr: tab parts, search, line-up, use sections, match tempo, Chrome.
  - Help: tips.
  - The toolbar also has Play, Pause, Stop, the loop box, a "Selected bars ▾" dropdown and Pedal rehearsal.
- **Text size:** a slider (80–150%) at the top right drives named Tk fonts and the chart scale (`SectionChart.set_scale`).
- **Settings:** stored per machine in `%APPDATA%\MidiTool\finder-settings.json` (`sourceFolder`, `finalFolder`, `textScale`, `geometry`).
- **Song list:**
  - saved songs are white;
  - source-folder MIDIs that aren't saved are grey and parsed on click;
  - the source folder is rescanned every 4 s.
- **Songsterr parts:** "Open the drum / guitar / bass tab" picks the most-viewed part of each kind, "Open another part ›" lists all parts, and there's a search item. `songsterr.Part`, `Tab.parts`, `Tab.part_url`, `Tab.best_part`. Play-along works with any part.
- **Bar chart:** bars leave about 20% headroom (at least 18 px) under the top of the background; the peak label sits on the top level line (`preview_ui.py`).
- **Bug fixes:**
  - worker threads no longer touch Tk; they use `Finder.from_worker` and the queue polled in `poll_covers`;
  - Songsterr titles are compared on letters and digits only (`songsterr.same_title`), so TNT matches T.N.T.;
  - line-up moves don't count as unsaved edits (`LINEUP_LABELS`);
  - the quit prompt counts unsaved songs across sessions.
- **Speed:**
  - Songsterr disk cache (`songsterr.find_cached`): 7 days for a found tab, 1 day for a song Songsterr doesn't have;
  - per-song sessions (`park_view`, `resume_session`, `show_view`) keyed by `session_key(path, editable)` and checked against file mtime;
  - previews in `PREVIEWS` named `<key>-<generation>.wav`, kept for the four most recent songs and cleared at start and quit;
  - `render_section` mixes pre-built drum sounds, about 4× faster (around 0.4 s);
  - `open_source` no longer parses twice.
- **Dead code removed:** `describe`, `KitInfo` and `read_kit` (replaced by `file_notes`), `styled_text`, `restart_preview`, `open_folder`, `SITES`, and the HTML rendering in `build_section_chart.py`.
- **Checks:**
  - `pyflakes` is clean;
  - `vulture` shows false alarms only;
  - a headless run of all 18 songs, twice, with audio stubbed out, had no errors.

## Open
- `songs/ready.json` has the uncommitted Aerials entry. Its pattern 1 is 49–63 and pattern 2 is 65–80, which skips bar 64; the user may want to check that.
- New song notes still say "This chart does not rewrite the file." (the wording in `file_notes`), which refers to the removed chart page.
- The pedal rehearsal window is a fixed 900×780 drawing and won't fit small laptop screens.
- The Chrome extension's JavaScript wasn't checked, because Node isn't installed.

## Context for a future agent
- **Project:** a Tkinter tool for building Boss RC-10R rhythm patterns from drum MIDIs.
  - Main app: `scripts/find_drum_midi.py`.
  - Supporting modules:
    - `scripts/preview_ui.py`: the bar chart and audio render;
    - `scripts/songsterr.py`: tab lookup, cache and line-up;
    - `scripts/build_section_chart.py`: MIDI parsing and song list check;
    - also `pedal_sim.py`, `suggest_sections.py`, `midi_export.py`, `album_art.py` and `chrome_bridge.py`.
  - Song list: `songs/ready.json`. Drum MIDIs: `../Drum Tracks/Artists`.
- **Python:** `%LOCALAPPDATA%\Programs\Python\Python312\python.exe`. In PowerShell, chain commands with `;`.
- **Relaunching:** stop only the processes whose command line contains `find_drum_midi.py`, then run `Start Drum Finder.bat`.
- **Headless tests:**
  - point `app.INCOMING`, `app.SETTINGS_PATH`, `app.PREVIEWS` and `app.SONGSTERR_CACHE` at temp paths;
  - stub `app.audio_command` and `messagebox`;
  - drive the window with `root.update()`;
  - close with `finder.dirty=False; finder.sessions.clear(); finder.on_quit()`.
- **Laptop setup:** sign in to OneDrive on the laptop, install Python 3 with "Add to PATH", and double-click `Start Drum Finder.bat`.
- **Next step:** commit the Aerials song list change if the user asks, and consider fixing the leftover "chart" wording in `file_notes`.
