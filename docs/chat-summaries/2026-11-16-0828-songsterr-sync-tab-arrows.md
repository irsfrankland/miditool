# Songsterr sync and tab shift arrows

- Saved: 2026-11-16T08:28:00-07:00
- Source: current chat (transcript db2eee80-76ac-49f6-99b5-3f846d15187a)
- Project: C:\Users\irsfr\OneDrive\Desktop\MidiTool\miditool

## Goal
Improve Songsterr-in-Chrome follow sync (status stability and bar alignment), then add on-chart arrow controls to shift the Songsterr line-up one bar without using the Songsterr menu.

## Outcome
Chrome status flicker and late-bar follow work landed and was committed as `019d445` (extension 1.0.3). A cursor-chase sync approach was tried, made alignment worse, and was rolled back in favor of wall-clock timing plus keeping `markFraction` on late bar confirms. The newest request—arrow buttons above the chart’s Intro label to move the tab ±1 bar—was started (located existing `on_shift` / Songsterr menu items) but not implemented yet. Uncommitted local edits remain in `scripts/find_drum_midi.py`, `scripts/chrome_bridge.py`, `scripts/pedal_sim.py`, and `scripts/songsterr.py`.

## Decisions and constraints
- Never automate Rhythm Converter; launching its exe is OK.
- Replace/delete source MIDI only by explicit user choice; Recycle Bin for deletes.
- Don’t strip honk notes; don’t flatten Ain’t Talkin’ tempo unless the user retimes it.
- Don’t edit `c:\Users\irsfr\.cursor\plans\midi_section_charts_0abcbc21.plan.md`.
- Commit/push only when asked; one-shot git identity; never write git config; main push needs approval.
- Don’t play audible audio in tests; kill only processes whose command line contains `find_drum_midi.py`.
- Eight original songs stay locked in `EXPECTED` once in a user’s list.
- Prefer type 0 MIDI; bars start at 1.
- Do not chase Songsterr’s SVG playhead for sync; wall-clock from bar marks is the clock.
- Song list stays per-user (`%APPDATA%\MidiTool\song-list.json`); new users start blank.

## Work done
- Chrome status: debounce, fixed-width slot, brief hold through dropouts (`scripts/find_drum_midi.py`).
- Extension through **1.0.3**: confirm play/pause over a few reads; on new bar keep cursor fraction instead of forcing 0; in-bar progress is wall-clock + that fraction (`chrome-extension/content.js`, `manifest.json`).
- Finder follows tab playback speed when computing the target; seek thresholds restored after the failed mid-bar cursor chase.
- Prior session (already summarized / partly committed earlier): per-user song list, Songsterr Find online, artist/song autocomplete (`c91fb2d`), pedal looper practice cues, chart ruler/tooltip, release Midi Tool V0.11.
- Located tab shift API already in the Songsterr menu: `on_shift(±1)`, `place_tab`, items “Move the tab one bar earlier/later” around `build_menus` / `refresh_tab_panel` in `scripts/find_drum_midi.py`. Chart header layout lives in `scripts/preview_ui.py` (`first_row` / Intro labels).

## Open
- **Implement** chart-top ←/→ (or earlier/later) controls above Intro that call `on_shift(-1)` / `on_shift(1)`, enabled when a tab is lined up the same way as the menu items.
- User should reload the Chrome extension after extension file changes.
- Review and commit/push remaining dirty files if the user asks.
- If specific bars (e.g. 17) still drift after 1.0.3, check that song’s MIDI vs tab tempo/meter rather than chasing the SVG cursor again.

## Context for a future agent
Continue in `C:\Users\irsfr\OneDrive\Desktop\MidiTool\miditool`. Main app: `scripts/find_drum_midi.py`. Chart: `scripts/preview_ui.py`. Songsterr: `scripts/songsterr.py`. Chrome: `scripts/chrome_bridge.py` + `chrome-extension/`. Python: `%LOCALAPPDATA%\Programs\Python\Python312\python.exe`. Song list: `%APPDATA%\MidiTool\song-list.json`. Latest relevant commit: `019d445` (steadier Songsterr follow). Next task: add lineup shift arrows on the chart above the Intro / section-name row, wired to existing `self.on_shift(±1)` (menu already has the same actions). Prefer a small control strip in the finder above the chart or widgets embedded at the top of `SectionChart`, not a Songsterr menu navigation. Enable/disable with the same rules as `shift_down` / `shift_up` in `refresh_tab_panel`.
