"""Rehearse the RC-10R footswitches against the song's pedal ranges.

The switches follow the RC-10R owner's manual. Rhythm: press to start (intro,
then PTN 1), press for a fill, hold two seconds for a fill that changes
pattern, press twice for a fill and then the ending. While stopped, holding
it turns SYNC on or off. Every change waits for the next bar line.

The loop switch only shows what the looper would be doing. Nothing is
recorded. An empty track records, then plays, then overdubs; holding two
seconds undoes the recording or the last overdub and holding again redoes
the overdub; pressing twice stops; holding while stopped clears the phrase.
With SYNC on, PTN 1 records or plays TRK 1 and PTN 2 records or plays TRK 2.

The machine only decides which range plays next. The finder plays the
drums and tells the machine when a bar or a range finishes.
"""

from __future__ import annotations

import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk

IMAGE = Path(__file__).resolve().parents[1] / "assets" / "rc10r-pedal.png"
CODES = {
    "idle": "---",
    "intro": "INT",
    "pattern1": "P1",
    "pattern1Fill": "F1",
    "pattern2": "P2",
    "pattern2Fill": "F2",
    "ending": "END",
}
NAMES = {
    "pattern1": "Pattern 1",
    "pattern2": "Pattern 2",
}
LOOPS = {"pattern1", "pattern2"}
FILLS = {"pattern1Fill": "pattern1", "pattern2Fill": "pattern2"}
OTHER = {"pattern1": "pattern2", "pattern2": "pattern1"}
LOOP_WORDS = {"rec": "REC", "play": "PLAY", "dub": "DUB"}
PHRASE = "01"

LCD_TEXT = "#f2f4ff"
RING_OFF = "#2a2b31"
PART_OFF = "#3a3c44"
ORANGE = "#ff8a1f"
LIGHT_BLUE = "#5fd3ff"
FLASH_SECONDS = 0.8
ON_TIME_MS = 40
PART_LABELS = (
    ("INTRO", {"intro"}),
    ("PTN 1", {"pattern1", "pattern1Fill"}),
    ("PTN 2", {"pattern2", "pattern2Fill"}),
    ("ENDING", {"ending"}),
)
GREEN = "#3ee07a"
BLUE = "#3d8bff"
RED = "#ff3b30"
AMBER = "#ffb347"
YELLOW = "#ffe14a"


class PedalMachine:
    def __init__(self, divisions: dict[str, list[int]] | None = None) -> None:
        self.divisions = divisions or {}
        self.state = "idle"
        self.pending: str | None = None
        self.after_fill = "pattern1"
        self.sync = False
        self.tracks = {1: False, 2: False}
        self.dubbed = {1: False, 2: False}
        self.undone = {1: False, 2: False}
        self.track = 1
        self.loop = "off"
        self.note = ""
        self.now: float | None = None
        self.bar_ms = 0.0
        self.rec_start: dict[int, float | None] = {1: None, 2: None}
        self.length = {1: 0, 2: 0}
        self.cycle = {1: 0.0, 2: 0.0}
        self.result = ""

    def reset(self) -> None:
        """Stop the drums. A synced loop stops with them."""
        if self.sync:
            self._stop_loop()
            self.track = 1
        self.state = "idle"
        self.pending = None

    def stop_all(self) -> None:
        self.reset()
        self._stop_loop()
        self.track = 1

    def _stop_loop(self) -> None:
        if self.loop == "rec":
            self._close_rec(self.track, pressed=False)
        self.loop = "off"

    def span(self, key: str) -> tuple[int, int]:
        start, end = self.divisions[key]
        return start, end

    def has_intro(self) -> bool:
        start, end = self.span("intro")
        return end >= start and not (start == end == self.span("pattern1")[0])

    def start(self) -> str:
        self.pending = None
        self.note = ""
        self.state = "intro" if self.has_intro() else "pattern1"
        if self.state == "pattern1":
            self._enter("pattern1", fresh=True)
        return self.state

    # Rhythm switch

    def rhythm_tap(self) -> str | None:
        """Start when stopped, otherwise ask for a fill at the next bar."""
        if self.state == "idle":
            return "start"
        if self.state in LOOPS and self.pending is None:
            self.pending = "fill"
            self.note = ""
            return "pending"
        return None

    def rhythm_hold(self) -> str | None:
        """Change pattern at the next bar. When stopped, turn synced mode on or off."""
        if self.state == "idle":
            return "sync" if self.set_sync(not self.sync) else None
        if self.state in LOOPS and self.pending != "end":
            self.pending = "switch"
            self.note = ""
            return "pending"
        return None

    def rhythm_double(self) -> str | None:
        if self.state in ("idle", "ending"):
            return None
        self.pending = "end"
        self.note = ""
        return "pending"

    # Loop switch (shown only)

    def loop_tap(self) -> str | None:
        if self.sync and self.state == "idle":
            return "start"
        if self.sync and self.state == "intro":
            return None
        self.note = ""
        track = self.track
        if self.loop == "off":
            self.loop = "play" if self.tracks[track] else "rec"
            if self.loop == "rec":
                self._begin_rec(track)
            elif self.now is not None:
                self.cycle[track] = self.now
        elif self.loop == "rec":
            self.loop = "play"
            self._close_rec(track)
        elif self.loop == "play":
            self.loop = "dub"
            self.dubbed[track] = True
            self.undone[track] = False
        else:
            self.loop = "play"
        return "loop"

    def loop_hold(self) -> str | None:
        """Two seconds: Undo or Redo while playing or overdubbing, clear the phrase while stopped."""
        track = self.track
        if self.loop == "off":
            if not any(self.tracks.values()):
                return None
            self.clear_tracks()
            self.note = "PHRASE CLEARED"
            return "loop"
        if self.loop not in ("play", "dub") or not self.tracks[track]:
            return None
        self.loop = "play"
        if self.undone[track]:
            self.undone[track] = False
            self.note = "REDO"
        elif self.dubbed[track]:
            self.undone[track] = True
            self.note = "UNDO"
        else:
            self.tracks[track] = False
            self.length[track] = 0
            self.loop = "rec" if self.sync and self.state in LOOPS else "off"
            if self.loop == "rec":
                self._begin_rec(track, on_bar=True)
            self.note = "UNDO"
        return "loop"

    def _fresh(self, track: int) -> None:
        self.tracks[track] = True
        self.dubbed[track] = False
        self.undone[track] = False

    def loop_double(self) -> str | None:
        if self.sync and self.state not in ("idle", "ending"):
            self.pending = "end"
            self.note = ""
            return "pending"
        if self.loop != "off":
            self._stop_loop()
            self.note = ""
            return "loop"
        return None

    def set_sync(self, value: bool) -> bool:
        if self.state != "idle" or self.loop != "off":
            return False
        self.sync = value
        self.track = 1
        self.note = "SYNC ON" if value else "SYNC OFF"
        return True

    def clear_tracks(self) -> None:
        self.tracks = {1: False, 2: False}
        self.dubbed = {1: False, 2: False}
        self.undone = {1: False, 2: False}
        self.rec_start = {1: None, 2: None}
        self.length = {1: 0, 2: 0}
        self.result = ""
        self.track = 1
        if self.state == "idle":
            self.loop = "off"

    # Loop timing, counted in bars played since Start along the pedal's path

    def set_clock(self, now: float | None, bar_ms: float = 0.0) -> None:
        self.now = now
        if bar_ms:
            self.bar_ms = float(bar_ms)

    def _begin_rec(self, track: int, on_bar: bool = False) -> None:
        """Synced recordings begin on the pattern's first bar; otherwise on the press."""
        self._fresh(track)
        self.length[track] = 0
        if self.now is None:
            self.rec_start[track] = None
            return
        start = float(round(self.now)) if on_bar else self.now
        self.rec_start[track] = start
        self.cycle[track] = start
        if not on_bar:
            self.result = f"TRK {track} started recording {self._miss(self.now - round(self.now))}."

    def _close_rec(self, track: int, pressed: bool = True) -> None:
        """The RC-10R rounds the loop to whole bars, so report how far off the press was."""
        start = self.rec_start[track]
        self.rec_start[track] = None
        if start is None or self.now is None:
            return
        elapsed = self.now - start
        bars = max(1, round(elapsed))
        self.length[track] = bars
        words = f"TRK {track} is {bars} bar{'' if bars == 1 else 's'}"
        if pressed:
            words += f", closed {self._miss(self.now - round(self.now))}"
        self.result = f"{words}. {self._fit(track, bars)}".strip()

    def _miss(self, bars_off: float) -> str:
        ms = round(bars_off * self.bar_ms)
        if abs(ms) <= ON_TIME_MS:
            return f"on the bar line ({ms:+d} ms)"
        return f"{abs(ms)} ms {'late' if ms > 0 else 'early'}"

    def target(self, track: int) -> tuple[str, int] | None:
        """The pattern a track should line up with, and its length in bars."""
        if self.sync:
            key = "pattern1" if track == 1 else "pattern2"
        elif self.state in LOOPS:
            key = self.state
        else:
            key = FILLS.get(self.state, "pattern1")
        if key not in self.divisions:
            return None
        start, end = self.span(key)
        return (key, end - start + 1) if end >= start else None

    def _fit(self, track: int, bars: int) -> str:
        target = self.target(track)
        if target is None:
            return ""
        key, size = target
        name = NAMES[key]
        if bars == size:
            return f"That matches {name}."
        if size % bars == 0:
            return f"It plays {size // bars} times per {name} ({size} bars), so it stays lined up."
        if bars % size == 0:
            return f"It covers {name} {bars // size} times, so it stays lined up."
        return f"{name} is {size} bars, so this loop will drift against it."

    def _recorded(self) -> float | None:
        start = self.rec_start[self.track]
        if self.loop != "rec" or start is None or self.now is None:
            return None
        return max(0.0, self.now - start)

    def loop_bar(self) -> tuple[int, int] | None:
        """Where the current track is: (bar, length). Length is the target while recording."""
        track = self.track
        recorded = self._recorded()
        if recorded is not None:
            target = self.target(track)
            return int(recorded) + 1, target[1] if target else 0
        length = self.length[track]
        if self.loop in ("play", "dub") and length and self.now is not None:
            return int((self.now - self.cycle[track]) % length) + 1, length
        return None

    def loop_progress(self) -> float:
        track = self.track
        recorded = self._recorded()
        if recorded is not None:
            target = self.target(track)
            return min(1.0, recorded / target[1]) if target else 0.0
        length = self.length[track]
        if self.loop in ("play", "dub") and length and self.now is not None:
            return ((self.now - self.cycle[track]) % length) / length
        return 1.0 if self.loop != "off" else 0.0

    def press_now(self) -> bool:
        """True during the last bar of a recording that should close on the next bar line."""
        recorded = self._recorded()
        target = self.target(self.track)
        if recorded is None or target is None or (self.sync and self.state in LOOPS):
            return False
        return target[1] - 1 <= recorded < target[1]

    def loop_guide(self) -> str:
        track = self.track
        target = self.target(track)
        size = target[1] if target else 0
        name = NAMES[target[0]] if target else ""
        lines = []
        where = self.loop_bar()
        if self.loop == "rec" and where is not None:
            bar = where[0]
            if self.sync and self.state in LOOPS:
                lines.append(f"Recording TRK {track} with {name}: bar {bar} of {size}. "
                             "It closes on its own when the pattern changes.")
            elif size and bar < size:
                lines.append(f"Recording TRK {track}: bar {bar} of {size}. Press LOOP on the bar line after bar {size}.")
            elif size and bar == size:
                lines.append(f"Recording TRK {track}: last bar. Press LOOP on the coming bar line.")
            else:
                lines.append(f"Recording TRK {track}: bar {bar}. Press LOOP on a bar line to close it.")
        elif self.loop in ("play", "dub") and where is not None:
            verb = "Overdubbing" if self.loop == "dub" else "Playing"
            action = "stop overdubbing" if self.loop == "dub" else "overdub"
            lines.append(f"{verb} TRK {track}: loop bar {where[0]} of {where[1]}. "
                         f"Press LOOP to {action}, hold two seconds to undo.")
        elif self.loop == "off" and self.length[track]:
            lines.append(f"TRK {track} holds {self.length[track]} bars. Press LOOP to play it.")
        elif self.state == "idle":
            if self.sync:
                lines.append("TRK 1 will record from the first bar of Pattern 1.")
            else:
                lines.append("Start the rhythm first so loop presses can be timed against its bars.")
        elif self.loop == "off":
            aim = f" Aim for {size} bars to match {name}." if size else ""
            lines.append(f"Press LOOP right on a bar line to record TRK {track}.{aim}")
        if self.result:
            lines.append(self.result)
        return "\n".join(lines)

    # Timing, called by the player

    def bar_done(self) -> str | None:
        """A bar just finished inside the current range. Act on a waiting press."""
        if self.pending is None or self.state not in LOOPS:
            return None
        return self._advance()

    def range_done(self) -> str:
        """The current range reached its last bar."""
        if self.pending is not None and self.state in LOOPS:
            return self._advance()
        if self.state == "intro":
            if self.pending == "end":
                self.pending = None
                self.state = "ending"
            else:
                self.state = "pattern1"
                self._enter("pattern1", fresh=True)
        elif self.state in FILLS:
            if self.pending == "end":
                self.pending = None
                self.after_fill = "ending"
            self.state = self.after_fill
            if self.state != "ending":
                self._enter(self.state)
        elif self.state == "ending":
            self.reset()
        return self.state

    def _advance(self) -> str:
        action = self.pending
        self.pending = None
        if action == "end":
            self.after_fill = "ending"
            self.state = self.state + "Fill"
        elif action == "fill":
            self.after_fill = self.state
            self.state = self.state + "Fill"
        elif action == "switch":
            self.after_fill = OTHER[self.state]
            self.state = self.state + "Fill"
        return self.state

    def _enter(self, state: str, fresh: bool = False) -> None:
        """A synced loop follows the part: Pattern 1 is track 1, Pattern 2 is track 2."""
        if not self.sync:
            return
        track = 1 if state == "pattern1" else 2
        if track == self.track and not fresh:
            return
        if self.loop == "rec" and track != self.track:
            self._close_rec(self.track, pressed=False)
        self.track = track
        self.loop = "play" if self.tracks[track] else "rec"
        if self.loop == "rec":
            self._begin_rec(track, on_bar=True)
        elif self.now is not None:
            self.cycle[track] = float(round(self.now))

    # Text

    def waiting_text(self) -> str:
        if self.pending == "end":
            if self.state in LOOPS:
                return "Fill at the next bar line, then the ending."
            return "The ending plays after this part."
        if self.pending == "fill":
            return f"Fill at the next bar line, then {NAMES[self.state]} again from bar 1."
        if self.pending == "switch":
            return f"Fill at the next bar line, then {NAMES[OTHER[self.state]]} from bar 1."
        if self.state in FILLS:
            if self.after_fill == "ending":
                return "Fill, then the ending."
            return f"Fill, then {NAMES[self.after_fill]} from bar 1."
        if self.state == "intro":
            return "Intro, then Pattern 1."
        if self.state in LOOPS:
            return "Looping. Press for a fill, hold two seconds to change pattern, press twice to end."
        if self.state == "ending":
            return "Ending, then stop."
        if self.sync:
            return "SYNC is on: press the rhythm switch to start. TRK 1 records when PTN 1 starts."
        return "Press the rhythm switch to start. Hold it two seconds while stopped to turn SYNC on."

    def loop_text(self) -> str:
        word = LOOP_WORDS.get(self.loop)
        if word is None:
            word = "STOP" if self.tracks[self.track] else "EMPTY"
        return f"T{self.track} {word}"

    def lcd_lines(self, bar: int | None, beat: int | None, bpm: int | None, meter: str = "4/4") -> tuple[str, str]:
        """The top screen: phrase number, time signature, and tempo, then the part and the looper."""
        first = f"{PHRASE}: {meter:<5}" + (f"\u2669={bpm}" if bpm else "")
        if self.note:
            second = self.note
        else:
            if self.state == "idle" or bar is None:
                part = "SYNC" if self.sync else "READY"
            else:
                part = f"{CODES.get(self.state, '---')} {bar}-{beat or 1}"
            second = f"{part:<8}{self.loop_text():>8}"
        return first[:16].ljust(16), second[:16].ljust(16)


class Switch:
    """Turns mouse presses on a footswitch pad into tap, hold, or tap twice."""

    HOLD_MS = 2000
    DOUBLE_MS = 300

    def __init__(self, widget: tk.Misc, on_tap, on_hold, on_double, on_press, on_release) -> None:
        self.widget = widget
        self.on_tap = on_tap
        self.on_hold = on_hold
        self.on_double = on_double
        self.on_press = on_press
        self.on_release = on_release
        self.hold_job: str | None = None
        self.tap_job: str | None = None
        self.held = False

    def press(self) -> None:
        self.held = False
        self.on_press()
        self.hold_job = self.widget.after(self.HOLD_MS, self._hold)

    def release(self) -> None:
        self.on_release()
        if self.hold_job is not None:
            self.widget.after_cancel(self.hold_job)
            self.hold_job = None
        if self.held:
            return
        if self.tap_job is not None:
            self.widget.after_cancel(self.tap_job)
            self.tap_job = None
            self.on_double()
            return
        self.tap_job = self.widget.after(self.DOUBLE_MS, self._tap)

    def _hold(self) -> None:
        self.hold_job = None
        self.held = True
        if self.tap_job is not None:
            self.widget.after_cancel(self.tap_job)
            self.tap_job = None
        self.on_hold()

    def _tap(self) -> None:
        self.tap_job = None
        self.on_tap()


class PedalWindow:
    """A picture of the RC-10R with live ring lights, screen, and footswitches."""

    PICTURE = 540
    WIDTH = 880
    HEIGHT = 750
    LOOP_RING = (130, 320)
    RHYTHM_RING = (423, 320)
    RING_RADIUS = 68
    RING_START = 215
    RING_SWEEP = 250
    LCD = (54, 99, 333, 194)
    SWITCHES = {"loop": (104, 641), "rhythm": (441, 641)}
    SWITCH_RADIUS = 38
    PADS = {"loop": (12, 480, 270, 736), "rhythm": (270, 480, 528, 736)}
    LOOP_SEGMENTS = 12

    def __init__(self, parent: tk.Misc, controls: dict, title: str, on_close) -> None:
        self.controls = controls
        self.on_close = on_close
        self.window = tk.Toplevel(parent)
        self.window.title("RC-10R rehearsal")
        self.window.configure(bg="#101114")
        self.window.resizable(False, False)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.canvas = tk.Canvas(
            self.window, width=self.WIDTH, height=self.HEIGHT, highlightthickness=0, bg="#101114"
        )
        self.canvas.pack()
        self.photo: tk.PhotoImage | None = None
        if IMAGE.is_file():
            try:
                self.photo = tk.PhotoImage(file=str(IMAGE))
                self.canvas.create_image(0, 0, image=self.photo, anchor="nw")
            except tk.TclError:
                self.photo = None
        side = self.PICTURE + 20
        middle_x = (side + self.WIDTH - 20) / 2
        panel_w = self.WIDTH - side - 20
        self.title = self.canvas.create_text(
            middle_x, 34, text=title, fill="#f3f1eb", font=("Segoe UI", 12, "bold"), width=panel_w, justify="center"
        )

        left, top, right, bottom = self.LCD
        rows = (bottom - top) / 4
        self.lcd = [
            self.canvas.create_text(left + 12, top + rows * 1.25, text="", fill=LCD_TEXT, font=("Consolas", 21, "bold"), anchor="w"),
            self.canvas.create_text(left + 12, top + rows * 2.85, text="", fill=LCD_TEXT, font=("Consolas", 21, "bold"), anchor="w"),
        ]

        self.loop_ring = self._ring(self.LOOP_RING, self.LOOP_SEGMENTS)
        self.rhythm_ring: list[int] = []
        self.rhythm_beats = 0
        cx, cy = self.LOOP_RING
        self.track_labels = [
            self.canvas.create_text(cx, cy - 2 + index * 22, text=f"TRK {index + 1}", fill=PART_OFF, font=("Segoe UI", 12, "bold"))
            for index in range(2)
        ]
        cx, cy = self.RHYTHM_RING
        self.part_labels = [
            self.canvas.create_text(cx, cy - 22 + index * 19, text=text, fill=PART_OFF, font=("Segoe UI", 10, "bold"))
            for index, (text, _states) in enumerate(PART_LABELS)
        ]
        self.flash_until = 0.0
        self.last_note = ""

        r = self.SWITCH_RADIUS + 6
        self.pad_glow = {
            name: self.canvas.create_oval(x - r, y - r, x + r, y + r, outline="", width=4)
            for name, (x, y) in self.SWITCHES.items()
        }
        self.switches = {
            "loop": Switch(
                self.window,
                lambda: controls["loop_tap"](),
                lambda: controls["loop_hold"](),
                lambda: controls["loop_double"](),
                lambda: self._glow("loop", True),
                lambda: self._glow("loop", False),
            ),
            "rhythm": Switch(
                self.window,
                lambda: controls["rhythm_tap"](),
                lambda: controls["rhythm_hold"](),
                lambda: controls["rhythm_double"](),
                lambda: self._glow("rhythm", True),
                lambda: self._glow("rhythm", False),
            ),
        }
        self.pressed: str | None = None
        self.canvas.bind("<ButtonPress-1>", self._mouse_down)
        self.canvas.bind("<ButtonRelease-1>", self._mouse_up)
        self.canvas.bind("<Motion>", self._mouse_move)

        self.canvas.create_text(
            middle_x, 92, text="Click a footswitch on the pedal (hold it two seconds to hold), or use these.",
            fill="#8d8d94", font=("Segoe UI", 9), width=panel_w, justify="center"
        )
        self.buttons: dict[str, ttk.Button] = {}
        spacing = panel_w / 3
        for row, (heading, keys) in enumerate((
            ("Rhythm switch", ("rhythm_tap", "rhythm_hold", "rhythm_double")),
            ("Loop switch (silent: it times your presses against the bars)", ("loop_tap", "loop_hold", "loop_double")),
        )):
            y = 130 + row * 76
            self.canvas.create_text(middle_x, y, text=heading, fill="#c9c7c0", font=("Segoe UI", 9, "bold"))
            for column, (key, text) in enumerate(zip(keys, ("Press", "Hold 2 s", "Press twice"))):
                button = ttk.Button(self.canvas, text=text, command=controls[key], width=11)
                self.canvas.create_window(side + spacing * (column + 0.5), y + 30, window=button)
                self.buttons[key] = button

        self.sync_var = tk.BooleanVar(value=False)
        sync = ttk.Checkbutton(
            self.canvas,
            text="SYNC the loop with the rhythm (light blue)",
            variable=self.sync_var,
            command=lambda: controls["sync"](self.sync_var.get()),
        )
        self.canvas.create_window(side + 4, 296, window=sync, anchor="w")
        self.sync_box = sync
        clear = ttk.Button(self.canvas, text="Clear loops", command=controls["clear"], width=12)
        self.canvas.create_window(middle_x - 66, 336, window=clear)
        stop = ttk.Button(self.canvas, text="Stop all", command=controls["stop"], width=12, style="Accent.TButton")
        self.canvas.create_window(middle_x + 66, 336, window=stop)

        self.waiting = self.canvas.create_text(
            middle_x, 378, text="", fill="#e8e6df", font=("Segoe UI", 10), width=panel_w, anchor="n", justify="center"
        )
        self.canvas.create_text(middle_x, 452, text="Looper", fill="#c9c7c0", font=("Segoe UI", 9, "bold"))
        self.guide = self.canvas.create_text(
            middle_x, 468, text="", fill="#f6d28b", font=("Segoe UI", 10), width=panel_w, anchor="n", justify="center"
        )
        self.ranges = self.canvas.create_text(
            middle_x, 610, text="", fill="#8d8d94", font=("Segoe UI", 9), width=panel_w, anchor="n", justify="center"
        )
        self.last: tuple | None = None
        self.info: dict = {}
        self.show({"state": "idle", "lines": ("READY", ""), "waiting": "", "range": "", "pending": None})
        self.pulse_job: str | None = self.window.after(250, self._pulse)

    def _pulse(self) -> None:
        self.pulse_job = None
        if self.info:
            self.show(self.info)
        self.pulse_job = self.window.after(250, self._pulse)

    def _ring(self, center: tuple[int, int], count: int) -> list[int]:
        """Lit segments in a horseshoe over the top of the ring, filling clockwise from the lower left."""
        cx, cy = center
        r = self.RING_RADIUS
        step = self.RING_SWEEP / count
        gap = min(5.0, step * 0.22)
        return [
            self.canvas.create_arc(
                cx - r, cy - r, cx + r, cy + r,
                start=self.RING_START - (i + 1) * step + gap / 2,
                extent=step - gap,
                style="arc",
                width=13,
                outline=RING_OFF,
            )
            for i in range(count)
        ]

    def _pad_at(self, x: int, y: int) -> str | None:
        for name, (x1, y1, x2, y2) in self.PADS.items():
            if x1 <= x <= x2 and y1 <= y <= y2:
                return name
        return None

    def _mouse_down(self, event) -> None:
        name = self._pad_at(event.x, event.y)
        if name:
            self.pressed = name
            self.switches[name].press()

    def _mouse_up(self, _event) -> None:
        if self.pressed:
            name, self.pressed = self.pressed, None
            self.switches[name].release()

    def _mouse_move(self, event) -> None:
        self.canvas.configure(cursor="hand2" if self._pad_at(event.x, event.y) else "")

    def _glow(self, name: str, on: bool) -> None:
        self.canvas.itemconfigure(self.pad_glow[name], outline=AMBER if on else "")

    def set_title(self, title: str) -> None:
        self.canvas.itemconfigure(self.title, text=title)

    def show(self, info: dict) -> None:
        """info: state, lines, waiting, range, pending, beat, beats, loop_progress, cue, guide, loop, sync."""
        self.info = info
        blink = int(time.perf_counter() * 4) % 2 == 0
        key = tuple(sorted((k, str(v)) for k, v in info.items())) + (blink,)
        if key == self.last:
            return
        self.last = key
        state = info["state"]
        for item, line in zip(self.lcd, info["lines"]):
            self.canvas.itemconfigure(item, text=line)
        self.canvas.itemconfigure(self.waiting, text=info.get("waiting", ""))
        self.canvas.itemconfigure(self.guide, text=info.get("guide", ""))
        self.canvas.itemconfigure(self.ranges, text=info.get("range", ""))

        sync = bool(info.get("sync"))
        self.sync_var.set(sync)
        self.sync_box.state(["disabled"] if state != "idle" or info.get("loop", "off") != "off" else ["!disabled"])

        beats = max(1, min(12, int(info.get("beats") or 4)))
        if beats != self.rhythm_beats:
            for item in self.rhythm_ring:
                self.canvas.delete(item)
            self.rhythm_ring = self._ring(self.RHYTHM_RING, beats)
            self.rhythm_beats = beats
        base = LIGHT_BLUE if sync else GREEN
        beat = int(info.get("beat") or 0)
        for index, item in enumerate(self.rhythm_ring):
            if state == "idle":
                color = base if sync else RING_OFF
            elif index == beat - 1:
                color = RED
            else:
                color = base if index < beat else RING_OFF
            self.canvas.itemconfigure(item, outline=color)
        pending = info.get("pending")
        playing = FILLS.get(state, state)
        upcoming = {"end": "ending", "switch": OTHER.get(playing), "fill": playing}.get(pending or "")
        for item, (_text, states) in zip(self.part_labels, PART_LABELS):
            if upcoming in states and blink:
                color = RED if pending == "end" else AMBER
            elif state in states:
                color = "#ffffff"
            else:
                color = PART_OFF
            self.canvas.itemconfigure(item, fill=color)

        loop = info.get("loop", "off")
        track = int(info.get("track") or 1)
        for index, item in enumerate(self.track_labels):
            self.canvas.itemconfigure(item, fill="#ffffff" if index + 1 == track else PART_OFF)
        note = str(info.get("note") or "")
        if note != self.last_note and note in ("UNDO", "REDO"):
            self.flash_until = time.perf_counter() + FLASH_SECONDS
        self.last_note = note
        flashing = time.perf_counter() < self.flash_until
        loop_color = {"rec": RED, "play": GREEN, "dub": ORANGE}.get(loop)
        lit = round(float(info.get("loop_progress") or 0) * self.LOOP_SEGMENTS)
        cue = bool(info.get("cue"))
        for index, item in enumerate(self.loop_ring):
            if flashing:
                color = LIGHT_BLUE
            elif loop_color is None:
                color = GREEN if info.get("recorded") else RING_OFF
            elif index < max(1, lit):
                color = loop_color
            else:
                color = AMBER if cue and blink else RING_OFF
            self.canvas.itemconfigure(item, outline=color)

    def lift(self) -> None:
        self.window.deiconify()
        self.window.lift()
        self.window.focus_force()

    def close(self) -> None:
        if self.pulse_job is not None:
            self.window.after_cancel(self.pulse_job)
            self.pulse_job = None
        try:
            self.on_close()
        finally:
            self.window.destroy()
