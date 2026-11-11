# RC-10R MIDI section assignment

- Saved: 2026-11-10T23:13:00-07:00
- Source: current chat
- Project: c:\Users\irsfr\OneDrive\Desktop\MidiTool\miditool

## Goal

Build a tool that takes drum MIDI files and drives the Boss RC-10R Rhythm Converter on Windows, assigning each song an intro, Pattern 1, Pattern 1 fill, Pattern 2, Pattern 2 fill, and ending. The parts should follow the song so a guitarist can play a full cover live with a drummer and/or another guitarist, and also loop a section. The first songs are examples. More MIDI files will be added later, and the tool should scan for them.

## Outcome

No importer or Rhythm Converter automation has been written. The repo only contains this summary. Drum MIDIs were analyzed, section ranges were proposed, and one live Assign was observed on the pedal at `F:`. Songs with tempo or meter changes through the body are flagged and skipped. Thunderstruck stays in the assignable set; its outro slowdown is deferred.

## Decisions and constraints

- Use is live covers of the whole song with a drummer and/or another guitarist, plus the ability to loop.
- Intro length is whatever the song needs. It runs from bar 1 through the bar before the first chorus, played once.
- Pattern 1 is the first chorus, the whole section, and it loops. Later choruses that use the same beat go back to Pattern 1.
- Pattern 2 is the bridge when the song has one; otherwise the second chorus or the other distinct section (solo, interlude). It loops.
- The ending is the whole outro.
- Pattern 1 and Pattern 2 fills stay one bar. The user said the fills already identified look accurate.
- The pedal path is intro once, Pattern 1 looping, Pattern 2 looping, ending once. There is no third pattern for a later verse.
- The MIDI file is what gets loaded. Songsterr supplies section names, bar counts, tempo, and meter only. Do not copy tab notes.
- Spotify's audio-analysis endpoint has been closed to new apps since 2024-11-27. It cannot supply measure numbers.
- Rhythm Converter is a GUI app with no published CLI. The pedal's MIDI control changes start, stop, and fill during playback. It does not write rhythm patterns.
- A new MIDI dropped in `Drum Tracks` should be picked up by a scan (path, size, modified time, tempo, meter, bar count, drum channel). Section ranges are a later step. The file stays unassigned until those ranges exist.
- Flag and skip a song when the tempo or the meter changes through the body. It needs a MIDI rewritten at one tempo and one time signature before it can be used. A ritard that is only on the ending does not drop the song. Thunderstruck stays; figure the outro out later.
- Bar numbers below are each MIDI file's own measures, starting at 1.
- Pattern names stored on the pedal are clipped to 8 characters.

## Work done

- Drum files are in `C:\Users\irsfr\OneDrive\Desktop\MidiTool\Drum Tracks`, a sibling of this repo. Artist songs are under `Drum Tracks\Artists\`. Numbered files such as `1_funk_80_beat_4-4.mid` are groove libraries, not songs.
- The pedal mounts as a USB drive. During this chat it was `F:`.
  - `F:\ROLAND\DATA\MEMORY1.RC0` and `MEMORY2.RC0` are phrase memories, XML, 99 memories, null-padded. Tempo is BPM times 10 (`1200` = 120.0). Each memory has `<RHYTHM>` with Genre, Pattern, Kit, Tempo, Level, Intro, PlayMode, StopMode, FadeOutTime, TransitionFill, ReverbLevel.
  - `F:\ROLAND\DATA\RC10R.RC0` is the binary drum-pattern bank (about 2,005,812 bytes). It is not a MIDI file.
  - `F:\ROLAND\DATA\SYSTEM1.RC0` and `SYSTEM2.RC0` are system XML.
  - `F:\ROLAND\WAVE\001_1` through `099_2` are the looper WAVs, two tracks per memory.
- A custom GUI can read and write the drive. Editing `<Pattern>` or `<Kit>` in the memory XML does not create a rhythm. New drum notes have to land in `RC10R.RC0`. That layout is unpublished. Rhythm Converter is the known writer. Do not write `F:` while Rhythm Converter has the drive open.
- One Assign, observed at 11:06 PM, rewrote `RC10R.RC0` in place (same size) and touched `MEMORY2.RC0` only. `MEMORY1.RC0` stayed at 5:53 PM. The two memory files have the same XML. The only difference is the byte after `</database>`: `MEMORY1` is `0x28` (40), `MEMORY2` is `0x29` (41). Treat the higher trailing byte as the newer copy. Names visible in `RC10R.RC0` after that Assign: `T.N.Test`, `LotaLove`, `GoodBadT`, `GetAJob`, `Muricana`, `Vicinity`, `ATWA`, `AC_DC_-_`, `Funk01`, `Funk02`.

### Ranges to use

Thunderstruck, 134 BPM. `Artists\ACDC\AC_DC_-_Thunderstruck.mid`. Stays in the set. The outro slows from 134 toward about 96; leave the ending for later.

- Intro 1–91 (intro, both verses, pre-chorus)
- Pattern 1: 92–103 (chorus 1). Chorus 2 is 126–141 and matches chorus 1
- Pattern 1 fill: 84
- Pattern 2: 142–157 (guitar solo 2)
- Pattern 2 fill: 157
- Ending 158–162 is the outro, including the slowdown. Do not treat that slowdown as decided.

Panama, about 142 BPM. `Artists\Van Halen\Van_Halen_-_Panama.mid` (124 bars in the file and on the chart)

- Intro 1–42
- Pattern 1: 43–50 (chorus 1). Chorus 2 is 67–74, chorus 3 is 112–119
- Pattern 1 fill: 41
- Pattern 2: 83–102 (bridge)
- Pattern 2 fill: 120
- Ending 120–124

Good Times Bad Times. `Artists\Led Zeppelin\Led_Zeppelin_-_Good_Times_Bad_Times.mid`. File is 91 BPM; the chart is 95.

- Intro 1–17 (bar 1 is a single hit)
- Pattern 1: 18–22 (chorus)
- Pattern 1 fill: 17
- Pattern 2: 23–30 (bridge). Solo 36–43 is the alternate if Pattern 2 should be the solo
- Pattern 2 fill: 67
- Ending 49–68 (outro and fade). The chart fade starts near 61; the file stays loud until 68

Communication Breakdown, 175 BPM. `Artists\Led Zeppelin\Led_Zeppelin_-_Communication_Breakdown.mid`. Intro and first chorus match the chart. Later sections can sit a few bars off.

- Intro 1–24
- Pattern 1: 25–36 (chorus)
- Pattern 1 fill: 24
- Pattern 2: 63–76 (guitar solo)
- Pattern 2 fill: 112
- Ending 89–116

Ego Brain, 12/8 at 129 BPM. `Artists\Sytem of a Down\System of a Down — Ego Brain [MIDIfind.com].mid`. One file bar is one 12/8 measure. The artist folder name is misspelled "Sytem".

- Intro 1–18 (bars 1–7 are silent)
- Pattern 1: 19–26 (chorus)
- Pattern 1 fill: 18
- Pattern 2: 45–54 (interlude)
- Pattern 2 fill: 66
- Ending 67. The chart outro is 67–72; this file stops at 67

Why Don't You Get a Job, 107 BPM. Use `Artists\The Offspring\The_Offspring_-_Why_Dont'You_Get_a_Job.mid`. The other copy, `Offspring (The) — Why Don't You Get a Job [MIDIfind.com].mid`, is kick and snare only. The chart has no section markers. Drums do not change after the intro.

- Intro 1–17
- Pattern 1: 18–73
- Pattern 1 fill: 17
- Pattern 2: 18–73 (same beat)
- Pattern 2 fill: 73
- Ending 74

Ramble On. `Artists\Led Zeppelin\Led_Zeppelin_-_Ramble_On.mid`. The kit is silent in the verses, which matches the record.

- Intro 1–29 (drums tacet)
- Pattern 1: 30–37 (chorus 1). Chorus 2 is 54–67
- Pattern 1 fill: 63
- Pattern 2: 86–98 (chorus 3)
- Pattern 2 fill: 98
- Ending 99–107

### Flagged files

- Ain't Talkin' 'Bout Love. `Artists\Van Halen\Van_Halen_-_Ain't_Talkin_Bout_Love.mid`. Within about two bars of the chart, but 89 tempo changes. Skip until the MIDI is one tempo (about 139) and one meter. Ranges that had been drafted, and should not be used until then: intro 1–24 (bars 3–8 silent), Pattern 1 bars 25–32, fill 26, Pattern 2 bars 65–80, fill 124, ending 121–131.
- Vicinity of Obscenity. `Artists\Sytem of a Down\System of a Down — Vicinity of Obscenity [MIDIfind.com].mid`. 75 bars, 115 BPM, no tempo changes, 12 meter changes. Not only the chorus. Bars 13–16 and 38–41 alternate 13/16 and 15/16 at the two verse entrances. Bar 59 is one bar of 6/4, then 4/4 at bar 60, just before the chart's "LI-AR!" break. The chart's first chorus (about bar 30) is plain 4/4.
- Heartbreaker changes meter and tempo. A 4/4 bar count lands on the wrong music.
- Back in Black (`Artists\ACDC\AC_DC_-_Back_In_Black.mid`): 60 bars of one identical groove, 17 silent bars, then that groove again. No chorus, bridge, or outro in the file. File tempo is about 95 BPM.
- Whole Lotta Love: 86 bars in the file against a 126-bar chart. The long middle was cut. From the file only: intro 1–12, Pattern 1 bars 13–32, Pattern 2 bars 33–49, ending 75–86.
- ATWA: 32 bars at 48 BPM. Chart is 65 bars at 85 BPM. Plays about half speed. From the file: intro 1–10, Pattern 1 bars 11–16, Pattern 2 bars 21–28, ending 29–32.
- Americana: full song in half time, 60 bars at 75 BPM against 119 chart bars at 146. After doubling the tempo: intro 1–28, Pattern 1 bars 29–32 (chorus), Pattern 2 bars 46–50 (bridge), ending 54–60.

Still worth doing once before building a writer: the converter's exact field labels (start bar and length versus start and end), the kit list, and whether a long intro (Thunderstruck is 91 bars) is accepted. The drum note map is the last page of the RC-10R Parameter Guide. Notes outside that map are silent on the pedal.

## Open

- No scanner, measure map, or Rhythm Converter automation exists yet.
- Thunderstruck's ending slowdown is undecided. The rest of that song is in range.
- Ain't Talkin' 'Bout Love, Vicinity of Obscenity, and Heartbreaker stay out until the MIDI is one tempo and one meter.
- Back in Black, Whole Lotta Love, ATWA, and Americana still need a file that matches the song's length and tempo.
- Whether Pattern 2 on Good Times Bad Times should be the solo (bars 36–43) instead of the bridge was left open.
- Which phrase memory receives the first song, and which kit number to store, has not been chosen.
- Maximum bars per part in Rhythm Converter has not been tested.
- `RC10R.RC0` pattern-record layout is still unknown. Do not hand-write it.

## Context for a future agent

The user wants full-song RC-10R rhythm memories from drum MIDIs in `C:\Users\irsfr\OneDrive\Desktop\MidiTool\Drum Tracks`, for live covers with a drummer and/or another guitarist. This repo, `c:\Users\irsfr\OneDrive\Desktop\MidiTool\miditool`, has no application code. New files will appear in that drum folder later. Scan for them. Queue them unassigned until title, artist, and six ranges exist.

Assignment rule: intro is the entire opening through the bar before the first chorus; Pattern 1 is that whole chorus; Pattern 2 is the whole bridge, or the second chorus when there is no bridge; the ending is the whole outro; fills stay the one-bar exits already chosen. Skip songs whose tempo or meter changes through the body. Thunderstruck stays despite the outro ritard. Use the ranges under "Ranges to use". Do not assign the flagged files.

The pedal is a USB mass-storage device (`F:` in this chat). An Assign writes the pattern into `F:\ROLAND\DATA\RC10R.RC0` and increments the trailing byte of `MEMORY2.RC0`. `MEMORY1.RC0` was not modified. Rhythm Converter has no CLI. A future tool should prepare a type 0 drum MIDI plus a measure map, then either stop for the GUI clicks or drive the window. Do not write the drive while the converter is running. Next step is the scanner and the measure map for the ready songs, not another guess at the sections.
