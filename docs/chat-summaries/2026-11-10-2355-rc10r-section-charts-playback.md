# RC-10R section charts with drum playback

- Saved: 2026-11-10T23:55:00-07:00
- Source: current chat
- Project: c:\Users\irsfr\OneDrive\Desktop\MidiTool\miditool

## Goal

Turn the confirmed Boss RC-10R section map into labeled measure charts the user can type into Rhythm Converter by hand, then hear each division and follow it with a playhead. The map comes from `docs/chat-summaries/2026-11-10-2240-rc10r-midi-section-assignment.md`.

## Outcome

Eight ready songs have a generated chart page at `docs/charts/index.html`. Each chart shows kit-only hit counts, the intro / Pattern 1 / Pattern 2 / ending / fill ranges, and the start measure and length to type. A Web Audio player in `docs/charts/player.js` plays the drum kit for one division or the whole file, and a red playhead tracks the current bar. Both commits are on `main` at https://github.com/irsfrankland/miditool.git: `19b74ba` (charts) and `2ce1089` (player and playhead).

## Decisions and constraints

- Chart for manual entry. Do not automate the Rhythm Converter window.
- Eight ready songs only. Do not assign Back in Black, Whole Lotta Love, ATWA, Americana, Heartbreaker, or Vicinity of Obscenity until tempo, meter, and length match.
- Intro is the opening through the bar before the first chorus. Pattern 1 is that whole first chorus and loops. Pattern 2 is the whole bridge, or the second chorus when there is no bridge, and loops. Ending is the whole outro, including ritard. Fills stay the already chosen one-bar exits and may overlap other divisions. Pedal path is intro once, Pattern 1 looping, Pattern 2 looping, ending once. No third pattern.
- Bar numbers are each MIDI file's own measures, starting at 1. Songsterr supplies names, bar counts, tempo, and meter only. Do not copy tab notes.
- Prefer type 0 MIDI. Ranges may overlap. Pedal must be on USB. Workflow is File → Open MIDI, set start measure and length, pick a kit, delete the existing user pattern, File → Assign.
- Good Times Bad Times Pattern 2 is the bridge (bars 23–30). Solo 36–43 is the unused alternate.
- Ain't Talkin' 'Bout Love has many tempo changes. Set one tempo near 139 before converting. Do not rewrite the file.
- Ego Brain is 12/8. One file bar is one 12/8 measure. File ending is bar 67.
- Why Don't You Get a Job Pattern 2 is the same range as Pattern 1 (18–73).
- Ramble On Pattern 1 fill is bar 63, inside an unstored later chorus. Silent bars are acoustic sections.
- Playback is drum-kit audio only, not guitar or bass. Honking notes were identified and left in the tracks.
- The preview synth is not the RC-10R kit.

## Work done

- `songs/ready.json` holds the eight maps plus the excluded list. Paths are relative to `C:\Users\irsfr\OneDrive\Desktop\MidiTool\Drum Tracks\Artists`. Ego Brain's folder is misspelled `Sytem of a Down` and the filename uses an em dash. Why Don't You uses `The_Offspring_-_Why_Dont'You_Get_a_Job.mid`.
- `scripts/build_section_chart.py` is a dependency-free Python 3.12 reader and page generator. Interpreter: `%LOCALAPPDATA%\Programs\Python\Python312\python.exe`. `python` on PATH is a Store stub. Rebuild with that interpreter pointed at the script.
- Kit columns count the track whose name contains `drum`, excluding names containing `perc` or `steel`. Thunderstruck has no kit track, so it counts channel 10. Trailing empty bars after the last kit note are dropped. Internal silence is kept. Tempo events do not move bar lines.
- Typing values, start × length: Thunderstruck 1×91, 92×12, fill 84×1, 142×16, fill 157×1, ending 158×5 (162 bars). Panama 1×42, 43×8, fill 41×1, 83×20, fill 120×1, ending 120×5 (124 bars). Good Times Bad Times 1×17, 18×5, fill 17×1, 23×8, fill 67×1, ending 49×20 (68 bars). Ain't Talkin' 'Bout Love 1×24, 25×8, fill 26×1, 65×16, fill 124×1, ending 121×11 (131 bars). Communication Breakdown 1×24, 25×12, fill 24×1, 63×14, fill 112×1, ending 89×28 (116 bars). Ego Brain 1×18, 19×8, fill 18×1, 45×10, fill 66×1, ending 67×1 (67 bars). Why Don't You Get a Job 1×17, 18×56, fill 17×1, Pattern 2 18×56, fill 73×1, ending 74×1 (74 bars). Ramble On 1×29, 30×8, fill 63×1, 86×13, fill 98×1, ending 99×9 (107 bars).
- `docs/charts/player.js` schedules the embedded kit events. Patterns and fills loop. Intro, ending, and the whole track play once. The playhead is a shaded bar, a red line, and a triangle. Status includes the bar number. The chart scroller follows the line. Stop hides it. Checked on Thunderstruck: the line moved from bar 3 to bar 4 on the full track, then sat on bar 148 while Pattern 2 looped, and Stop hid it.
- Honks, left in the files: Good Times Bad Times drum note 56 cowbell, 312 hits, bars 4–68. Why Don't You Get a Job drum note 83 jingle bell, 165 hits, bars 33–74; the same track also has note 69 cabasa. Numbered groove files use note 26 metronome bell and note 22, which is not on the RC-10R map. The RC-10R map starts at note 25; note 26 is Metronome Bell 2.
- `.gitignore` ignores `__pycache__/` and `*.pyc`. There is no `.gitconfig`. Commits used a one-shot author `irsfrankland` / `irsfrankland@users.noreply.github.com` and did not write git config. `gh` is not installed.

## Open

- The six weak files still have no typing numbers.
- Ain't Talkin' 'Bout Love is not flattened to one tempo.
- Honk notes were not removed.
- No type 0 export and no Rhythm Converter automation.
- This summary file is local only.

## Context for a future agent

The user is assigning drum MIDIs from `C:\Users\irsfr\OneDrive\Desktop\MidiTool\Drum Tracks` onto a Boss RC-10R. The earlier analysis is `docs/chat-summaries/2026-11-10-2240-rc10r-midi-section-assignment.md`. This chat built the manual-entry charts and a browser player for the eight ready songs. Do not re-guess ranges. Do not chart or assign the six excluded songs. Do not rewrite MIDI, strip honks, or drive Rhythm Converter unless asked.

Open `docs/charts/index.html` through a local server (`python -m http.server` from the repo root). `file://` was unreliable. The page embeds per-song JSON (`bars` in milliseconds, `events` as `[ms, note, velocity]`) and loads `docs/charts/player.js`. Regenerating the page requires the Python 3.12 path above and rewrites `docs/charts/index.html`; `player.js` is hand-written and is not regenerated. Playhead geometry shared by the generator and the script: pad 46, step 10, bar width 8. Bar n starts at `46 + (n-1)*10`.

Repo remote is `https://github.com/irsfrankland/miditool.git`, branch `main`, latest commit `2ce1089`. Do not update git config. PowerShell on this machine rejects `&&`. Plan file `c:\Users\irsfr\.cursor\plans\midi_section_charts_0abcbc21.plan.md` must not be edited.
