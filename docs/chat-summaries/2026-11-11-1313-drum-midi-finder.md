# Drum MIDI finder, part labels, and playback fix

- Saved: 2026-11-11T13:13:00-07:00
- Source: current chat
- Project: c:\Users\irsfr\OneDrive\Desktop\MidiTool\miditool

Earlier in this same chat: labeled RC-10R section charts and the browser drum player. That portion is already saved in `docs/chat-summaries/2026-11-10-2355-rc10r-section-charts-playback.md`. This file covers the finder GUI through the part-label and play-button fix. The finder window with those fixes was left running.

## Goal

A desktop tool that searches Midifind.com or MIDIWORLD.com for a free drum MIDI, parses it the same way as the section charts, suggests RC-10R loop ranges for a cover, shows the bar chart with a drum player and playhead, and can save a confirmed song onto the chart. Song-form names (verse, chorus, guitar solo) should sit above the pedal ranges. Play must actually start.

## Outcome

The finder is `scripts/find_drum_midi.py`. It searches only those two sites after a Yes/No confirm, downloads into gitignored `incoming/`, suggests loop ranges for new files, draws a dark chart with two label rows, and plays drums through a WAV file. Saved songs stay on their locked ranges. Add to chart copies the MIDI under the drum folder, appends `songs/ready.json`, and rebuilds `docs/charts/index.html`. The play button had been stuck on "Preparing the drum track…" because Windows refused an in-memory async sound. It now writes `incoming/preview-*.wav` and plays that file. Part names are inferred from drum grooves, not from MIDI markers. None of this later work is committed.

## Decisions and constraints

- Eight ready songs only. Do not assign Back in Black, Whole Lotta Love, ATWA, Americana, Heartbreaker, or Vicinity of Obscenity until tempo, meter, and length match. They stay in `songs/ready.json` `excluded`.
- Intro is the opening through the bar before the first chorus. Pattern 1 is that whole first chorus and loops. Pattern 2 is the whole bridge, or the second chorus when there is no bridge, and loops. Ending is the whole outro, including ritard. Fills are one-bar exits and may overlap other divisions. Pedal path is intro once, Pattern 1 looping, Pattern 2 looping, ending once. No third pattern.
- Bar numbers are each MIDI file's own measures, starting at 1. Songsterr supplies names, bar counts, tempo, and meter only. Do not copy tab notes. Rhythm Converter has no CLI and must not be automated. The user types start × length by hand. Prefer type 0 MIDI. Ranges may overlap.
- Good Times Bad Times Pattern 2 is the bridge (bars 23–30). Solo 36–43 is the unused alternate.
- Ain't Talkin' 'Bout Love has many tempo changes. Set one tempo near 139 before converting. Do not rewrite the file.
- Ego Brain is 12/8. One file bar is one 12/8 measure. File ending is bar 67.
- Why Don't You Get a Job: Pattern 2 is the same range as Pattern 1 (18–73).
- Ramble On Pattern 1 fill is bar 63, inside an unstored later chorus, not next to Pattern 1. Silent bars are acoustic sections.
- Do not strip honk notes unless asked. Do not flatten Ain't Talkin' tempo or rewrite MIDI unless asked. Do not commit or push unless asked. Do not edit the plan file. Do not hand-edit `docs/charts/index.html`.
- Search only Midifind.com or MIDIWORLD.com. Not BitMidi. Search and download only after Yes/No. Do not write `songs/ready.json` until the user confirms Add to chart.
- Suggestions are for new downloads only. The eight saved songs keep the locked ranges below. The user still types the numbers into Rhythm Converter.
- Song-part names are chart labels only, not a second pedal map.

Locked typing values (start × length):

- Thunderstruck: intro 1×91, P1 92×12, P1 fill 84×1, P2 142×16, P2 fill 157×1, ending 158×5. 162 measures, 4/4, tempo map starts at 134 BPM.
- Panama: intro 1×42, P1 43×8, P1 fill 41×1, P2 83×20, P2 fill 120×1, ending 120×5. 124 measures, 142 BPM, 4/4. P2 fill is the first bar of the ending.
- Good Times Bad Times: intro 1×17, P1 18×5, P1 fill 17×1, P2 23×8, P2 fill 67×1, ending 49×20. 68 measures, chart tempo 95.
- Ain't Talkin' 'Bout Love: intro 1×24, P1 25×8, P1 fill 26×1 (inside P1), P2 65×16, P2 fill 124×1, ending 121×11. 131 measures, tempo map starts at 135 BPM. Kit bars 3–8 are silent.
- Communication Breakdown: intro 1×24, P1 25×12, P1 fill 24×1, P2 63×14, P2 fill 112×1, ending 89×28. 116 measures, 175 BPM.
- Ego Brain: intro 1×18, P1 19×8, P1 fill 18×1, P2 45×10, P2 fill 66×1, ending 67×1. 67 measures, 129 BPM, 12/8. Bars 1–7 silent. One 12/8 bar is 6 quarter notes.
- Why Don't You Get a Job: intro 1×17, P1 18×56, P1 fill 17×1, P2 18×56, P2 fill 73×1, ending 74×1. 74 measures, 107 BPM.
- Ramble On: intro 1×29, P1 30×8, P1 fill 63×1, P2 86×13, P2 fill 98×1, ending 99×9. 107 measures, 100 BPM, 4/4. Intro kit is silent.

## Work done

- Chart generator `scripts/build_section_chart.py` parses kit notes only, builds measures, paints intro / pattern 1 / pattern 2 / ending, and locks the eight songs in `EXPECTED`. A new id is allowed and is not locked. Templates live in `docs/charts/page.template.html`, `song.template.html`, and `chart.css`. Generated page is `docs/charts/index.html`. Browser player is hand-written `docs/charts/player.js` (`?v=2`). Rebuild with the Python 3.12 exe below. Do not hand-edit the HTML.
- Honks identified and left in the files: Good Times Bad Times note 56 cowbell; Why Don't You Get a Job note 83 jingle bell and note 69 cabasa; numbered grooves use note 26 metronome bell plus note 22. Whole Lotta Love congas/bongos are not honks.
- `scripts/find_drum_midi.py` is the dark GUI. Left side lists saved songs and search results. Right side is the chart, range fields, note, Copy ranges, and Add to chart. Window about 1180×840. Saved ranges are display-only. New downloads are editable before Add to chart.
- Search: Midifind needs the homepage fetched first with a full Chrome User-Agent and the cookie jar reused, or `/search/?q=` returns 403. Query max 30 characters. MIDIWORLD search is the song title only at `https://www.midiworld.com/search/?q=`. Artist plus title returns nothing; rank with the artist. Allowed hosts: midifind.com, www.midifind.com, midiworld.com, www.midiworld.com. Reject other redirects. Downloads stay in `incoming/` until Add to chart.
- Add to chart confirms, copies the MIDI into the artist folder under `drumRoot` (case-insensitive folder match), upserts `songs/ready.json` (refuses the eight locked ids), rebuilds the HTML, and rolls back the json and a newly copied MIDI if the rebuild fails. `format_catalog` keeps two-int ranges on one line and preserves CRLF. `song_id` strips apostrophes so "Ain't Talkin' 'Bout Love" stays `aint-talkin-bout-love`.
- `scripts/suggest_sections.py` `suggest()` picks loop ranges for new files only. Pattern 1 is one loop of the densest returning groove. Pattern 2 is a later contrast or the same bars. Intro is everything before Pattern 1. Ending is the closing phrase. Fills are one bar. A family whose first run starts after halfway through the song cannot be Pattern 1.
- `song_parts()` labels Verse, Chorus, Guitar solo, Bridge, Break, Intro, and Outro from drum families. The eight files have no useful section markers. If one groove dominates, the song is split into a phrase grid. Runs shorter than 4 bars are absorbed, and a leading run shorter than 4 bars is folded into the next section so a one-bar "Intro" does not stay.
- `scripts/preview_ui.py` draws the dark canvas. Part names at y=14 (`#e6d3a8`). RC labels such as "Intro  1 × 91" at y=36. Playhead is a canvas line every 40 ms. `render_section` synthesizes 16 kHz mono 16-bit audio in pure Python.
- Playback fix: `winsound` with `SND_ASYNC | SND_MEMORY` raised `RuntimeError: Cannot play asynchronously from memory`, so the status never left "Preparing the drum track…". `begin_playback` now writes `incoming/preview-{token}.wav` and uses `SND_ASYNC | SND_FILENAME | SND_NODEFAULT`. `stop()` purges and deletes `incoming/preview-*.wav`. A silent file played and purged without that error. A whole song can still sit on "Preparing…" for a few seconds while the synth runs.
- Desktop shortcut `C:\Users\irsfr\OneDrive\Desktop\Drum MIDI finder.lnk` targets `pythonw.exe` beside Python 3.12, arguments the quoted finder script, working directory the repo, icon `assets/drum-finder.ico`. The icon is a drawn pair of sticks and a drum key, not the photo, because the photo is unreadable at 16 and 32 px. `SetCurrentProcessExplicitAppUserModelID("MidiTool.DrumFinder")` must run before `tk.Tk()`, then `iconbitmap`.
- `.gitignore` includes `__pycache__/`, `*.pyc`, and `incoming/`.
- Pushed commits on `main` stop at `2ce1089` (browser playhead). Remote `https://github.com/irsfrankland/miditool.git`. Template extraction, the finder, the icon, `.gitignore`, and both chat summaries are local and uncommitted. There is no `.gitconfig`. Do not write git config. Earlier commits used one-shot `-c user.name="irsfrankland" -c user.email="irsfrankland@users.noreply.github.com"`. `gh` is not installed.

## Open

- The updated finder was relaunched and left running (process started 2026-11-11 13:10 local). The user has not confirmed that Play is audible or that the part labels look right on screen.
- The leading short-run fold in `_coalesce` was not re-printed against the eight songs after the edit, so a leftover one-bar Intro label is possible.
- Full-song synth is pure Python and can be slow. If Play still hangs after the filename fix, the synth loop is the next place to look, not winsound.
- The chart page after the CSS/template split was not reopened in a browser.
- Do not commit unless asked. Do not strip honks, assign the six excluded files, flatten tempo, or drive Rhythm Converter.

## Context for a future agent

Continue from the drum-MIDI finder, not from a blank chart. The user covers songs on a Boss RC-10R from drum MIDIs and types start × length into Rhythm Converter by hand. Eight songs are locked in `songs/ready.json` and in `EXPECTED` inside `scripts/build_section_chart.py`. New songs may be added. Excluded files stay excluded until tempo, meter, and length match. Do not rewrite MIDI, strip honk notes, or automate Rhythm Converter.

Python is `%LOCALAPPDATA%\Programs\Python\Python312\python.exe`. `python` / `py` are Store stubs. `pythonw.exe` is what the desktop shortcut uses. PowerShell rejects `&&`. No Pillow, no Node, no `gh`. Drum MIDIs live outside the repo at `C:\Users\irsfr\OneDrive\Desktop\MidiTool\Drum Tracks\Artists`. `songs/ready.json` `drumRoot` points there. Ego Brain's filename contains a real em dash, and its artist folder is misspelled `Sytem of a Down`.

Run the finder with the Python exe above and `scripts\find_drum_midi.py`. Playback writes a WAV under `incoming/` and plays it with `SND_FILENAME`. Part names come from `suggest_sections.song_parts` and are drawn above the RC labels in `preview_ui.SectionChart.draw`. Saved songs show locked ranges. New downloads get `suggest()` ranges the user can edit before Add to chart. Search only Midifind and MIDIWORLD, and only after the user confirms.

Latest finder launch was clean as of this summary. If Play is stuck on "Preparing…", check whether the process is an old window from before the WAV-file fix. A few seconds of "Preparing…" on a long song is the synth, then audio and the red playhead should start. Do not commit unless asked.
