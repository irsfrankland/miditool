"""A drum piano roll that scrolls with the playhead.

Each drum has its own row. Hits are drawn as shapes, brighter for harder hits, so
the kick, snare, toms, hi-hat, ride and crash can be told apart at a glance.
It shows either the MIDI's drums or, when there is a tab, Songsterr's drums lined up
bar for bar with the same line-up the chart uses. One of the two is shown at a time.
"""

from __future__ import annotations

import bisect
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

CHART_BG = "#171a21"
HEADER_BG = "#252a34"
HEADER_TEXT = "#d6d9e0"
LABEL_TEXT = "#c2c6d0"
ROW_A = "#1b1f27"
ROW_B = "#171a21"
BAR_LINE = "#4a5163"
BEAT_LINE = "#2a303c"
BAR_TEXT = "#9aa3b4"
NOW_LINE = "#e24b3a"

# key, label, colour, shape, General MIDI drum numbers. Top row first.
ROWS = (
    ("crash", "Crash", "#f29ac8", "star", frozenset({49, 52, 55, 57})),
    ("ride", "Ride", "#5aa0ee", "diamond", frozenset({51, 53, 59})),
    ("hat_open", "Hat open", "#9be8a8", "xring", frozenset({46})),
    ("hat", "Hi-hat", "#5ec4d6", "x", frozenset({42, 44})),
    ("snare", "Snare", "#f0d264", "circle", frozenset({37, 38, 39, 40})),
    ("tom_high", "Tom high", "#cfb0f2", "circle", frozenset({48, 50})),
    ("tom_mid", "Tom mid", "#b38ce0", "circle", frozenset({45, 47})),
    ("tom_low", "Floor tom", "#9566c8", "circle", frozenset({41, 43})),
    ("kick", "Kick", "#e2694a", "circle", frozenset({35, 36})),
)
ROW_OF = {note: index for index, row in enumerate(ROWS) for note in row[4]}
ROW_H = 14
HEADER_H = 18
GAP = 8
GUTTER = 78
ZOOM_STEPS_MS = (3000, 4500, 6000, 9000, 12000, 18000)
BEHIND = 0.25  # how much of the window lies behind the playhead
GHOST = 50
ACCENT = 108
MARGIN = 1.2  # how much extra time, as a share of the window, is drawn on each side
PLAYED = "#6e7380"  # notes left of the playhead turn this gray instead of sitting under a fade


def mix(back: str, front: str, amount: float) -> str:
    amount = max(0.0, min(1.0, amount))
    one = [int(back[i : i + 2], 16) for i in (1, 3, 5)]
    two = [int(front[i : i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(a + (b - a) * amount):02x}" for a, b in zip(one, two))


def row_for(note: int) -> int | None:
    """Which row a General MIDI drum belongs on, or None for sounds the roll does not draw."""
    return ROW_OF.get(note)


def tab_events(ticks: list, bounds: list, bar_ms: list, shift: int, half: bool) -> list[list[int]]:
    """Songsterr's drum hits as [ms, note, velocity] on the MIDI's bars.

    Each tab bar lands on the MIDI bar the line-up says it belongs to, and hits keep their
    place inside the bar. Tab bars with no MIDI bar to land on are left out. This is the same
    mapping the Chrome follow uses.
    """
    out: list[list[int]] = []
    bars = len(bounds) - 1
    if bars < 1 or len(bar_ms) < 2:
        return out
    for tick, note, velocity in ticks:
        index = min(bars - 1, max(0, bisect.bisect_right(bounds, tick) - 1))
        span = bounds[index + 1] - bounds[index]
        fraction = (tick - bounds[index]) / span if span > 0 else 0.0
        if half:
            midi_bar, inner, share = index // 2 + 1 + shift, (index % 2) / 2, 0.5
        else:
            midi_bar, inner, share = index + 1 + shift, 0.0, 1.0
        if not 1 <= midi_bar < len(bar_ms):
            continue
        length = bar_ms[midi_bar] - bar_ms[midi_bar - 1]
        out.append([round(bar_ms[midi_bar - 1] + (inner + fraction * share) * length), note, velocity])
    out.sort()
    return out


class SlidingRoll(ttk.Frame):
    """A canvas that scrolls with the playhead without being drawn again every frame.

    The notes are drawn once for a stretch of time a little wider than the window, then slid
    sideways as the playhead moves, which is cheap enough to run smoothly. They are only drawn
    again when the playhead gets near the edge of what was drawn, or when the data, size or
    zoom changes. Subclasses draw(): notes carry the tag "scroll", everything else stays put.
    """

    FRAME_MS = 8  # asks for the next frame this soon; Windows rounds it up to about 15 ms

    def __init__(self, parent: tk.Misc, show_roll: tk.BooleanVar) -> None:
        super().__init__(parent)
        self.show_roll = show_roll
        self.clock = None  # returns the song time (ms) while playing, or None when nothing is moving
        self.window_ms = 6000
        self.position = 0.0
        self.bar_ms: list[int] = []
        self.meters: list[tuple[int, int]] = []
        self._dirty = True
        self._at = 0.0  # the song time the notes sit at right now
        self._drawn_for = 0.0  # the song time they were drawn around
        self._size = (0, 0)
        self.redraws = 0
        self.followers: list = []  # called with the position every frame, so other rolls move in step with this one
        # Each note: canvas items, song time (ms), live fill colour, already gray?
        # Kept in song-time order so paint_played can walk a frontier instead of every note.
        self._notes: list[tuple[tuple[int, ...], float, str, bool]] = []
        self._played_i = 0  # first note that is still ahead of the playhead

    def _frame(self) -> None:
        """One animation frame. While something is playing the roll asks the clock where it is.

        The followers move in the same frame, so rolls stacked together never lag one another.
        The next frame is booked after this one finishes, so work cannot pile up.
        """
        try:
            if self.clock is not None:
                position = self.clock()
                if position is not None:
                    if self.show_roll.get():
                        self.show(position)
                    for follow in self.followers:
                        follow(position)
        finally:
            self.after(self.FRAME_MS, self._frame)

    def show(self, position: float) -> None:
        """Move the roll to song time position (ms)."""
        self.position = float(position)
        self.redraw()

    def remember_note(self, items: int | tuple[int, ...] | list[int], ms: float, fill: str) -> None:
        """Keep a drawn note so it can turn gray once the playhead passes it."""
        if isinstance(items, int):
            items = (items,)
        else:
            items = tuple(items)
        self._notes.append((items, float(ms), fill, False))

    def _color_note(self, items: tuple[int, ...], color: str) -> None:
        canvas = self.canvas
        for item in items:
            kind = canvas.type(item)
            if kind in ("line", "text", "polygon", "rectangle"):
                canvas.itemconfigure(item, fill=color)
            elif kind == "oval":
                # Solid hits use fill; open-hat rings use outline only.
                if canvas.itemcget(item, "fill"):
                    canvas.itemconfigure(item, fill=color)
                if canvas.itemcget(item, "outline"):
                    canvas.itemconfigure(item, outline=color)

    def paint_played(self) -> None:
        """Notes at or behind the playhead go gray; notes still ahead keep their colour."""
        now = self.position
        notes = self._notes
        i = self._played_i
        while i < len(notes) and notes[i][1] <= now:
            items, ms, live, was = notes[i]
            if not was:
                self._color_note(items, PLAYED)
                notes[i] = (items, ms, live, True)
            i += 1
        while i > 0 and notes[i - 1][1] > now:
            i -= 1
            items, ms, live, was = notes[i]
            if was:
                self._color_note(items, live)
                notes[i] = (items, ms, live, False)
        self._played_i = i

    def redraw(self) -> None:
        if not self.show_roll.get():
            return
        width = self.canvas.winfo_width()
        if width < 40:
            return
        margin = self.window_ms * MARGIN
        if (
            self._dirty
            or self._size != (width, self.window_ms)
            or abs(self.position - self._drawn_for) > margin * 0.75
        ):
            self.draw(width)
            return
        plot = max(1, width - GUTTER - 8)
        step = (self.position - self._at) * plot / self.window_ms
        if abs(step) >= 0.05:
            self.canvas.move("scroll", -step, 0)
            self._at = self.position
        self.paint_played()


    def _bars(self, which: str, body: int, bottom: int, x_of, start_ms: float, end_ms: float) -> None:
        canvas = self.canvas
        bar_ms = self.bar_ms
        if len(bar_ms) < 2:
            return
        first = max(0, bisect.bisect_right(bar_ms, start_ms) - 1)
        for index in range(first, len(bar_ms) - 1):
            begin, finish = bar_ms[index], bar_ms[index + 1]
            if begin > end_ms:
                break
            beats = self.meters[index][0] if index < len(self.meters) else 4
            for beat in range(1, max(1, beats)):
                x = x_of(begin + (finish - begin) * beat / beats)
                canvas.create_line(x, body, x, bottom, fill=BEAT_LINE, tags="scroll")
            x = x_of(begin)
            canvas.create_line(x, body, x, bottom, fill=BAR_LINE, tags="scroll")
            number = self.bar_number(which, index + 1)
            if number > 0:
                canvas.create_text(x + 4, HEADER_H // 2, text=str(number), anchor="w", fill=BAR_TEXT, font=self.bar_font, tags="scroll")



class DrumRoll(SlidingRoll):
    """The roll and its small header: which drums to show, and the zoom.

    One set of rows is shown at a time, MIDI or Songsterr. The hits are drawn once for a
    stretch of time a little wider than the window, then slid sideways as the playhead moves,
    which is cheap enough to run smoothly. They are only drawn again when the playhead gets
    near the edge of what was drawn, or when the data, size or zoom changes.
    """

    def __init__(self, parent: tk.Misc, show_roll: tk.BooleanVar, source: tk.StringVar, on_change=None) -> None:
        super().__init__(parent, show_roll)
        self.source = source
        self.on_change = on_change
        self.sources: dict[str, tuple[list[int], list[list[int]]]] = {"midi": ([], []), "tab": ([], [])}
        self.tab_note = ""
        self.tab_name = "Songsterr"
        self.tab_shift = 0
        self.tab_half = False
        self.on_zoom = None  # called with the new window (ms) so other rolls can show the same stretch of time
        self.columnconfigure(0, weight=1)

        head = ttk.Frame(self)
        head.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        ttk.Label(head, text="Drum roll", style="Section.TLabel").pack(side="left")
        self.roll_check = ttk.Checkbutton(head, text="Show", variable=show_roll, command=self._switch, takefocus=False, style="Chip.TCheckbutton")
        self.roll_check.pack(side="left", padx=(12, 0))
        # MIDI and Songsterr are one control in two halves, so they sit a hairline apart.
        self.midi_radio = ttk.Radiobutton(head, text="MIDI", variable=source, value="midi", command=self._switch, takefocus=False, style="Chip.TRadiobutton")
        self.midi_radio.pack(side="left", padx=(12, 1))
        self.tab_radio = ttk.Radiobutton(head, text="Songsterr", variable=source, value="tab", command=self._switch, takefocus=False, style="Chip.TRadiobutton")
        self.tab_radio.pack(side="left")
        self.zoom_in_button = ttk.Button(head, text="+", width=2, command=lambda: self.zoom(1))
        self.zoom_in_button.pack(side="right")
        self.zoom_label = ttk.Label(head, text="", style="Muted.TLabel", width=5, anchor="center")
        self.zoom_label.pack(side="right", padx=4)
        self.zoom_out_button = ttk.Button(head, text="−", width=2, command=lambda: self.zoom(-1))
        self.zoom_out_button.pack(side="right")
        ttk.Label(head, text="Window", style="Muted.TLabel").pack(side="right", padx=(0, 6))

        self.canvas = tk.Canvas(self, bg=CHART_BG, highlightthickness=0, height=self.full_height())
        self.canvas.grid(row=1, column=0, sticky="ew")
        self.label_font = tkfont.Font(self.canvas, family="Segoe UI", size=8)
        self.bar_font = tkfont.Font(self.canvas, family="Segoe UI", size=8)
        self.head_font = tkfont.Font(self.canvas, family="Segoe UI", size=9, weight="bold")
        self.canvas.bind("<Configure>", lambda _event: self.redraw())
        self.canvas.bind("<MouseWheel>", self._wheel)
        self._label_zoom()
        self._switch(notify=False)
        self._frame()

    # sizes

    def full_height(self) -> int:
        return HEADER_H + len(ROWS) * ROW_H + 4

    def which(self) -> str:
        return "tab" if self.source.get() == "tab" else "midi"

    # data

    def set_bars(self, bar_ms: list[int], meters: list[tuple[int, int]]) -> None:
        self.bar_ms = list(bar_ms)
        self.meters = list(meters)
        self._dirty = True
        self.redraw()

    def set_events(
        self, which: str, events: list[list[int]] | None, name: str = "", note: str = "", shift: int = 0, half: bool = False
    ) -> None:
        events = sorted(events or [], key=lambda item: item[0])
        self.sources[which] = ([item[0] for item in events], events)
        if which == "tab":
            self.tab_name = name or "Songsterr"
            self.tab_note = note
            self.tab_shift, self.tab_half = shift, half
        self._dirty = True
        self.redraw()

    def clear(self) -> None:
        self.bar_ms, self.meters = [], []
        self.sources = {"midi": ([], []), "tab": ([], [])}
        self.tab_note = ""
        self._dirty = True
        self.redraw()

    # window

    def can_zoom(self, direction: int) -> bool:
        index = ZOOM_STEPS_MS.index(self.window_ms)
        return 0 <= index + direction < len(ZOOM_STEPS_MS)

    def zoom(self, direction: int) -> None:
        if not self.can_zoom(direction):
            return
        self.window_ms = ZOOM_STEPS_MS[ZOOM_STEPS_MS.index(self.window_ms) + direction]
        self._label_zoom()
        self._dirty = True
        self.redraw()
        if self.on_zoom:
            self.on_zoom(self.window_ms)

    def _label_zoom(self) -> None:
        self.zoom_label["text"] = f"{self.window_ms / 1000:g} s"
        self.zoom_in_button.configure(state="normal" if self.can_zoom(1) else "disabled")
        self.zoom_out_button.configure(state="normal" if self.can_zoom(-1) else "disabled")

    def _wheel(self, event: tk.Event) -> str:
        self.zoom(-1 if event.delta > 0 else 1)
        return "break"

    def _switch(self, notify: bool = True) -> None:
        self.midi_radio.configure(state="normal" if self.show_roll.get() else "disabled")
        self.tab_radio.configure(state="normal" if self.show_roll.get() else "disabled")
        if self.show_roll.get():
            self.canvas.configure(height=self.full_height())
            self.canvas.grid()
        else:
            self.canvas.grid_remove()
        self._dirty = True
        self.redraw()
        if notify and self.on_change:
            self.on_change()

    # moving

    # drawing

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

        which = self.which()
        body = HEADER_H
        bottom = body + len(ROWS) * ROW_H
        canvas.create_rectangle(0, 0, width, HEADER_H, fill=HEADER_BG, outline="")
        for index in range(len(ROWS)):
            y = body + index * ROW_H
            canvas.create_rectangle(0, y, width, y + ROW_H, fill=ROW_A if index % 2 == 0 else ROW_B, outline="")
        self._bars(which, body, bottom, x_of, start_ms, end_ms)
        times, events = self.sources[which]
        if which == "tab" and not events:
            message = self.tab_note or "No Songsterr drums for this song."
            canvas.create_text((left + right) // 2, (body + bottom) // 2, text=message, fill=LABEL_TEXT, font=self.label_font)
        else:
            low = bisect.bisect_left(times, start_ms)
            high = bisect.bisect_right(times, end_ms)
            for ms, note, velocity in events[low:high]:
                row = ROW_OF.get(note)
                if row is not None:
                    self._hit(x_of(ms), body + row * ROW_H + ROW_H / 2, row, velocity, ms)
        # Everything left of the rows stays put and covers the hits sliding under it.
        canvas.create_rectangle(0, 0, left, HEADER_H, fill=HEADER_BG, outline="")
        title = "MIDI" if which == "midi" else f"Songsterr · {self.tab_name}"
        canvas.create_text(8, HEADER_H // 2, text=title, anchor="w", fill=HEADER_TEXT, font=self.head_font)
        for index, (_key, label, color, _shape, _notes) in enumerate(ROWS):
            y = body + index * ROW_H
            canvas.create_rectangle(0, y, left - 2, y + ROW_H, fill=ROW_A if index % 2 == 0 else ROW_B, outline="")
            canvas.create_text(left - 8, y + ROW_H // 2, text=label, anchor="e", fill=mix(CHART_BG, color, 0.85), font=self.label_font)
        canvas.create_line(now_x, 0, now_x, bottom, fill=NOW_LINE, width=2)
        self.paint_played()

    def bar_number(self, which: str, midi_bar: int) -> int:
        """The number shown at a bar line: the MIDI bar, or on the Songsterr rows the tab's own bar number."""
        if which == "midi":
            return midi_bar
        if self.tab_half:
            return (midi_bar - self.tab_shift) * 2 - 1
        return midi_bar - self.tab_shift

    def _hit(self, x: float, y: float, row: int, velocity: int, ms: float) -> None:
        canvas = self.canvas
        _key, _label, color, shape, _notes = ROWS[row]
        strength = 0.3 + 0.7 * max(0, min(127, velocity)) / 127
        fill = mix(CHART_BG, color, strength)
        radius = 4.0
        if velocity < GHOST:
            radius = 2.6
        elif velocity >= ACCENT:
            radius = 5.2
        tag = "scroll"
        items: list[int] = []
        if shape == "circle":
            items.append(canvas.create_oval(x - radius, y - radius, x + radius, y + radius, fill=fill, outline="", tags=tag))
        elif shape == "diamond":
            items.append(canvas.create_polygon(x, y - radius - 1, x + radius + 1, y, x, y + radius + 1, x - radius - 1, y, fill=fill, outline="", tags=tag))
        else:
            items.append(canvas.create_line(x - radius, y - radius, x + radius, y + radius, fill=fill, width=2, tags=tag))
            items.append(canvas.create_line(x - radius, y + radius, x + radius, y - radius, fill=fill, width=2, tags=tag))
            if shape == "xring":
                items.append(canvas.create_oval(x - radius - 2, y - radius - 2, x + radius + 2, y + radius + 2, outline=fill, tags=tag))
            elif shape == "star":
                items.append(canvas.create_line(x, y - radius - 1, x, y + radius + 1, fill=fill, width=2, tags=tag))
        self.remember_note(items, ms, fill)
