"""Dark drum chart and a short preview mix for the finder window."""

from __future__ import annotations

import io
import math
import random
import wave

import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

import build_section_chart as chart

ROLE = {
    "intro": "#2f9e62",
    "pattern1": "#3d7ec4",
    "pattern2": "#e09a3c",
    "ending": "#a56bb8",
    "unstored": "#9aa3b4",
}
CHART_BG = "#171a21"
INK = "#f3f1eb"
MUTED = "#c2beb4"
PLAYHEAD = "#e24b3a"
GRID_BG = "#1c2029"
GRID_LEVEL = "#242a35"
GRID_BAR = "#2a303c"
GRID_PHRASE = "#3c4453"
GRID_BEAT = "#232833"
GRID_PULSE = "#20242e"
GRID_METER = "#6b5a3a"
GRID_METER_TEXT = "#d9b56e"
RATE = 16000
LANES = (
    ("K", "Kick", {35, 36}, "#e2694a"),
    ("S", "Snare", {37, 38, 39, 40}, "#f0d264"),
    ("T", "Toms", {41, 43, 45, 47, 48, 50}, "#b38ce0"),
    ("C", "Cymbals", {42, 44, 46, 49, 51, 52, 53, 55, 57, 59}, "#5ec4d6"),
)
CRASHES = {49, 52, 55, 57}
LANE_HEIGHT = 10
LANE_GAP = 3


def bar_lanes(events: list, boundaries: list[int]) -> tuple[list[tuple[int, ...]], set[int]]:
    """Hits per bar for kick, snare, toms, and cymbals, and the bars that have a crash."""
    count = max(0, len(boundaries) - 1)
    lanes = [[0] * len(LANES) for _ in range(count)]
    crashes: set[int] = set()
    if count == 0:
        return [], crashes
    index = 0
    for time, note, _velocity in sorted(events, key=lambda item: item[0]):
        while index < count - 1 and time >= boundaries[index + 1]:
            index += 1
        if time < boundaries[0] or time >= boundaries[-1]:
            continue
        for lane, (_short, _name, notes, _color) in enumerate(LANES):
            if note in notes:
                lanes[index][lane] += 1
                break
        if note in CRASHES:
            crashes.add(index + 1)
    return [tuple(row) for row in lanes], crashes


def _blend(color: str, amount: float) -> str:
    amount = max(0.0, min(1.0, amount))
    base = [int(CHART_BG[i : i + 2], 16) for i in (1, 3, 5)]
    top = [int(color[i : i + 2], 16) for i in (1, 3, 5)]
    mixed = [round(b + (t - b) * amount) for b, t in zip(base, top)]
    return "#" + "".join(f"{value:02x}" for value in mixed)


class SectionChart(ttk.Frame):
    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.canvas = tk.Canvas(
            self,
            height=248,
            bg=CHART_BG,
            highlightthickness=0,
            xscrollcommand=self._on_scroll,
        )
        self.bar = ttk.Scrollbar(self, orient="horizontal", command=self.canvas.xview, style="Dark.Horizontal.TScrollbar")
        self.canvas.configure(xscrollcommand=self.bar.set)
        self.canvas.pack(fill="both", expand=True)
        self.bar.pack(fill="x", pady=(4, 0))
        self.pad = 28
        self.steps = (6, 10, 16, 24, 36, 52)
        self.zoom_index = 1
        self.step = self.steps[self.zoom_index]
        self.bar_w = self.step - 2
        self.plot_top = 72
        self.plot_bottom = 204
        self.full_width = 1
        self.shade = None
        self.head = None
        self.seen = False
        self._hits: list[int] = []
        self._divisions: dict = {}
        self._parts: list[tuple[int, int, str]] = []
        self._lanes: list[tuple[int, ...]] = []
        self._crashes: set[int] = set()
        self._meters: list[tuple[int, int]] = []
        self.on_zoom_changed = None
        self.on_bar_click = None
        self.on_bars_chosen = None
        self.on_bars_inserted = None
        self._selection: tuple[int, int] | None = None
        self._press_bar: int | None = None
        self._press_x = 0
        self._dragged = False
        self._insert_slot: int | None = None
        self._insert_x = 0
        self._inserting = False
        self._content = 248
        self.canvas.configure(cursor="hand2", takefocus=True)
        self.canvas.bind("<Control-MouseWheel>", self._on_wheel)
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._motion)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<ButtonPress-3>", self._insert_press)
        self.canvas.bind("<B3-Motion>", self._insert_motion)
        self.canvas.bind("<ButtonRelease-3>", self._insert_release)
        self.name_font = tkfont.Font(self.canvas, family="Segoe UI", size=11, weight="bold")
        self.bar_font = tkfont.Font(self.canvas, family="Segoe UI", size=10)
        self.axis_font = tkfont.Font(self.canvas, family="Segoe UI", size=10)
        self.axis_bold = tkfont.Font(self.canvas, family="Segoe UI", size=10, weight="bold")
        self.lane_font = tkfont.Font(self.canvas, family="Segoe UI", size=9, weight="bold")

    def _on_scroll(self, first: str, last: str) -> None:
        self.bar.set(first, last)

    def can_zoom(self, direction: int) -> bool:
        nxt = self.zoom_index + direction
        return bool(self._hits) and 0 <= nxt < len(self.steps)

    def zoom(self, direction: int) -> None:
        if not self.can_zoom(direction):
            return
        fraction = self.canvas.canvasx(0) / max(1, self.full_width)
        self.zoom_index += direction
        self.step = self.steps[self.zoom_index]
        self.draw(self._hits, self._divisions, self._parts, self._lanes, self._crashes, self._meters)
        self.canvas.xview_moveto(fraction)
        if self.on_zoom_changed is not None:
            self.on_zoom_changed()

    def _on_wheel(self, event: tk.Event) -> None:
        self.zoom(1 if event.delta > 0 else -1)

    def bar_at(self, event: tk.Event) -> int | None:
        if not self._hits:
            return None
        x = self.canvas.canvasx(event.x)
        index = 0 if x < self.pad else int((x - self.pad) // self.step)
        index = max(0, min(len(self._hits) - 1, index))
        return index + 1

    def slot_at(self, event: tk.Event) -> int:
        """Bar slot under the pointer. One past the last bar means after the song."""
        if not self._hits:
            return 1
        x = self.canvas.canvasx(event.x)
        if x < self.pad:
            return 1
        return int((x - self.pad) // self.step) + 1

    def _insert_press(self, event: tk.Event) -> None:
        if not self._hits:
            return
        self.canvas.focus_set()
        self._insert_slot = self.slot_at(event)
        self._insert_x = event.x
        self._inserting = False

    def _insert_motion(self, event: tk.Event) -> None:
        if self._insert_slot is None:
            return
        if abs(event.x - self._insert_x) > 4 or self.slot_at(event) != self._insert_slot:
            self._inserting = True
        if not self._inserting:
            return
        start, count = self._insert_span(event)
        self.show_insert(start, count)

    def _insert_release(self, event: tk.Event) -> None:
        if self._insert_slot is None:
            return
        inserting = self._inserting
        start, count = self._insert_span(event)
        self._insert_slot = None
        self._inserting = False
        self.canvas.delete("insert")
        if not inserting:
            return
        if self.on_bars_inserted is not None and count > 0:
            self.on_bars_inserted(start, count)

    def _insert_span(self, event: tk.Event) -> tuple[int, int]:
        slot = self.slot_at(event)
        press = self._insert_slot if self._insert_slot is not None else slot
        left = max(1, min(press, slot))
        right = max(press, slot, left)
        return left, right - left + 1

    def show_insert(self, start: int, count: int) -> None:
        self.canvas.delete("insert")
        if count < 1 or not self._hits:
            return
        x0 = self.pad + (start - 1) * self.step
        x1 = x0 + count * self.step
        self.canvas.create_rectangle(
            x0,
            self.plot_top,
            x1,
            self.plot_bottom,
            outline=INK,
            width=2,
            dash=(4, 3),
            fill=ROLE["unstored"],
            stipple="gray25",
            tags="insert",
        )
        label = "+1 silent bar" if count == 1 else f"+{count} silent bars"
        self.canvas.create_text(x0 + 4, self.plot_top + 6, text=label, anchor="nw", fill=INK, font=self.bar_font, tags="insert")
        width = max(self.full_width, x1 + 16)
        self.canvas.configure(scrollregion=(0, 0, width, self._content))
        self._raise_playhead()

    def _press(self, event: tk.Event) -> None:
        bar = self.bar_at(event)
        if bar is None:
            return
        self.canvas.focus_set()
        self._press_bar = bar
        self._press_x = event.x
        self._dragged = False

    def _motion(self, event: tk.Event) -> None:
        if self._press_bar is None:
            return
        bar = self.bar_at(event)
        if bar is None:
            return
        if abs(event.x - self._press_x) > 4 or bar != self._press_bar:
            self._dragged = True
            start, end = sorted((self._press_bar, bar))
            self.show_selection(start, end)

    def _release(self, event: tk.Event) -> None:
        if self._press_bar is None:
            return
        bar = self.bar_at(event) or self._press_bar
        pressed = self._press_bar
        dragged = self._dragged
        self._press_bar = None
        if dragged:
            start, end = sorted((pressed, bar))
            self.show_selection(start, end)
            if self.on_bars_chosen is not None:
                self.on_bars_chosen(start, end)
            return
        if self.on_bar_click is not None:
            self.on_bar_click(bar)

    def show_selection(self, start: int | None, end: int | None) -> None:
        self.canvas.delete("selection")
        if start is None or end is None or not self._hits:
            self._selection = None
            return
        start, end = sorted((int(start), int(end)))
        start = max(1, min(len(self._hits), start))
        end = max(start, min(len(self._hits), end))
        self._selection = (start, end)
        x0 = self.pad + (start - 1) * self.step
        x1 = self.pad + end * self.step
        self.canvas.create_rectangle(x0, self.plot_top, x1, self.plot_bottom, outline="#e4a24a", width=2, tags="selection")
        self._raise_playhead()

    def reveal(self, bar: int) -> None:
        if not self._hits or not 1 <= bar <= len(self._hits):
            return
        x = self.pad + (bar - 1) * self.step
        width = max(1, self.canvas.winfo_width())
        left = self.canvas.canvasx(0)
        if x < left + 24 or x + self.step > left + width - 24:
            self.canvas.xview_moveto(max(0.0, (x - width * 0.3) / max(1, self.full_width)))

    def _raise_playhead(self) -> None:
        if self.shade is not None:
            self.canvas.tag_raise(self.shade)
        if self.head is not None:
            self.canvas.tag_raise(self.head)

    def _assign_rows(self, labels: list[dict], gap: int = 8) -> int:
        """Give each label a row so its text does not sit on top of another label."""
        occupied: list[list[tuple[float, float]]] = []
        for label in labels:
            width = label.get("width", label["font"].measure(label["text"]))
            if label.get("anchor") == "n":
                left = label["x"] - width / 2
            else:
                left = label["x"]
            right = left + width
            row = 0
            while row < len(occupied) and any(right + gap > other_left and left < other_right + gap for other_left, other_right in occupied[row]):
                row += 1
            if row == len(occupied):
                occupied.append([])
            occupied[row].append((left, right))
            label["row"] = row
        return len(occupied)

    def draw(
        self,
        hits: list[int],
        divisions: dict,
        parts: list[tuple[int, int, str]] | None = None,
        lanes: list[tuple[int, ...]] | None = None,
        crashes: set[int] | None = None,
        meters: list[tuple[int, int]] | None = None,
    ) -> None:
        chosen = self._selection
        self._hits = list(hits)
        self._divisions = divisions
        self._parts = list(parts or [])
        self._lanes = list(lanes or [])
        self._crashes = set(crashes or ())
        self._meters = [tuple(meter) for meter in meters or []]
        roles = chart.paint_roles(len(hits), divisions)
        names = dict(chart.DIVISION_ORDER)
        self.canvas.delete("all")
        self.seen = False
        self.bar_w = max(4, self.step - 2)
        count = max(len(hits), 1)
        self.full_width = self.pad + count * self.step + 16
        peak = max(hits) if hits else 1
        peak = max(peak, 1)
        fills = set()
        for key in ("pattern1Fill", "pattern2Fill"):
            fills.add(divisions[key][0])
        section_starts: dict[int, str] = {}
        part_labels = []
        for start, end, name in parts or []:
            if end < start or not 1 <= start <= len(hits):
                continue
            section_starts[start] = "part"
            bar_text = f"bar {start}"
            part_labels.append(
                {
                    "x": self.pad + (start - 1) * self.step,
                    "text": name,
                    "bar": bar_text,
                    "width": max(self.name_font.measure(name), self.bar_font.measure(bar_text)),
                    "font": self.name_font,
                    "anchor": "w",
                }
            )
        for role, start, end in chart.role_spans(roles):
            if role == "unstored" or end < start:
                continue
            section_starts[start] = "both" if start in section_starts else "role"
        rc_labels = []
        for role, start, end in chart.role_spans(roles):
            if role == "unstored":
                continue
            length = end - start + 1
            text = f"{names[role]}  {start} × {length}"
            rc_labels.append(
                {
                    "x": self.pad + (start - 1) * self.step,
                    "text": text,
                    "fill": ROLE[role],
                    "font": self.name_font,
                    "anchor": "w",
                }
            )
        marks = {1, len(hits)}
        marks.update(section_starts)
        for bar in range(16, len(hits) + 1, 16):
            marks.add(bar)
        axis_labels = []
        for bar in sorted(mark for mark in marks if 1 <= mark <= len(hits)):
            bold = bar in section_starts
            axis_labels.append(
                {
                    "x": self.pad + (bar - 1) * self.step,
                    "text": str(bar),
                    "fill": INK if bold else MUTED,
                    "font": self.axis_bold if bold else self.axis_font,
                    "anchor": "n",
                }
            )
        part_rows = self._assign_rows(part_labels)
        rc_rows = self._assign_rows(rc_labels)
        axis_rows = self._assign_rows(axis_labels)
        part_stride = 42
        rc_stride = 24
        axis_stride = 20
        part_bottom = 14 + max(part_rows, 1) * part_stride
        rc_top = part_bottom + 2
        meter_row = rc_top + max(rc_rows, 1) * rc_stride + 4
        self.plot_top = meter_row + 16
        self.plot_bottom = self.plot_top + 120
        lane_top = self.plot_bottom + 5
        lane_rows = len(LANES) if self._lanes else 0
        axis_top = lane_top + lane_rows * (LANE_HEIGHT + LANE_GAP) + 6
        content = axis_top + max(axis_rows, 1) * axis_stride + 8
        self._content = content
        self.canvas.configure(height=max(248, content), scrollregion=(0, 0, self.full_width, content))
        self._draw_grid(len(hits), peak, meter_row)
        self.canvas.create_text(8, self.plot_top + 4, text=str(peak), fill=MUTED, anchor="w", font=self.axis_font)
        self.canvas.create_line(self.pad, self.plot_bottom, self.full_width - 12, self.plot_bottom, fill="#3a404d")
        for bar, count_hits in enumerate(hits, start=1):
            role = roles[bar - 1]
            column = 18 if count_hits == 0 else 18 + round(count_hits / peak * 94)
            x = self.pad + (bar - 1) * self.step
            y = self.plot_bottom - column
            self.canvas.create_rectangle(x, y, x + self.bar_w, self.plot_bottom, fill=ROLE[role], width=0)
            if self.bar_w >= 12:
                beats, _pulses = self.beats_in(*self.meter_of(bar))
                divider = _blend(ROLE[role], 0.58)
                for index in range(1, beats):
                    bx = x + index * self.bar_w / beats
                    self.canvas.create_line(bx, y + 2, bx, self.plot_bottom, fill=divider)
            if bar in fills:
                self.canvas.create_rectangle(x, y, x + self.bar_w, self.plot_bottom, outline=INK, width=1)
            if bar in self._crashes:
                middle = x + self.bar_w / 2
                radius = 2 if self.step < 16 else 3
                self.canvas.create_oval(
                    middle - radius, y - 4 - 2 * radius, middle + radius, y - 4, fill=INK, outline="", tags="crash"
                )
        if self._lanes:
            peaks = [max((row[lane] for row in self._lanes), default=0) or 1 for lane in range(len(LANES))]
            for lane, (short, _name, _notes, color) in enumerate(LANES):
                top = lane_top + lane * (LANE_HEIGHT + LANE_GAP)
                self.canvas.create_text(10, top + LANE_HEIGHT / 2, text=short, fill=color, anchor="w", font=self.lane_font)
                for bar, row in enumerate(self._lanes[: len(hits)], start=1):
                    value = row[lane]
                    x = self.pad + (bar - 1) * self.step
                    fill = _blend(color, 0.12) if value == 0 else _blend(color, 0.3 + 0.7 * value / peaks[lane])
                    self.canvas.create_rectangle(x, top, x + self.bar_w, top + LANE_HEIGHT, fill=fill, width=0, tags="lane")
        for bar in section_starts:
            x = self.pad + (bar - 1) * self.step
            self.canvas.create_line(x, self.plot_top, x, self.plot_bottom, fill=INK, width=1)
        for label in part_labels:
            y = 14 + label["row"] * part_stride
            self.canvas.create_text(label["x"], y, text=label["text"], fill="#e6d3a8", anchor="w", font=self.name_font)
            self.canvas.create_text(label["x"], y + 20, text=label["bar"], fill="#d9c79d", anchor="w", font=self.bar_font)
        for label in rc_labels:
            y = rc_top + label["row"] * rc_stride
            self.canvas.create_text(label["x"], y, text=label["text"], fill=label["fill"], anchor="w", font=self.name_font)
        for label in axis_labels:
            y = axis_top + label["row"] * axis_stride
            self.canvas.create_text(label["x"], y, text=label["text"], fill=label["fill"], anchor="n", font=label["font"])
        self.shade = self.canvas.create_rectangle(
            0, self.plot_top, self.step, self.plot_bottom, outline=PLAYHEAD, width=1, state="hidden"
        )
        self.head = self.canvas.create_line(
            0, self.plot_top - 4, 0, self.plot_bottom, fill=PLAYHEAD, width=2, state="hidden"
        )
        if chosen:
            self.show_selection(*chosen)

    def meter_of(self, bar: int) -> tuple[int, int]:
        if 1 <= bar <= len(self._meters):
            num, den = self._meters[bar - 1]
            return int(num), int(den)
        return 4, 4

    @staticmethod
    def beats_in(num: int, den: int) -> tuple[int, int]:
        """Felt beats per bar and the pulses inside each beat (6/8 is two beats of three)."""
        if den >= 8 and num % 3 == 0 and num > 3:
            return num // 3, 3
        return max(1, num), 1

    def _draw_grid(self, count: int, peak: int, meter_row: int) -> None:
        """Bar, beat, and phrase lines behind the bars, following each bar's time signature."""
        left = self.pad - 1
        right = self.pad + count * self.step - 1
        top, bottom = self.plot_top, self.plot_bottom
        self.canvas.create_rectangle(left, top, right, bottom, fill=GRID_BG, width=0, tags="grid")
        for share in (0.25, 0.5, 0.75, 1.0):
            y = bottom - (18 + share * 94)
            self.canvas.create_line(left, y, right, y, fill=GRID_LEVEL, tags="grid")
        beat_room = self.bar_w >= 12
        phrase_start = 1
        previous = None
        label_end = -1e9
        for bar in range(1, count + 1):
            meter = self.meter_of(bar)
            x = self.pad + (bar - 1) * self.step
            if meter != previous:
                phrase_start = bar
                if previous is not None:
                    self.canvas.create_line(x - 1, meter_row, x - 1, bottom, fill=GRID_METER, width=2, tags="grid")
                text = f"{meter[0]}/{meter[1]}"
                if x + 1 > label_end:
                    self.canvas.create_text(
                        x + 1, meter_row + 7, text=text, fill=GRID_METER_TEXT, anchor="w",
                        font=self.axis_bold, tags="grid",
                    )
                    label_end = x + 1 + self.axis_bold.measure(text) + 6
                previous = meter
            elif (bar - phrase_start) % 4 == 0:
                self.canvas.create_line(x - 1, top, x - 1, bottom, fill=GRID_PHRASE, width=1, tags="grid")
            else:
                self.canvas.create_line(x - 1, top, x - 1, bottom, fill=GRID_BAR, width=1, tags="grid")
            if beat_room:
                beats, pulses = self.beats_in(*meter)
                divisions = beats * pulses if self.bar_w >= 30 and pulses > 1 else beats
                for index in range(1, divisions):
                    bx = x + index * self.bar_w / divisions
                    on_beat = pulses == 1 or divisions == beats or index % pulses == 0
                    self.canvas.create_line(
                        bx, top, bx, bottom, fill=GRID_BEAT if on_beat else GRID_PULSE, tags="grid"
                    )
        self.canvas.create_line(right, top, right, bottom, fill=GRID_PHRASE, tags="grid")

    def show_playhead(self, index: int, fraction: float) -> None:
        if self.head is None:
            return
        fraction = min(1.0, max(0.0, fraction))
        x0 = self.pad + index * self.step
        x = x0 + fraction * self.step
        self.canvas.coords(self.shade, x0, self.plot_top, x0 + self.step, self.plot_bottom)
        self.canvas.coords(self.head, x, self.plot_top - 4, x, self.plot_bottom)
        self.canvas.itemconfigure(self.shade, state="normal")
        self.canvas.itemconfigure(self.head, state="normal")
        self.canvas.tag_raise(self.shade)
        self.canvas.tag_raise(self.head)
        width = max(1, self.canvas.winfo_width())
        left = self.canvas.canvasx(0)
        if not self.seen or x < left + 40 or x > left + width - 40:
            self.canvas.xview_moveto(max(0.0, (x - width * 0.35) / max(1, self.full_width)))
            self.seen = True

    def hide_playhead(self) -> None:
        self.seen = False
        if self.head is None:
            return
        self.canvas.itemconfigure(self.shade, state="hidden")
        self.canvas.itemconfigure(self.head, state="hidden")


def _tone(mix: list[float], pos: int, length: int, freq: float, freq_end: float, peak: float) -> None:
    if pos >= len(mix) or length <= 0 or peak <= 0:
        return
    length = min(length, len(mix) - pos)
    for index in range(length):
        fade = 1 - index / length
        freq_now = freq + (freq_end - freq) * (index / length)
        mix[pos + index] += math.sin(math.tau * freq_now * index / RATE) * peak * fade * fade


def _noise(mix: list[float], pos: int, length: int, peak: float, rng: random.Random) -> None:
    if pos >= len(mix) or length <= 0 or peak <= 0:
        return
    length = min(length, len(mix) - pos)
    for index in range(length):
        fade = 1 - index / length
        mix[pos + index] += rng.uniform(-1, 1) * peak * fade


def _hit(mix: list[float], pos: int, note: int, velocity: int, rng: random.Random) -> None:
    level = max(0.15, velocity / 127)
    samples = RATE
    if note in (35, 36):
        _tone(mix, pos, int(samples * 0.16), 150, 48, 0.9 * level)
    elif note in (38, 40):
        _tone(mix, pos, int(samples * 0.1), 180, 110, 0.28 * level)
        _noise(mix, pos, int(samples * 0.12), 0.35 * level, rng)
    elif note in (42, 44):
        _noise(mix, pos, int(samples * 0.04), 0.22 * level, rng)
    elif note in (46, 32):
        _noise(mix, pos, int(samples * 0.2), 0.18 * level, rng)
    elif note in (49, 51, 52, 53, 55, 57, 59):
        _noise(mix, pos, int(samples * 0.28), 0.16 * level, rng)
    elif note == 56:
        _tone(mix, pos, int(samples * 0.12), 587, 587, 0.16 * level)
    elif note in (26, 34):
        _tone(mix, pos, int(samples * 0.22), 1660, 1660, 0.22 * level)
    elif note in (83, 84):
        _tone(mix, pos, int(samples * 0.12), 2637, 2637, 0.12 * level)
    elif note in (41, 43, 45, 47, 48, 50):
        pitch = {41: 90, 43: 110, 45: 140, 47: 170, 48: 200, 50: 240}.get(note, 150)
        _tone(mix, pos, int(samples * 0.16), pitch, pitch * 0.7, 0.4 * level)
    else:
        freq = 440 * (2 ** ((note - 69) / 12))
        _tone(mix, pos, int(samples * 0.08), freq, freq, 0.16 * level)


def render_section(events: list[list[int]], start_ms: int, end_ms: int) -> bytes:
    duration = max(0.05, (end_ms - start_ms) / 1000)
    total = int(duration * RATE) + int(0.05 * RATE)
    mix = [0.0] * total
    rng = random.Random(1)
    for ms, note, velocity in events:
        if ms < start_ms or ms >= end_ms:
            continue
        _hit(mix, int((ms - start_ms) / 1000 * RATE), note, velocity, rng)
    peak = max((abs(sample) for sample in mix), default=1.0) or 1.0
    scale = 28000 / peak
    pcm = bytearray()
    for sample in mix:
        value = max(-32767, min(32767, int(sample * scale)))
        pcm += int(value).to_bytes(2, "little", signed=True)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(RATE)
        handle.writeframes(pcm)
    return buffer.getvalue()
