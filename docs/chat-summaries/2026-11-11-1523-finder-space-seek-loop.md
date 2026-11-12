# Finder space, seek, and loop

- Saved: 2026-11-11T15:23:00-07:00
- Source: current chat
- Project: c:\Users\irsfr\OneDrive\Desktop\MidiTool\miditool

## Goal

Build live-cover tools for a Boss RC-10R Rhythm Loop Station from drum MIDIs. In this stretch the user wanted the finder to play and pause with the space bar, then to click any bar and have playback jump there, plus a simple loop from a chosen bar for a chosen number of bars.

## Outcome

Space-bar play/pause is written in the finder and a silent MCI pause/resume check passed. The finder was not relaunched after that change, so the running window still has the older player. Click-to-seek and the bar loop were requested and not built. The chat is still in progress.

## Decisions and constraints

- Rhythm Converter has no CLI and must not be automated. Do not rewrite MIDI, strip honk notes, flatten Ain't Talkin' tempo, or edit the plan file `c:\Users\irsfr\.cursor\plans\midi_section_charts_0abcbc21.plan.md`.
- Do not hand-edit `docs/charts/index.html` (rebuild it). Do not commit or push unless asked.
- Prefer type 0 MIDI. Bar numbers start at 1 in each file. Songsterr supplies names, bar counts, tempo, and meter only. Ranges may overlap. Fills are one-bar exits and may overlap other divisions.
- Pedal path: intro once, Pattern 1 looping, Pattern 2 looping, ending once. No third pattern. Intro is the opening through the bar before the first chorus. Pattern 1 is that whole first chorus and loops. Pattern 2 is the whole bridge, or the second chorus or other distinct section when there is no bridge, and loops. Ending is the whole outro, including ritard.
- The original eight songs stay locked in `EXPECTED` inside `scripts/build_section_chart.py` except where the user later overrode a specific rule. A new song id is allowed and is not locked.
- Excluded files stay excluded until tempo, meter, and length match: Back in Black, Whole Lotta Love, ATWA, Heartbreaker, Vicinity of Obscenity.
- Americana is BOTH a saved song AND still listed under `excluded` (reason: full song in half time, 60 bars against 119 chart bars). Do not remove either Americana entry unless asked.
- Pattern 1 and Pattern 2 cannot be the same bars. Why Don't You Get a Job and Americana were the saved songs that had to change. The suggester must not copy Pattern 1 into Pattern 2 except a 1-bar file.
- Drum MIDIs live outside the repo at `C:\Users\irsfr\OneDrive\Desktop\MidiTool\Drum Tracks\Artists` (`songs/ready.json` `drumRoot`).
- Search only Midifind.com and MIDIWORLD.com, only after Yes/No. Not BitMidi. Downloads stay in gitignored `incoming/` until Add to chart.
- Covers are label property and must stay out of the GitHub repo (`artwork/` is gitignored). Deezer, not iTunes.
- Preview synth is not the Boss kit. Do not play audible audio in tests.
- Space must still type a space when an Entry, TEntry, or Text has focus (ranges, note, search).
- Click-to-seek and the loop control must be very user-friendly. Not implemented yet.

Locked typing values (start × length), bar 1 is the first bar of the file:

- Thunderstruck: intro 1×91, P1 92×12, P1 fill 84×1, P2 142×16, P2 fill 157×1, ending 158×5. 162 measures, 4/4, tempo map starts at 134 BPM.
- Panama: intro 1×42, P1 43×8, P1 fill 41×1, P2 83×20, P2 fill 120×1, ending 120×5. 124 measures, 142 BPM, 4/4. P2 fill is the first bar of the ending.
- Good Times Bad Times: intro 1×17, P1 18×5, P1 fill 17×1, P2 23×8 (bridge, not the solo 36–43), P2 fill 67×1, ending 49×20. 68 measures. Chart tempo 95; file is about 91 BPM.
- Ain't Talkin' 'Bout Love: intro 1×24, P1 25×8, P1 fill 26×1 (inside P1), P2 65×16, P2 fill 124×1, ending 121×11. 131 measures. Many tempo changes starting at 135 BPM. Set one tempo near 139 before converting. Do not rewrite the file. Kit bars 3–8 silent.
- Communication Breakdown: intro 1×24, P1 25×12, P1 fill 24×1, P2 63×14, P2 fill 112×1, ending 89×28. 116 measures, 175 BPM.
- Ego Brain: intro 1×18, P1 19×8, P1 fill 18×1, P2 45×10, P2 fill 66×1, ending 67×1. 67 measures, 129 BPM, 12/8. One file bar is one 12/8 measure (6 quarter notes). File ending is bar 67. Bars 1–7 silent. Artist folder misspelled `Sytem of a Down`; filename contains a real em dash.
- Why Don't You Get a Job: intro 1×17, P1 18×15 (bars 18–32, chorus before the jingle bell), P1 fill 17×1, P2 33×16 (bars 33–48, jingle-bell chorus), P2 fill 48×1, ending 74×1. 74 measures, 107 BPM. Note in ready.json: "Pattern 1 is the chorus before the jingle bell. Pattern 2 is the jingle-bell chorus starting at bar 33. They are not the same bars."
- Ramble On: intro 1×29, P1 30×8, P1 fill 63×1 (inside a later unstored chorus, not next to P1), P2 86×13, P2 fill 98×1, ending 99×9. 107 measures, 100 BPM, 4/4. Intro kit is silent. Silent bars are acoustic sections.
- Americana (saved, not in EXPECTED): intro 1–27, P1 28–43, P1 fill bar 27, P2 46–53 (break plus the chorus after it), P2 fill bar 53, ending 54–60. File: `The Offspring/Offspring (The) — Americana (3) [MIDIfind.com] (2).mid`. Meter changes (4/4, 2/4 at bar 48, 4/4 at bar 49), 75 BPM, 60 measures.

## Work done

- Song-form names (Verse, Chorus, Guitar solo, Bridge) along the top of the finder chart, plus Intro / Pattern 1 / Pattern 2 / Ending. Labels under those names show `bar N`. Vertical lines mark section starts. Zoom steps (6, 10, 16, 24, 36, 52), default step 10. Ctrl+MouseWheel zooms.
- Playback works from a wav file. `winsound` `SND_ASYNC|SND_MEMORY` raises `RuntimeError: Cannot play asynchronously from memory`. The working path writes `incoming/preview-{token}.wav` and plays it. The user confirmed playback works.
- Left song list is a PanedWindow with horizontal scrollbars so long names can be read.
- Album art banner sits below the chart and to the right of the section Play buttons. Deezer covers, 200px PNG cache in gitignored `artwork/`.
- Pattern 1 and Pattern 2 are different ranges on every saved song and in `suggest()`. Why Don't You Get a Job and Americana were the two that had been identical.
- RC-10R kit suggestion from the MIDI channel-10 program, else song context. Shown as a row and prepended by Copy ranges. Verified: Thunderstruck Live, Panama Rock, GTBT Rock, Ain't Talkin' Rock, Communication Breakdown Rock, Ego Brain Metal, Why Don't You Get a Job Rock, Ramble On Live, Americana Rock.
- Space bar, written in `scripts/find_drum_midi.py` and not yet in the running window: `bind_all("<Key-space>", on_space)`. Ignored while an Entry, TEntry, or Text is focused, and while preparing. Idle plays the whole drum track (`play("all")`). Playing pauses. Paused resumes the same section. Pause uses MCI `winmm.mciSendStringW` (`open` waveaudio alias `drum`, `play`, `pause`, `resume`, `seek drum to start`, `stop`, `close`) because winsound cannot pause or seek. Pause keeps the wav and the playhead. `stop()` still purges, closes the alias, and deletes `incoming/preview-*.wav`.
- Silent check passed: open/pause/resume/close on a silent wav, and space with the note field focused returned None without starting playback. `unique_path` was briefly merged into `close_preview` and then restored.

## Open

- Relaunch the finder so space-bar pause is what the user actually gets. The window launched for kit suggestions (pid 25324, shell 941854) predates the MCI pause code.
- Click any bar to reset playback to that position. Not built. The chart canvas has no click binding. Playback still renders one slice and cannot seek inside it except restart-to-start for loops.
- A user-friendly loop from a specific bar for a number of bars. Not built. Pattern and fill loops still restart their whole rendered slice.
- HTML chart has no covers, section lines, zoom, part bar labels, or kit suggestion. Not requested.
- Americana saved ranges were not re-checked against the commercial chart. The half-time warning remains.
- Do not commit. Do not strip honks, assign the remaining excluded files, flatten tempo, or drive Rhythm Converter.

## Context for a future agent

Continue in `c:\Users\irsfr\OneDrive\Desktop\MidiTool\miditool`. Python is `%LOCALAPPDATA%\Programs\Python\Python312\python.exe` (and `pythonw.exe` for the desktop shortcut). PowerShell rejects `&&`. The finder is `scripts/find_drum_midi.py`. Launch:

`& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" "c:\Users\irsfr\OneDrive\Desktop\MidiTool\miditool\scripts\find_drum_midi.py"`

Killing a finder reports exit 4294967295. That is the killed process. Do not play audible audio in tests. Headless `Finder(); update_idletasks(); destroy()` is the reliable check. Computer-use screenshots often miss the finder. Do not click the user's browser or a meeting.

The user asked, in order, and the last two are unfinished: space bar plays and pauses the track; click any bar and playback resets to that position; add a loop from a specific bar for a number of bars, and make it very user-friendly.

Space is already coded. `on_space` is around line 1235, `pause_playback` / `resume_playback` follow it, and `audio_command` / `open_preview` / `pause_preview` / `resume_preview` / `restart_preview` / `close_preview` sit just above `unique_path`. Alias is `drum`. Do not use `SND_MEMORY`. Do not delete the preview wav on pause. A text field must still receive spaces. The silent MCI test already passed, so do not repeat a long audible test. Relaunch after the remaining UI work.

Click and loop are the next edit. A friendly layout is a Loop checkbox on the transport row (next to Play drums and Stop) with "from bar" and "bars" fields. Clicking the chart (`scripts/preview_ui.py` `SectionChart`, pad 28, `self.step`, plot from y=72) should map x to a 1-based bar, move the playhead there, and start or continue playback from that bar. If Loop is on, repeat from the from-bar for the given number of bars, and show that span on the chart. Prefer seeking one rendered song wav with MCI (`set drum time format milliseconds`, `seek drum to <ms>`, `play drum`) so a click does not show "Preparing…" again. The first render of a song can still take a few seconds. Looping patterns and fills must keep working. Space pause/resume must still freeze and continue the same offset after a seek.

`play()` still toggles the same key by calling `on_stop()`, which closes MCI and deletes the wav. A seek or pause must not take that path. `tick_playhead` restarts a looping slice with `restart_preview()` when elapsed passes the slice duration. A bar-range loop needs a seek to the loop start in milliseconds instead of always seeking to the start of the file, unless the wav itself is only the loop.

Other current behavior to leave alone: kit row above the six division rows; Play buttons are grid `row + 2`; `recommend_kit` in `scripts/suggest_sections.py`; album art via `scripts/album_art.py` and a queue polled by `poll_covers` (do not call `root.after` from a worker thread); AppUserModelID `MidiTool.DrumFinder` before `tk.Tk()`; desktop shortcut `C:\Users\irsfr\OneDrive\Desktop\Drum MIDI finder.lnk`.

Chart rebuild, only if ready.json changes: the Python 3.12 exe and `scripts\build_section_chart.py`. A new id prints "new song, using the map". EXPECTED mismatch raises SystemExit. `format_catalog` keeps two-int ranges on one line and preserves CRLF. `json.dumps` must use `ensure_ascii=False`.

Earlier summaries, do not overwrite: `docs/chat-summaries/2026-11-10-2240-rc10r-midi-section-assignment.md`, `2026-11-10-2355-rc10r-section-charts-playback.md`, `2026-11-11-1313-drum-midi-finder.md`, `2026-11-11-1506-finder-covers-chart-zoom.md`. The 1506 file predates the pattern split, kit suggestion, and space-bar work.
