"""Guitar, bass and vocal parts that scroll with the playhead, under the drum roll.

Each part has its own lane and the lanes stack, so any mix of the three can be shown at once. A
Songsterr guitar or bass part is drawn as a tab: each string is a line with the fret numbers
where the notes land. A vocal, or any part that comes from the MIDI itself, is drawn as a melody:
bars at their pitch. Every lane uses the same window, playhead and bar lines as the drum roll, and a
Songsterr part is lined up bar for bar the way the chart has it. The notes live in memory only,
like the drum rows.
"""

from __future__ import annotations

import bisect
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

from drum_roll import (
    BEAT_LINE,
    BEHIND,
    CHART_BG,
    GUTTER,
    HEADER_BG,
    HEADER_H,
    HEADER_TEXT,
    LABEL_TEXT,
    MARGIN,
    NOW_LINE,
    ROW_A,
    ROW_B,
    SlidingRoll,
    mix,
)

TAB_ROW_H = 17
LEAST_STRINGS = 4
KINDS = ("guitar", "bass", "vocal")
TITLES = {"guitar": "Guitar", "bass": "Bass", "vocal": "Vocal"}
KIND_COLORS = {"guitar": "#e8a24a", "bass": "#6fb0e8", "vocal": "#e87fb0"}
MELODY_ROWS = 6
NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
STRING_LINE = "#3a4150"


def open_string_name(pitch: int) -> str:
    return NAMES[int(pitch) % 12]


def map_notes(notes, bar_ms: list[int], shift: int, half: bool) -> list[tuple]:
    """A part's notes as (ms, length ms, string, fret, flags) on the MIDI's bars.

    `notes` is a songsterr.TabNotes. Each tab bar lands on the MIDI bar the line-up says it
    belongs to, and notes keep their place inside the bar. Tab bars with no MIDI bar to land on
    are left out. This is the mapping the drum rows and the Chrome follow use.
    """
    out: list[tuple] = []
    if len(bar_ms) < 2:
        return out
    for bar, start, length, string, fret, flags in notes.notes:
        if half:
            midi_bar, inner, share = bar // 2 + 1 + shift, (bar % 2) / 2, 0.5
        else:
            midi_bar, inner, share = bar + 1 + shift, 0.0, 1.0
        if not 1 <= midi_bar < len(bar_ms):
            continue
        span = bar_ms[midi_bar] - bar_ms[midi_bar - 1]
        out.append((
            round(bar_ms[midi_bar - 1] + (inner + start * share) * span),
            round(length * share * span),
            string,
            fret,
            flags,
        ))
    out.sort(key=lambda item: (item[0], item[2]))
    return out


def fret_text(fret: int, flags: str) -> str:
    """What is written on the string: the fret, in brackets when ghosted, with what comes next."""
    if "d" in flags:
        return "x"
    text = str(fret)
    if "g" in flags:
        text = f"({text})"
    if "s" in flags:
        text += "/"
    if "h" in flags:
        text += "h"
    if "b" in flags:
        text += "b"
    return text


class TabRoll(SlidingRoll):
    """One lane: a part of one kind, with its part picker and a switch to hear it."""

    def __init__(
        self, parent: tk.Misc, kind: str, shown: tk.BooleanVar, on_change=None, on_wheel=None, on_hear=None
    ) -> None:
        super().__init__(parent, shown)
        self.kind = kind
        self.on_change = on_change
        self.on_wheel = on_wheel
        self.on_hear = on_hear  # called when this part is switched on or off for hearing
        self.hear = tk.BooleanVar(master=parent, value=False)
        self.part_name = tk.StringVar(master=parent, value="")
        self.parts: list[tuple[int | tuple, str]] = []
        self.strings = 6
        self.tuning: list[int] = []
        self.notes: list[tuple] = []
        self.times: list[int] = []
        self.mode = "tab"  # "tab" for strings and frets, "melody" for pitch bars
        self.melody_low = 48
        self.melody_span = 12
        self.midi_numbers = False  # bar numbers are the MIDI's own, not the tab's
        self.message = ""
        self.shift = 0
        self.half = False
        self.columnconfigure(0, weight=1)

        color = KIND_COLORS[kind]
        head = ttk.Frame(self)
        head.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        self.part_box = ttk.Combobox(head, textvariable=self.part_name, state="readonly", width=28, values=(), style="Flat.TCombobox")
        self.part_box.pack(side="left")
        self.part_box.bind("<<ComboboxSelected>>", lambda _event: self._part_changed())
        self.hear_check = ttk.Checkbutton(
            head, text="♪  Hear", variable=self.hear, command=self._hear_changed, takefocus=False,
            style=f"{TITLES[kind]}.Chip.TCheckbutton",
        )
        self.hear_check.pack(side="left", padx=(8, 0))
        self.hear_note = ttk.Label(head, text="", style="Muted.TLabel")
        self.hear_note.pack(side="left", padx=(10, 0))
        self.color = color

        self.canvas = tk.Canvas(self, bg=CHART_BG, highlightthickness=0, height=self.full_height())
        self.canvas.grid(row=1, column=0, sticky="ew")
        self.label_font = tkfont.Font(self.canvas, family="Segoe UI", size=8)
        self.bar_font = tkfont.Font(self.canvas, family="Segoe UI", size=8)
        self.head_font = tkfont.Font(self.canvas, family="Segoe UI", size=9, weight="bold")
        self.fret_font = tkfont.Font(self.canvas, family="Consolas", size=9, weight="bold")
        self.canvas.bind("<Configure>", lambda _event: self.redraw())
        self.canvas.bind("<MouseWheel>", self._wheel)

    # sizes

    def lines(self) -> int:
        return MELODY_ROWS if self.mode == "melody" else max(LEAST_STRINGS, self.strings)

    def full_height(self) -> int:
        return HEADER_H + self.lines() * TAB_ROW_H + 4

    # choices

    def part_index(self) -> int | tuple | None:
        name = self.part_name.get()
        return next((index for index, label in self.parts if label == name), None)

    def set_choices(self, parts: list[tuple], chosen: int | tuple | None) -> None:
        """The parts to pick from, best first, and which one is showing."""
        self.parts = list(parts)
        labels = [label for _index, label in self.parts]
        self.part_box.configure(values=labels, state="readonly" if len(labels) > 1 else "disabled")
        label = next((label for index, label in self.parts if index == chosen), labels[0] if labels else "")
        self.part_name.set(label)

    def _hear_changed(self) -> None:
        if self.on_hear:
            self.on_hear()

    def _part_changed(self) -> None:
        if self.on_change:
            self.on_change()

    # data

    def set_bars(self, bar_ms: list[int], meters: list[tuple[int, int]]) -> None:
        self.bar_ms = list(bar_ms)
        self.meters = list(meters)
        self._dirty = True
        self.redraw()

    def set_part(
        self,
        notes: list[tuple] | None,
        strings: int = 6,
        tuning: list[int] | None = None,
        message: str = "",
        shift: int = 0,
        half: bool = False,
    ) -> None:
        self.notes = list(notes or [])
        self.times = [item[0] for item in self.notes]
        self.message = message
        self.shift, self.half = shift, half
        self.mode = "tab"
        self.midi_numbers = False
        self.strings = max(1, min(12, strings))
        self.tuning = list(tuning or [])
        self.canvas.configure(height=self.full_height())
        self._dirty = True
        self.redraw()

    def set_melody(
        self, notes: list[tuple] | None, message: str = "", midi_numbers: bool = False, shift: int = 0, half: bool = False
    ) -> None:
        """Show notes as bars at their pitch. Each is (ms, length ms, pitch, velocity, kind, flags)."""
        self.notes = sorted(notes or [], key=lambda item: item[0])
        self.times = [item[0] for item in self.notes]
        self.message = message
        self.shift, self.half = shift, half
        self.mode = "melody"
        self.midi_numbers = midi_numbers
        self.strings = MELODY_ROWS
        self.tuning = []
        if self.notes:
            low = min(item[2] for item in self.notes)
            high = max(item[2] for item in self.notes)
            self.melody_span = max(12, high - low + 3)
            self.melody_low = low - 1 - (self.melody_span - (high - low + 3)) // 2
        self.canvas.configure(height=self.full_height())
        self._dirty = True
        self.redraw()

    def clear(self) -> None:
        self.bar_ms, self.meters = [], []
        self.set_part([], message="")

    def set_window(self, window_ms: int) -> None:
        """Follow the drum roll's zoom so both show the same stretch of time."""
        if window_ms != self.window_ms:
            self.window_ms = window_ms
            self._dirty = True
            self.redraw()

    def _wheel(self, event: tk.Event) -> str:
        if self.on_wheel:
            self.on_wheel(-1 if event.delta > 0 else 1)
        return "break"

    # drawing

    def bar_number(self, which: str, midi_bar: int) -> int:
        """The tab's own bar number at each bar line, or the MIDI's when the part comes from the MIDI."""
        if self.midi_numbers:
            return midi_bar
        if self.half:
            return (midi_bar - self.shift) * 2 - 1
        return midi_bar - self.shift

    def draw(self, width: int) -> None:
        canvas = self.canvas
        canvas.delete("all")
        self._notes = []
        self._played_i = 0
        self.redraws += 1
        self._dirty = False
        self._size = (width, self.window_ms)
        self._at = self._drawn_for = self.position
        left, right = GUTTER, width - 8
        plot = max(1, right - left)
        per_ms = plot / self.window_ms
        now_x = left + BEHIND * plot
        origin = self.position
        margin = self.window_ms * MARGIN
        start_ms = origin - BEHIND * self.window_ms - margin
        end_ms = origin + (1 - BEHIND) * self.window_ms + margin

        def x_of(ms: float) -> float:
            return now_x + (ms - origin) * per_ms

        lines = self.lines()
        body = HEADER_H
        bottom = body + lines * TAB_ROW_H
        color = self.color
        canvas.create_rectangle(0, 0, width, HEADER_H, fill=HEADER_BG, outline="")
        for index in range(lines):
            y = body + index * TAB_ROW_H
            canvas.create_rectangle(0, y, width, y + TAB_ROW_H, fill=ROW_A if index % 2 == 0 else ROW_B, outline="")
        self._bars("tab", body, bottom, x_of, start_ms, end_ms)
        if not self.notes:
            text = self.message or "Reading the part…"
            canvas.create_text((left + right) // 2, (body + bottom) // 2, text=text, fill=LABEL_TEXT, font=self.label_font)
        elif self.mode == "melody":
            self._melody_notes(body, bottom, left, right, per_ms, x_of, start_ms, end_ms, color)
        else:
            for index in range(self.strings):
                y = body + index * TAB_ROW_H + TAB_ROW_H / 2
                canvas.create_line(left, y, right, y, fill=STRING_LINE)
            low = bisect.bisect_left(self.times, start_ms - self.window_ms)
            high = bisect.bisect_right(self.times, end_ms)
            for ms, length, string, fret, flags in self.notes[low:high]:
                if string >= self.strings or ms + length < start_ms:
                    continue
                self._note(x_of(ms), body + string * TAB_ROW_H + TAB_ROW_H / 2, length * per_ms, fret, flags, color, ms)
        canvas.create_rectangle(0, 0, left, HEADER_H, fill=HEADER_BG, outline="")
        canvas.create_rectangle(0, 0, 3, HEADER_H, fill=color, outline="")
        canvas.create_text(12, HEADER_H // 2, text=TITLES[self.kind], anchor="w", fill=HEADER_TEXT, font=self.head_font)
        for index in range(lines):
            y = body + index * TAB_ROW_H
            canvas.create_rectangle(0, y, left - 2, y + TAB_ROW_H, fill=ROW_A if index % 2 == 0 else ROW_B, outline="")
            if self.mode == "tab" and index < self.strings and index < len(self.tuning):
                canvas.create_text(
                    left - 8, y + TAB_ROW_H // 2, text=open_string_name(self.tuning[index]), anchor="e",
                    fill=mix(CHART_BG, color, 0.85), font=self.label_font,
                )
        if self.mode == "melody" and self.notes:
            self._melody_labels(body, bottom, left, color)
        canvas.create_line(now_x, 0, now_x, bottom, fill=NOW_LINE, width=2)
        self.paint_played()

    def _pitch_y(self, pitch: float, body: int, bottom: int) -> float:
        return bottom - (pitch - self.melody_low + 0.5) * (bottom - body) / self.melody_span

    def _melody_notes(self, body, bottom, left, right, per_ms, x_of, start_ms, end_ms, color) -> None:
        canvas = self.canvas
        for pitch in range(self.melody_low, self.melody_low + self.melody_span):
            if pitch % 12 in (0, 7):
                y = self._pitch_y(pitch, body, bottom)
                canvas.create_line(left, y, right, y, fill=STRING_LINE if pitch % 12 == 0 else BEAT_LINE)
        height = max(4.0, (bottom - body) / self.melody_span - 1)
        low = bisect.bisect_left(self.times, start_ms - self.window_ms)
        high = bisect.bisect_right(self.times, end_ms)
        for ms, length, pitch, velocity, _kind, _flags in self.notes[low:high]:
            if ms + length < start_ms:
                continue
            x0 = x_of(ms)
            x1 = max(x0 + 3, x_of(ms + length) - 1)
            y = self._pitch_y(pitch, body, bottom)
            fill = mix(CHART_BG, color, 0.35 + 0.65 * max(0, min(127, velocity)) / 127)
            bar = canvas.create_rectangle(x0, y - height / 2, x1, y + height / 2, fill=fill, outline="", tags="scroll")
            self.remember_note(bar, ms, fill)
            if height >= 8 and x1 - x0 >= 20:
                canvas.create_text(
                    x0 + 3, y, text=NAMES[pitch % 12], anchor="w", fill=CHART_BG, font=self.label_font, tags="scroll"
                )

    def _melody_labels(self, body: int, bottom: int, left: int, color: str) -> None:
        """Octave labels in the gutter: each C, named with its octave."""
        for pitch in range(self.melody_low, self.melody_low + self.melody_span):
            if pitch % 12 == 0:
                self.canvas.create_text(
                    left - 8, self._pitch_y(pitch, body, bottom), text=f"C{pitch // 12 - 1}", anchor="e",
                    fill=mix(CHART_BG, color, 0.85), font=self.label_font,
                )

    def _note(self, x: float, y: float, length_px: float, fret: int, flags: str, color: str, ms: float) -> None:
        canvas = self.canvas
        strength = 0.85
        if "g" in flags or "d" in flags:
            strength = 0.5
        elif "p" in flags:
            strength = 0.68
        elif "a" in flags:
            strength = 1.0
        fill = mix(CHART_BG, color, strength)
        tied = "t" in flags
        text = "" if tied else fret_text(fret, flags)
        half = (len(text) * 6 + 4) / 2 if text else 0
        if length_px > 2 * half + 6:
            sustain = mix(CHART_BG, color, 0.35)
            line = canvas.create_line(x + half, y, x + length_px, y, fill=sustain, width=3, tags="scroll")
            self.remember_note(line, ms, sustain)
        if tied:
            return
        # Background blot under the fret stays dark; only the number turns gray once played.
        canvas.create_rectangle(x - half, y - 7, x + half, y + 7, fill=CHART_BG, outline="", tags="scroll")
        number = canvas.create_text(x, y, text=text, fill=fill, font=self.fret_font, tags="scroll")
        self.remember_note(number, ms, fill)


class TabRolls(ttk.Frame):
    """The parts panel: a switch for each of guitar, bass and vocal, and a lane under the drum roll for each one on.

    The lanes stack in that order, so any mix can be read together. One volume sets how loud the
    parts are when they are heard with the drums; each lane has its own switch for being heard.
    """

    def __init__(self, parent: tk.Misc, on_change=None, on_wheel=None, on_hear=None) -> None:
        super().__init__(parent)
        self.on_change = on_change
        self.on_hear = on_hear
        self.shown = {kind: tk.BooleanVar(master=parent, value=False) for kind in KINDS}
        self.hear_volume = tk.IntVar(master=parent, value=60)
        self.columnconfigure(0, weight=1)

        head = ttk.Frame(self)
        head.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        ttk.Label(head, text="Band parts", style="Section.TLabel").pack(side="left")
        self.chips: dict[str, ttk.Checkbutton] = {}
        for index, kind in enumerate(KINDS):
            chip = ttk.Checkbutton(
                head, text=TITLES[kind], variable=self.shown[kind], takefocus=False,
                command=lambda kind=kind: self._toggled(kind), style=f"{TITLES[kind]}.Chip.TCheckbutton",
            )
            chip.pack(side="left", padx=(12 if index == 0 else 4, 0))
            self.chips[kind] = chip
        self.volume = ttk.Scale(head, from_=0, to=100, variable=self.hear_volume, length=84, takefocus=False)
        self.volume.pack(side="right")
        self.volume.bind("<ButtonRelease-1>", lambda _event: self._hear_changed())
        ttk.Label(head, text="Volume", style="Muted.TLabel").pack(side="right", padx=(0, 8))

        self.lanes: dict[str, TabRoll] = {}
        for row, kind in enumerate(KINDS, start=1):
            lane = TabRoll(self, kind, self.shown[kind], on_change=on_change, on_wheel=on_wheel, on_hear=self._hear_changed)
            lane.grid(row=row, column=0, sticky="ew", pady=(0, 8))
            lane.grid_remove()
            self.lanes[kind] = lane

    def _toggled(self, kind: str) -> None:
        lane = self.lanes[kind]
        if self.shown[kind].get():
            lane.grid()
            lane._dirty = True
            lane.redraw()
        else:
            lane.grid_remove()
        if self.on_change:
            self.on_change()

    def _hear_changed(self) -> None:
        if self.on_hear:
            self.on_hear()

    def shown_kinds(self) -> list[str]:
        return [kind for kind in KINDS if self.shown[kind].get()]

    def show_kind(self, kind: str, shown: bool = True) -> None:
        """Switch a lane on or off the way its button does."""
        if self.shown[kind].get() != shown:
            self.chips[kind].invoke()

    def hearing(self) -> list[str]:
        return [kind for kind in KINDS if self.lanes[kind].hear.get()]

    # the lanes follow the song together

    def set_bars(self, bar_ms: list[int], meters: list[tuple[int, int]]) -> None:
        for lane in self.lanes.values():
            lane.set_bars(bar_ms, meters)

    def set_window(self, window_ms: int) -> None:
        for lane in self.lanes.values():
            lane.set_window(window_ms)

    def show(self, position: float) -> None:
        """Move every lane that is showing to song time position (ms)."""
        for kind in KINDS:
            if self.shown[kind].get():
                self.lanes[kind].show(position)

    def clear(self) -> None:
        for lane in self.lanes.values():
            lane.clear()
