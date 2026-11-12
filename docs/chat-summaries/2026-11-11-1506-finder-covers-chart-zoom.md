# Finder covers, section lines, and zoom

- Saved: 2026-11-11T15:06:00-07:00
- Source: current chat
- Project: c:\Users\irsfr\OneDrive\Desktop\MidiTool\miditool

Earlier write-ups: `docs/chat-summaries/2026-11-10-2240-rc10r-midi-section-assignment.md`, `docs/chat-summaries/2026-11-10-2355-rc10r-section-charts-playback.md`, and `docs/chat-summaries/2026-11-11-1313-drum-midi-finder.md`. This file covers the finder after playback was fixed: the resizable song list, album covers, section lines, zoom, and start-bar labels.

## Goal

Make the drum-finder chart easier to read while assigning RC-10R ranges. The user wanted the full song names visible, album art when a song is on the chart, a line at each section start, zoom to spread the bars, and the starting bar printed under names such as Bridge and Chorus.

## Outcome

The finder window does all of that. Playback was confirmed working by the user. The song list is a draggable pane. A 200px album cover sits below the chart and to the right of the section Play buttons, with the album name under the picture. Vertical lines mark where each song part and each pedal range starts. Zoom in, Zoom out, and Ctrl+mouse-wheel change the bar width. Under each part name is a label such as `bar 64`. The finder was relaunched with those labels. None of this is committed.

## Decisions and constraints

- The user confirmed playback works. The preview writes `incoming/preview-*.wav` and plays it with `winsound` `SND_ASYNC | SND_FILENAME | SND_NODEFAULT`. Windows cannot play an in-memory sound asynchronously.
- Album art is not stored in the MIDI files. Look it up from the artist and title. Show it when a song is placed on the chart, below the chart and to the right of the section Play buttons.
- Covers are label property. Cache them locally and keep them out of the GitHub repo.
- iTunes Search was the wrong source. It returned Iron Man 2 for Thunderstruck, a Best Of for Ain't Talkin' 'Bout Love, and a lullaby for Why Don't You Get a Job. Deezer's public search is the source, and live, best-of, tribute, lullaby, karaoke, session, remix, and cover versions are skipped.
- The user still types start × length into Rhythm Converter by hand. Do not automate Rhythm Converter, rewrite MIDI, or strip honk notes.
- Do not commit or push unless asked. Do not hand-edit `docs/charts/index.html`. Do not edit the plan file.
- Intro, Pattern 1, Pattern 2, and Ending stay the pedal path. Song-part names (Verse, Chorus, Bridge, Guitar solo) are chart labels, not a second pedal map. Fills are one bar and may overlap other divisions. No third pattern.
- The original eight songs stay locked to the ranges in the 1313 summary and in `EXPECTED` inside `scripts/build_section_chart.py`. A new song id is allowed and is not locked.

`songs/ready.json` now also has The Offspring — Americana as a saved song (`id` `americana`), and Americana is still listed under `excluded` with the reason that the file is the full song in half time, 60 bars against 119 chart bars. Saved divisions: intro 1–27, pattern 1 28–43, pattern 1 fill bar 27, pattern 2 the same 28–43, pattern 2 fill bar 43, ending 54–60. Note: the meter changes, so a straight bar count can land on the wrong music. File: `The Offspring/Offspring (The) — Americana (3) [MIDIfind.com] (2).mid`. Do not remove either entry unless asked.

## Work done

- `scripts/find_drum_midi.py`: the left song list and the chart are a horizontal `PanedWindow`. Drag the sash to widen the list. Both lists have a horizontal scrollbar. Saved songs stay display-only. New downloads stay editable until Add to chart.
- `scripts/album_art.py`: `cached()` and `cover_for()` ask `https://api.deezer.com/search` and save a 200px PNG plus the album name under `artwork/`. Image hosts must end in `.dzcdn.net`. `artwork/` is in `.gitignore`. A cached cover loads on the UI thread. A miss is fetched in the background and applied through a queue, because `root.after` from a worker before `mainloop` raises `RuntimeError: main thread is not in main loop`.
- Covers checked: Thunderstruck *The Razors Edge*, Panama *1984 (Remastered)*, Good Times Bad Times *Led Zeppelin (Remaster)*, Ain't Talkin' 'Bout Love *Van Halen (Remastered)*, Communication Breakdown *Led Zeppelin (Deluxe Edition)*, Ego Brain *Steal This Album!*, Why Don't You Get a Job *Americana*, Ramble On *Led Zeppelin II (Deluxe Edition)*, Americana *Americana*.
- `scripts/preview_ui.py` `SectionChart`: cream vertical lines at each song-part start and each intro / pattern / ending start. Bar numbers for those starts are bold. Part names are at y=12 and `bar N` is directly under them at y=28. Pedal labels such as `Intro  1 × 91` are at y=48. Plot is y=72 to y=204. Canvas height is 248.
- Zoom steps are 6, 10, 16, 24, 36, and 52 pixels per bar. The default is 10. Bar width is `step - 2`. Zoom keeps the current scroll fraction. Buttons are Zoom out and Zoom in, next to Stop. Ctrl+mouse-wheel over the chart zooms too. Buttons disable at the ends of the range.
- The user said the album-cover placement was well done. They have not sent a separate confirmation of the start-bar labels.

## Open

- The whole finder, templates, icon, `.gitignore` entries, chat summaries, and `artwork/` cache are local. Latest pushed commit known from the earlier summary is `2ce1089` on `main`. Do not commit unless asked. Do not commit `artwork/`.
- Americana is both saved and excluded. The ranges above are what the file contains now. They were not re-checked against the MIDI in this session.
- The HTML chart page was not changed for covers, section lines, or zoom. Those exist on the finder canvas only.
- A long song can still sit on "Preparing the drum track…" for a few seconds while the pure-Python drum synth runs, then play.

## Context for a future agent

Continue from the drum-MIDI finder. The user covers songs on a Boss RC-10R and types start × length into Rhythm Converter. Run the finder with `%LOCALAPPDATA%\Programs\Python\Python312\python.exe` and `scripts\find_drum_midi.py`. `python` and `py` are Store stubs. PowerShell rejects `&&`. Drum MIDIs live at `C:\Users\irsfr\OneDrive\Desktop\MidiTool\Drum Tracks\Artists`.

The chart the user is looking at is `SectionChart` in `scripts/preview_ui.py`, not the generated HTML page. Part names, `bar N` under those names, pedal ranges, vertical start lines, the playhead, and zoom all live there. Album art is `scripts/album_art.py`, shown in the finder below the chart and to the right of the section Play buttons. Playback writes a WAV under `incoming/` and plays that file. Search is still only Midifind and MIDIWORLD, and only after the user confirms. Add to chart is the only path that writes `songs/ready.json`.

Do not rewrite MIDI, strip honk notes, automate Rhythm Converter, or assign the excluded files unless asked. The original eight ranges stay locked. Americana is an extra saved song and is also still in `excluded`. The finder was relaunched after the `bar N` labels. If that window is closed, start it again with the command above.
