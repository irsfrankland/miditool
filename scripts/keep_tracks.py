"""Guitar, bass and vocal tracks that ride along with the drum part.

A saved MIDI used to carry only the drums. The guitar, bass and vocal tracks are
kept now, so the file can be used for more than the pedal later. They are
held as plain notes, [tick, length, key, velocity], with the channel and
program they played on. They follow every bar edit the drums follow.

Nothing here touches the drum part. Rhythm Converter still gets the drums
on channel 10 in the first track.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable

DRUM_CHANNEL = 9
GUITAR_PROGRAMS = range(24, 32)
BASS_PROGRAMS = range(32, 40)
VOCAL_PROGRAMS = (52, 53, 54, 85)  # choir aahs, voice oohs, synth voice, voice lead
VOCAL_WORDS = ("vocal", "vox", "voice", "singer", "melody", "choir")
DEFAULT_PROGRAM = {"guitar": 27, "bass": 33, "vocal": 54}
KINDS = ("guitar", "bass", "vocal")


@dataclass(frozen=True)
class Kept:
    name: str
    kind: str  # "guitar", "bass" or "vocal"
    channel: int
    program: int
    notes: tuple  # of (tick, length, key, velocity)

    @property
    def count(self) -> int:
        return len(self.notes)


def classify(name: str, program: int | None) -> str | None:
    """Guitar, bass, vocal, or None. The track name wins; the program fills in when it says nothing."""
    text = " ".join(name.lower().split())
    if "drum" not in text and "bassoon" not in text and "bass" in text:
        return "bass"
    if "guitar" in text or "gtr" in text:
        return "guitar"
    if any(word in text for word in VOCAL_WORDS):
        return "vocal"
    if program is not None:
        if program in BASS_PROGRAMS:
            return "bass"
        if program in GUITAR_PROGRAMS:
            return "guitar"
        if program in VOCAL_PROGRAMS:
            return "vocal"
    return None


def _lengths(ons: list, offs: list, default: int) -> list[list[int]]:
    """Pair note-ons with their note-offs. A note-on with no off lasts `default` ticks."""
    events = [(tick, 1, key, velocity) for tick, key, velocity in ons]
    events += [(tick, 0, key, 0) for tick, key in offs]
    events.sort(key=lambda item: (item[0], item[1]))
    open_notes: dict[int, list[list[int]]] = {}
    out: list[list[int]] = []
    for tick, is_on, key, velocity in events:
        if is_on:
            note = [tick, 0, key, velocity]
            out.append(note)
            open_notes.setdefault(key, []).append(note)
        elif open_notes.get(key):
            note = open_notes[key].pop(0)
            note[1] = max(1, tick - note[0])
    for note in out:
        if note[1] <= 0:
            note[1] = max(1, default)
    out.sort(key=lambda item: (item[0], item[2]))
    return out


def collect(tracks: Iterable[dict], ppq: int) -> list[Kept]:
    """The guitar and bass in a parsed file.

    `tracks` are dicts with name, notes (tick, channel, key, velocity), offs
    (tick, channel, key) and programs (channel, program). Channel 10 is never kept.
    """
    kept: list[Kept] = []
    for track in tracks:
        notes = track.get("notes") or []
        channels = sorted({channel for _tick, channel, _key, _velocity in notes if channel != DRUM_CHANNEL})
        name = str(track.get("name") or "").strip()
        for channel in channels:
            program = next((value for chan, value in track.get("programs") or [] if chan == channel), None)
            kind = classify(name, program)
            if kind is None:
                continue
            ons = [(tick, key, velocity) for tick, chan, key, velocity in notes if chan == channel]
            offs = [(tick, key) for tick, chan, key in track.get("offs") or [] if chan == channel]
            paired = _lengths(ons, offs, ppq // 2)
            if not paired:
                continue
            label = name or kind.title()
            if len(channels) > 1:
                label = f"{label} (channel {channel + 1})"
            kept.append(
                Kept(
                    name=label,
                    kind=kind,
                    channel=channel,
                    program=program if program is not None else DEFAULT_PROGRAM[kind],
                    notes=tuple(tuple(note) for note in paired),
                )
            )
    return kept


def _with(track: Kept, notes: list) -> Kept:
    return replace(track, notes=tuple(tuple(note) for note in notes))


def cut(tracks: list[Kept], start: int, end: int) -> list[Kept]:
    """Remove the notes that start in [start, end) and close the gap behind them."""
    gap = end - start
    out = []
    for track in tracks:
        notes = []
        for tick, length, key, velocity in track.notes:
            if start <= tick < end:
                continue
            if tick < start:
                length = min(length, start - tick)
            else:
                tick -= gap
            notes.append((tick, max(1, length), key, velocity))
        out.append(_with(track, notes))
    return out


def blank(tracks: list[Kept], start: int, end: int) -> list[Kept]:
    """Silence [start, end). Notes that ring into it stop where it starts."""
    out = []
    for track in tracks:
        notes = []
        for tick, length, key, velocity in track.notes:
            if start <= tick < end:
                continue
            if tick < start:
                length = min(length, start - tick)
            notes.append((tick, max(1, length), key, velocity))
        out.append(_with(track, notes))
    return out


def insert(tracks: list[Kept], at: int, gap: int) -> list[Kept]:
    """Open `gap` ticks of silence at `at`. Later notes move later; ringing notes stop at `at`."""
    out = []
    for track in tracks:
        notes = []
        for tick, length, key, velocity in track.notes:
            if tick >= at:
                tick += gap
            else:
                length = min(length, at - tick)
            notes.append((tick, max(1, length), key, velocity))
        out.append(_with(track, notes))
    return out


def copy_ranges(tracks: list[Kept], ranges: list[tuple[int, int, int]]) -> list[Kept]:
    """Notes from each (source_start, source_end, shift) range, moved by its shift."""
    out = []
    for track in tracks:
        notes = []
        for start, end, shift in ranges:
            for tick, length, key, velocity in track.notes:
                if start <= tick < end:
                    notes.append((tick + shift, max(1, min(length, end - tick)), key, velocity))
        notes.sort(key=lambda item: (item[0], item[2]))
        out.append(_with(track, notes))
    return out


def summary(tracks: list[Kept]) -> str:
    """'1 guitar and 1 bass', for a status line."""
    parts = []
    for kind in KINDS:
        count = sum(1 for track in tracks if track.kind == kind)
        if count:
            parts.append(f"{count} {kind}")
    if len(parts) > 2:
        return ", ".join(parts[:-1]) + " and " + parts[-1]
    return " and ".join(parts)


def _vlq(value: int) -> bytes:
    value = max(0, int(value))
    out = [value & 0x7F]
    value >>= 7
    while value:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    return bytes(reversed(out))


def chunk(track: Kept, end: int = 0) -> bytes:
    """One MTrk chunk. Channel and program are the ones the part played on."""
    channel = track.channel & 0x0F
    if channel == DRUM_CHANNEL:
        channel = 0
    title = track.name.encode("latin1", errors="replace")[:120]
    messages: list[tuple[int, int, bytes]] = [
        (0, 0, b"\xff\x03" + _vlq(len(title)) + title),
        (0, 1, bytes([0xC0 | channel, max(0, min(127, track.program))])),
    ]
    for tick, length, key, velocity in track.notes:
        key = max(0, min(127, int(key)))
        velocity = max(1, min(127, int(velocity)))
        messages.append((int(tick), 3, bytes([0x90 | channel, key, velocity])))
        messages.append((int(tick) + max(1, int(length)), 2, bytes([0x80 | channel, key, 0])))
    messages.sort(key=lambda item: (item[0], item[1]))
    last = max([end] + [tick for tick, _order, _data in messages])
    body = bytearray()
    cursor = 0
    for tick, _order, data in messages:
        body += _vlq(tick - cursor) + data
        cursor = tick
    body += _vlq(last - cursor) + b"\xff\x2f\x00"
    return b"MTrk" + len(body).to_bytes(4, "big") + bytes(body)


def kind_of(kind: str | None) -> str:
    """guitar, bass or vocal, from a part's own kind ('vocals' is Songsterr's word for vocal)."""
    if kind in ("bass",):
        return "bass"
    if kind in ("vocal", "vocals"):
        return "vocal"
    return "guitar"


def place_tab(
    notes,
    bounds: list[int],
    ppq: int,
    channel: int,
    program: int | None = None,
    kind: str | None = None,
    shift: int = 0,
    half: bool = False,
) -> Kept | None:
    """A Songsterr part as notes on the MIDI's own bars, `bounds`, the way the line-up places the tab.

    `notes` is a songsterr.TabNotes. Tab bar n lands on MIDI bar n + 1 + shift; in half time
    one MIDI bar holds two tab bars. Notes on tab bars with no MIDI bar to land on are left out.
    Tied notes ring on. Returns None when nothing lands.
    """
    out: list[list[int]] = []
    last_on_string: dict[int, list[int]] = {}
    bars = len(bounds) - 1
    for bar, start, share, string, fret, flags in notes.notes:
        if half:
            midi_bar, inner, portion = bar // 2 + 1 + shift, (bar % 2) / 2, 0.5
        else:
            midi_bar, inner, portion = bar + 1 + shift, 0.0, 1.0
        if not 1 <= midi_bar <= bars or string >= len(notes.tuning):
            continue
        span = bounds[midi_bar] - bounds[midi_bar - 1]
        tick = bounds[midi_bar - 1] + round((inner + start * portion) * span)
        length = max(1, round(share * portion * span))
        if "d" in flags:
            length = max(1, min(length, ppq // 8))
        previous = last_on_string.get(string)
        if "t" in flags and previous is not None and previous[0] + previous[1] >= tick - ppq // 16:
            previous[1] = max(previous[1], tick + length - previous[0])
            continue
        velocity = 92
        if "a" in flags:
            velocity = 112
        elif "g" in flags:
            velocity = 48
        elif "d" in flags:
            velocity = 52
        elif "p" in flags:
            velocity = 74
        pitch = max(0, min(127, notes.tuning[string] + fret))
        note = [tick, length, pitch, velocity]
        out.append(note)
        last_on_string[string] = note
    if not out:
        return None
    for note in out:
        note[1] = max(1, round(note[1] * 0.96))
    out.sort(key=lambda item: (item[0], item[2]))
    kind = kind_of(kind or notes.kind)
    chosen = program if program is not None and 0 <= program <= 127 else DEFAULT_PROGRAM[kind]
    return Kept(
        name=notes.name,
        kind=kind,
        channel=channel,
        program=chosen,
        notes=tuple(tuple(note) for note in out),
    )


def from_tab(
    notes, bounds: list[int], ppq: int, channel: int, program: int | None = None, kind: str | None = None
) -> Kept | None:
    """A Songsterr part as notes on the drum timeline `bounds` it was made from.

    Its played bars must be the same bars the drums were turned into, or nothing is returned.
    `kind` says guitar, bass or vocal when the part itself does not.
    """
    if notes.bars != len(bounds) - 1 or not notes.notes:
        return None
    return place_tab(notes, bounds, ppq, channel, program, kind)
