# Song list, Songsterr search, Chrome sync

- Saved: 2026-11-12T14:13:00-07:00
- Source: current chat (transcript db2eee80-76ac-49f6-99b5-3f846d15187a)
- Project: c:\Users\irsfr\OneDrive\Desktop\MidiTool\miditool

## Goal
Keep improving the Drum MIDI finder for RC-10R rehearsal: chart usability, a useful pedal looper guide, a blank song list for new GitHub users, Songsterr find/download/autocomplete, and solid Songsterr-in-Chrome follow sync.

## Outcome
Shipped several finder and pedal features and pushed most of them to GitHub (releases through Midi Tool V0.11 / tag `MIDI`). Song list is per Windows user. Find online can search Songsterr and build a drum MIDI from the tab. Artist and song title autocomplete from Songsterr’s most-played catalog. Chrome status flicker was reduced. Follow sync for slightly late bars (e.g. bar 17) was attempted, then rolled back from a worse cursor-chase approach; a milder late-confirm fix (extension 1.0.3) is in place and still needs user verification. Local commit `c91fb2d` (autocomplete) may not be on GitHub if the push was skipped.

## Decisions and constraints
- Never automate Rhythm Converter; launching its exe is OK.
- Replace or delete source MIDI only by explicit user choice; deleted files go to Recycle Bin.
- Don’t strip honk notes; don’t flatten Ain’t Talkin’ tempo unless the user retimes it.
- Don’t edit `c:\Users\irsfr\.cursor\plans\midi_section_charts_0abcbc21.plan.md`.
- Commit/push only when asked; one-shot `-c user.name="irsfrankland" -c user.email="irsfrankland@users.noreply.github.com"`; never write git config. Push to main needs explicit approval.
- Don’t play audible audio in tests; don’t kill unrelated Python processes (only `find_drum_midi.py`).
- Eight original songs stay locked in `EXPECTED` once present in a user’s list.
- Prefer type 0 MIDI; bar numbers start at 1.
- Pedal looper should be a silent practice guide (cues and timing), not real audio recording.
- Song list must not travel with the program; new users start blank; do not delete the user’s existing songs.
- RC-10R itself does not play MIDI tempo maps or mid-song meter changes; one tempo and one time signature per rhythm.

## Work done
- Pedal looper practice cues in `scripts/pedal_sim.py` + clock wiring in `scripts/find_drum_midi.py` (bar timing of LOOP presses, length vs pattern, amber cue on last bar).
- Chart: Audacity-style seconds ruler, hover tip naming the bar (and range while dragging), fill/center, scroll-follow, double-click play (earlier in session / prior commits).
- Song list moved to `%APPDATA%\MidiTool\song-list.json` via `chart.ensure_song_list()`; first run copies project `songs/ready.json` if present. Blank list defaults `drumRoot` to `Documents\MidiTool\Drum Tracks`. Repo no longer tracks `songs/ready.json`. Pushed as `7a3cfbd`.
- Find online: Songsterr drum-tab search/download (`songsterr.search_tabs`, `songsterr_midi`) and empty-list hints. Pushed as `d25246e`. GitHub release renamed to **Midi Tool V0.11**; tag `MIDI` → `d25246e`.
- Autocomplete: `popular_catalog_cached` (~1000 artists / ~3453 songs), `FieldComplete` on artist and title. Commit `c91fb2d` (push may have been skipped).
- Chrome helper: status debounce + fixed-width label; extension versions through **1.0.3**. Cursor-chase sync reverted; late bar confirm keeps `markFraction`; wall-clock elapsed restored for in-bar progress.
- Finder relaunched after Chrome sync changes.

## Open
- Verify Songsterr follow on problem bars (e.g. #17) after extension **1.0.3** reload; if still off, may be MIDI tempo/meter for that song rather than the follow clock.
- Push `c91fb2d` (and later uncommitted Chrome/sync edits) if not yet on GitHub; bump release past 0.11 if desired.
- Reload Chrome extension after each `chrome-extension` change (`chrome://extensions` → Reload, refresh Songsterr tab).
- Uncommitted local changes likely include Chrome sync/status work in `scripts/find_drum_midi.py` and `chrome-extension/*` since the last successful push.

## Context for a future agent
Project is the Tkinter Drum MIDI finder at `scripts/find_drum_midi.py`, with chart in `scripts/preview_ui.py`, Songsterr in `scripts/songsterr.py`, pedal sim in `scripts/pedal_sim.py`, Chrome bridge/extension in `scripts/chrome_bridge.py` and `chrome-extension/`. Python: `%LOCALAPPDATA%\Programs\Python\Python312\python.exe`. Launch: `Start Drum Finder.bat`. Song list: `%APPDATA%\MidiTool\song-list.json`. Songsterr popular cache: `%LOCALAPPDATA%\MidiTool\songsterr\popular.json`. Drum MIDIs for this user still live under `../Drum Tracks/Artists` via the migrated list; new installs use `Documents\MidiTool\Drum Tracks`. Remote: `https://github.com/irsfrankland/miditool.git`, release tag `MIDI` titled Midi Tool V0.11. Next step: confirm whether autocomplete and Chrome sync commits are pushed; have the user reload extension 1.0.3 and retest bar alignment; if bar 17 is still late, inspect that song’s MIDI vs tab tempo/meter rather than chasing the SVG playhead again.
