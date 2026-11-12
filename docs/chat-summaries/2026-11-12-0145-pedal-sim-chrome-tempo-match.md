# Pedal sim, Songsterr sync, and tempo matching

- Saved: 2026-11-12T01:45-07:00
- Source: current chat (transcript db2eee80-76ac-49f6-99b5-3f846d15187a)
- Project: c:\Users\irsfr\OneDrive\Desktop\MidiTool\miditool

## Goal
Make the Tkinter drum MIDI finder (`scripts/find_drum_midi.py`) a better tool for playing live with a Boss RC-10R:
- rehearse with the pedal;
- read the chart more easily;
- play along with the Songsterr tab in Chrome;
- edit MIDIs so they match Songsterr's timing;
- open any MIDI from the PC;
- choose whether to keep or replace the source file when saving.

## Outcome
Everything requested is built, tested headlessly, and running in the relaunched finder. Nothing is committed; the last commit is still `96834ec`. Two things have not been tried for real yet: following Chrome in the user's own browser (the unpacked extension must be loaded first), and tempo matching on a song the user really plays.

## Decisions and constraints
- The user picked option 1 for the improvement ideas: a pedal rehearsal simulator.
- The pedal window should look like the real RC-10R: a red body, a blue LCD and white text.
- Source MIDIs no longer need to be kept. Deleting or replacing one only happens when the user chooses it, and the old file goes to the Recycle Bin.
- "Option 1" for editing MIDIs means matching the tempo. The notes stay the same, and each bar is stretched or squeezed to its Songsterr bar length using the line-up shift and the half-time setting.
- Rhythm Converter is still typed by hand and is never automated.
- Honk notes are never stripped.
- Ain't Talkin' only changes tempo when the user runs tempo matching, and the app warns first when tempo changes would be replaced.
- The eight locked songs in `EXPECTED` in `scripts/build_section_chart.py` cannot be replaced after their bars were edited.
- Don't hand-edit `docs/charts/index.html`, don't edit the plan file, and don't commit or push unless asked.
- Tests must not play audible audio.

## Work done
- **YouTube video.** Summarized how the RC-10R pedal works in the video:
  - tap plays a fill and returns to the same pattern;
  - hold plays a fill and then switches part;
  - double-tap plays the ending;
  - synced loop mode.
- **Pedal simulator.** `scripts/pedal_sim.py` was rewritten: a `PedalMachine` state machine, a `Switch` that turns presses into gestures (hold 650 ms, double-tap 300 ms), and a 900×780 `PedalWindow`.
  - Its image `assets/rc10r-pedal.png` is drawn by the new `scripts/draw_pedal.ps1`.
  - The finder opens it from "Rehearse on the pedal".
- **Readability.** Bigger fonts and higher contrast. The window is now 1340x1150 (minimum 1160x900) and the subtitle was removed.
- **Background grid.** The chart in `scripts/preview_ui.py` has a grid that follows the time signature, including compound meters and meter-change labels.
- **Songsterr sync.**
  - `scripts/chrome_bridge.py` is a local HTTP server on 127.0.0.1:47391.
  - `chrome-extension/` is an MV3 extension (`manifest.json`, `background.js`, `content.js`). It reads the Songsterr bar label (minus 8), whether the tab is playing, and the speed.
  - The finder follows Chrome: it seeks when drift is over 300 ms, or on a new bar when drift is over 35 ms. Play, pause and Space are sent back to Chrome.
  - In the finder, this lives in the "Play along with Songsterr in Chrome" checkbox and the "Set up Chrome…" button.
- **Tempo matching.** `scripts/midi_export.py` has `tab_bar_lengths()` and `retime_to_tab()`. The finder has a "Match Songsterr tempo" button that confirms first and can be undone.
  - Test results:
    - Thunderstruck: 4:53 → 4:52 (tab 4:55)
    - Americana, half time: 3:10 → 3:17 (tab 3:16)
    - Ain't Talkin': 3:59 → 3:49 (tab 3:45)
  - Every bar is within 1 ms of its tab bar, and the hits are unchanged.
- **Open MIDI.** The "Open MIDI…" button sits next to Search. It guesses artist and title from "Artist - Title.mid" or from the artist folder.
- **Save window.** It now has a keep or replace choice. Replace uses `to_recycle_bin()` (Windows `SHFileOperationW`), writes over the original path, and puts the ranges .txt into `exports/`. Replace is disabled for "pedal parts only", for missing files, and for locked songs with bar edits.
- `exports/` was added to `.gitignore`.

## Open
- Test Chrome following live: load the unpacked `chrome-extension/` folder in Chrome, then play a Songsterr tab.
- Tempo matching on real songs: check the line-up first, since a weak drum match gives wrong timing.
- Commit all of this only when the user asks.

## Context for a future agent
**Where things are**
- Project: `c:\Users\irsfr\OneDrive\Desktop\MidiTool\miditool`. The main app is `scripts/find_drum_midi.py`.
- Drum MIDIs are in `C:\Users\irsfr\OneDrive\Desktop\MidiTool\Drum Tracks\Artists` (`drumRoot` in `songs/ready.json`).
- Python 3.12 is at `%LOCALAPPDATA%\Programs\Python\Python312\python.exe`. PowerShell needs `;` instead of `&&`.

**Relaunching the finder**
- Stop only the python processes whose command line contains `find_drum_midi.py`, then start it in the background.
- A killed finder exits with 4294967295, which is normal.

**Headless tests**
- Set `app.INCOMING` and `app.EXPORTS` to temp folders, because the running finder locks `preview-1.wav`.
- Stub `messagebox.askyesno` and the file dialogs, and don't play audio.
- Take screenshots with PowerShell System.Drawing `CopyFromScreen` on a topmost window. PIL is not installed.

**Current state**
All requested features are done and uncommitted on top of `96834ec`.

**Next step**
Wait for the user's feedback after real-world use. Likely follow-ups are Chrome extension setup issues or tuning tempo matching.
