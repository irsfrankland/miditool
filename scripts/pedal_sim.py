"""Rehearse the RC-10R footswitches against the song's pedal ranges.

The rhythm switch works like the pedal: press to start, tap for a fill that
returns to the same pattern, hold for a fill that changes pattern, and tap
twice for the ending. Every change waits for the next bar line.

The loop switch only shows what the looper would be doing (record, play,
overdub, the track for each part, synced mode). Nothing is recorded.

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

LCD_TEXT = "#ffffff"
RING_OFF = "#1d1d20"
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
        self.track = 1
        self.loop = "off"
        self.note = ""

    def reset(self) -> None:
        """Stop the drums. A synced loop stops with them."""
        self.state = "idle"
        self.pending = None
        if self.sync:
            self.loop = "off"
            self.track = 1

    def stop_all(self) -> None:
        self.reset()
        self.loop = "off"
        self.track = 1

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
        if self.loop == "off":
            self.loop = "play" if self.tracks[self.track] else "rec"
            self.tracks[self.track] = True
        elif self.loop == "rec":
            self.loop = "play"
        elif self.loop == "play":
            self.loop = "dub"
        else:
            self.loop = "play"
        return "loop"

    def loop_hold(self) -> str | None:
        if self.loop == "off" and self.tracks[self.track]:
            self.tracks[self.track] = False
            self.note = f"TRACK {self.track} CLEARED"
            return "loop"
        if self.loop in ("play", "dub") and self.tracks[self.track]:
            self.note = "REDO" if self.note == "UNDO" else "UNDO"
            return "loop"
        return None

    def loop_double(self) -> str | None:
        if self.sync and self.state not in ("idle", "ending"):
            self.pending = "end"
            self.note = ""
            return "pending"
        if self.loop != "off":
            self.loop = "off"
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
        self.track = 1
        if self.state == "idle":
            self.loop = "off"

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
                self.state = "ending"
            else:
                self.state = self.after_fill
                self._enter(self.state)
        elif self.state == "ending":
            self.reset()
        return self.state

    def _advance(self) -> str:
        action = self.pending
        self.pending = None
        if action == "end":
            self.state = "ending"
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
        self.track = track
        self.loop = "play" if self.tracks[track] else "rec"
        self.tracks[track] = True

    # Text

    def waiting_text(self) -> str:
        if self.pending == "end":
            return "Ending at the next bar line."
        if self.pending == "fill":
            return f"Fill at the next bar line, then {NAMES[self.state]} again from bar 1."
        if self.pending == "switch":
            return f"Fill at the next bar line, then {NAMES[OTHER[self.state]]} from bar 1."
        if self.state in FILLS:
            return f"Fill, then {NAMES[self.after_fill]} from bar 1."
        if self.state == "intro":
            return "Intro, then Pattern 1."
        if self.state in LOOPS:
            return "Looping. Tap for a fill, hold to change part, tap twice to end."
        if self.state == "ending":
            return "Ending, then stop."
        if self.sync:
            return "Synced: press either switch to start. The loop records on the first pattern."
        return "Press the rhythm switch to start."

    def loop_text(self) -> str:
        if self.note:
            return self.note
        word = LOOP_WORDS.get(self.loop)
        if word is None:
            word = "STOP" if self.tracks[self.track] else "EMPTY"
        return f"TR{self.track} {word}"

    def lcd_lines(self, bar: int | None, beat: int | None, bpm: int | None) -> tuple[str, str]:
        code = CODES.get(self.state, "---")
        tempo = f"{bpm:>3}BPM" if bpm else ""
        if self.state == "idle" or bar is None:
            first = f"{'READY':<9}{tempo:>7}"
        else:
            first = f"{code:<3} {bar:>3}-{beat or 1:<1} {tempo:>6}"
        second = f"{self.loop_text():<12}{'SYNC' if self.sync else '':>4}"
        return first[:16].ljust(16), second[:16].ljust(16)


class Switch:
    """Turns mouse presses on a footswitch pad into tap, hold, or tap twice."""

    HOLD_MS = 650
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

    WIDTH = 900
    PICTURE = 600
    HEIGHT = 780
    LOOP_RING = (185, 190)
    RHYTHM_RING = (715, 190)
    RING_RADIUS = 76
    LCD = (312, 128, 588, 200)
    PADS = {"loop": (82, 340, 430, 510), "rhythm": (470, 340, 818, 510)}
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
        self.title = self.canvas.create_text(
            self.WIDTH / 2, 20, text=title, fill="#f3f1eb", font=("Segoe UI", 12, "bold")
        )

        left, top, right, bottom = self.LCD
        middle = (left + right) / 2
        self.lcd = [
            self.canvas.create_text(middle, top + 21, text="", fill=LCD_TEXT, font=("Consolas", 22, "bold")),
            self.canvas.create_text(middle, top + 52, text="", fill=LCD_TEXT, font=("Consolas", 22, "bold")),
        ]

        self.loop_ring = self._ring(self.LOOP_RING, self.LOOP_SEGMENTS)
        self.rhythm_ring: list[int] = []
        self.rhythm_beats = 0

        self.pad_glow = {
            name: self.canvas.create_rectangle(x1 + 2, y1 + 2, x2 - 2, y2 - 2, outline="", width=3)
            for name, (x1, y1, x2, y2) in self.PADS.items()
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

        strip = self.PICTURE
        self.canvas.create_text(
            256, strip + 14, text="Loop switch (shown only, nothing is recorded)", fill="#8d8d94", font=("Segoe UI", 9)
        )
        self.canvas.create_text(644, strip + 14, text="Rhythm switch", fill="#8d8d94", font=("Segoe UI", 9))
        self.buttons: dict[str, ttk.Button] = {}
        for key, text, x in (
            ("loop_tap", "Tap", 166),
            ("loop_hold", "Hold", 256),
            ("loop_double", "Tap twice", 346),
            ("rhythm_tap", "Tap", 554),
            ("rhythm_hold", "Hold", 644),
            ("rhythm_double", "Tap twice", 734),
        ):
            button = ttk.Button(self.canvas, text=text, command=controls[key], width=11)
            self.canvas.create_window(x, strip + 44, window=button)
            self.buttons[key] = button

        self.sync_var = tk.BooleanVar(value=False)
        sync = ttk.Checkbutton(
            self.canvas,
            text="Sync the loop with the rhythm (blue)",
            variable=self.sync_var,
            command=lambda: controls["sync"](self.sync_var.get()),
        )
        self.canvas.create_window(82, strip + 88, window=sync, anchor="w")
        self.sync_box = sync
        clear = ttk.Button(self.canvas, text="Clear loops", command=controls["clear"], width=12)
        self.canvas.create_window(644, strip + 88, window=clear)
        stop = ttk.Button(self.canvas, text="Stop all", command=controls["stop"], width=12, style="Accent.TButton")
        self.canvas.create_window(764, strip + 88, window=stop)

        self.waiting = self.canvas.create_text(
            self.WIDTH / 2, strip + 128, text="", fill="#e8e6df", font=("Segoe UI", 10), width=840
        )
        self.ranges = self.canvas.create_text(
            self.WIDTH / 2, strip + 154, text="", fill="#8d8d94", font=("Segoe UI", 9), width=840
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
        cx, cy = center
        r = self.RING_RADIUS
        step = 360 / count
        gap = min(6.0, step * 0.18)
        return [
            self.canvas.create_arc(
                cx - r, cy - r, cx + r, cy + r,
                start=90 - (i + 1) * step + gap / 2,
                extent=step - gap,
                style="arc",
                width=10,
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
        """info: state, lines, waiting, range, pending, beat, beats, progress, loop, sync."""
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
        base = BLUE if sync else GREEN
        if state == "ending":
            base = RED
        elif state in FILLS or state == "intro":
            base = AMBER
        if info.get("pending") and blink:
            base = AMBER if info["pending"] != "end" else RED
        beat = int(info.get("beat") or 0)
        for index, item in enumerate(self.rhythm_ring):
            if state == "idle":
                color = base if sync and blink else RING_OFF
            else:
                color = base if index < beat else RING_OFF
            self.canvas.itemconfigure(item, outline=color)

        loop = info.get("loop", "off")
        loop_color = {"rec": RED, "play": GREEN, "dub": YELLOW}.get(loop)
        lit = round(float(info.get("progress") or 0) * self.LOOP_SEGMENTS)
        for index, item in enumerate(self.loop_ring):
            if loop_color is None:
                color = RING_OFF
            elif state == "idle":
                color = loop_color
            else:
                color = loop_color if index < max(1, lit) else RING_OFF
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
