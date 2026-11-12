"""Write the finder's edited drum part as a type 0 MIDI for Rhythm Converter.

The source MIDI is never changed. The export carries the drum notes on
channel 10, the tempo map, and a time signature for every meter change.
"""

from __future__ import annotations

from pathlib import Path

PEDAL_ORDER = ("intro", "pattern1", "pattern1Fill", "pattern2", "pattern2Fill", "ending")


def _vlq(value: int) -> bytes:
    value = max(0, int(value))
    out = [value & 0x7F]
    value >>= 7
    while value:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    return bytes(reversed(out))


def meter_for(length: int, ppq: int, preferred: tuple[int, int]) -> tuple[int, int]:
    """The meter that fills a bar of this many ticks, keeping the written one when it fits."""
    numerator, denominator = preferred
    if ppq * numerator * 4 // denominator == length:
        return preferred
    for den in (denominator, 4, 8, 16):
        top = length * den
        if top % (4 * ppq) == 0 and 1 <= top // (4 * ppq) <= 64:
            return top // (4 * ppq), den
    return preferred


def tempo_at(tempos: list[tuple[int, int]], tick: int) -> int:
    current = tempos[0][1] if tempos else 500_000
    for at, microseconds in tempos:
        if at <= tick:
            current = microseconds
        else:
            break
    return current


def clean_tempos(tempos: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Sorted tempo changes, one per tick, starting at tick 0."""
    merged: dict[int, int] = {}
    for tick, microseconds in sorted(tempos):
        if microseconds > 0:
            merged[max(0, int(tick))] = int(microseconds)
    points = sorted(merged.items())
    if not points:
        return [(0, 500_000)]
    if points[0][0] != 0:
        points.insert(0, (0, points[0][1]))
    kept = [points[0]]
    for tick, microseconds in points[1:]:
        if microseconds != kept[-1][1]:
            kept.append((tick, microseconds))
    return kept


def build(
    ppq: int,
    events: list,
    tempos: list[tuple[int, int]],
    boundaries: list[int],
    meters: list[tuple[int, int]],
    name: str = "Drums",
) -> bytes:
    messages: list[tuple[int, int, bytes]] = []
    title = name.encode("latin1", errors="replace")[:120]
    messages.append((0, 0, b"\xff\x03" + _vlq(len(title)) + title))
    for tick, microseconds in clean_tempos(tempos):
        messages.append((tick, 1, b"\xff\x51\x03" + int(microseconds).to_bytes(3, "big")))
    previous = None
    for index in range(len(boundaries) - 1):
        length = boundaries[index + 1] - boundaries[index]
        if length <= 0:
            continue
        preferred = meters[index] if index < len(meters) else (4, 4)
        numerator, denominator = meter_for(length, ppq, preferred)
        if (numerator, denominator) != previous:
            power = max(0, denominator.bit_length() - 1)
            messages.append((boundaries[index], 2, b"\xff\x58\x04" + bytes([numerator, power, 24, 8])))
            previous = (numerator, denominator)
    gate = max(1, ppq // 8)
    for tick, note, velocity in events:
        note = max(0, min(127, int(note)))
        velocity = max(1, min(127, int(velocity)))
        messages.append((int(tick), 4, bytes([0x99, note, velocity])))
        messages.append((int(tick) + gate, 3, bytes([0x89, note, 0])))
    messages.sort(key=lambda item: (item[0], item[1]))
    end = max([boundaries[-1] if boundaries else 0] + [tick for tick, _order, _data in messages])
    track = bytearray()
    cursor = 0
    for tick, _order, data in messages:
        track += _vlq(tick - cursor) + data
        cursor = tick
    track += _vlq(end - cursor) + b"\xff\x2f\x00"
    header = b"MThd" + (6).to_bytes(4, "big") + (0).to_bytes(2, "big") + (1).to_bytes(2, "big") + int(ppq).to_bytes(2, "big")
    return header + b"MTrk" + len(track).to_bytes(4, "big") + bytes(track)


def pedal_parts(
    events: list,
    tempos: list[tuple[int, int]],
    boundaries: list[int],
    meters: list[tuple[int, int]],
    divisions: dict[str, list[int]],
) -> tuple[list, list[tuple[int, int]], list[int], list[tuple[int, int]], dict[str, list[int]]]:
    """Copy only the pedal ranges, back to back, in pedal order.

    A range that sits inside one already copied, like a fill inside its
    pattern, points at those bars instead of being copied twice.
    """
    tempos = clean_tempos(tempos)
    out_events: list = []
    out_tempos: list[tuple[int, int]] = []
    out_bounds = [0]
    out_meters: list[tuple[int, int]] = []
    copied: list[tuple[int, int, int]] = []
    placed: dict[str, list[int]] = {}
    for key in PEDAL_ORDER:
        start, end = divisions[key]
        inside = next((item for item in copied if item[0] <= start and end <= item[1]), None)
        if inside is not None:
            offset = inside[2] - inside[0]
            placed[key] = [start + offset, end + offset]
            continue
        first_bar = len(out_meters) + 1
        source_a = boundaries[start - 1]
        source_b = boundaries[end]
        cursor = out_bounds[-1]
        delta = cursor - source_a
        for bar in range(start, end + 1):
            out_meters.append(meters[bar - 1] if bar - 1 < len(meters) else (4, 4))
            out_bounds.append(boundaries[bar] + delta)
        out_events.extend([tick + delta, note, velocity] for tick, note, velocity in events if source_a <= tick < source_b)
        out_tempos.append((cursor, tempo_at(tempos, source_a)))
        out_tempos.extend((tick + delta, microseconds) for tick, microseconds in tempos if source_a < tick < source_b)
        copied.append((start, end, first_bar))
        placed[key] = [first_bar, first_bar + end - start]
    out_events.sort(key=lambda item: (item[0], item[1]))
    return out_events, clean_tempos(out_tempos), out_bounds, out_meters, placed


def tab_bar_lengths(bars: list[dict], tab_tempos: list[tuple[int, float]]) -> list[float]:
    """Milliseconds for each played tab bar, from its meter and the tab's tempo changes."""
    changes = dict(tab_tempos)
    bpm = tab_tempos[0][1] if tab_tempos else 120.0
    lengths = []
    for index, bar in enumerate(bars, start=1):
        bpm = changes.get(index, bpm)
        numerator, denominator = bar["meter"]
        lengths.append(numerator * (4 / denominator) * 60_000 / max(float(bpm), 1.0))
    return lengths


def retime_to_tab(
    ppq: int,
    boundaries: list[int],
    tempos: list[tuple[int, int]],
    tab_lengths: list[float],
    shift: int,
    half: bool,
) -> tuple[list[tuple[int, int]], int]:
    """A tempo map that gives each MIDI bar the length of its tab bar.

    MIDI bar = tab bar + shift. In half time one MIDI bar spans two tab bars.
    Notes keep their ticks, so only timing changes. Bars with no tab bar keep
    their own tempo. Returns (tempos, bars matched).
    """
    tempos = clean_tempos(tempos)
    out: list[tuple[int, int]] = []
    matched = 0
    for bar in range(1, len(boundaries)):
        start, end = boundaries[bar - 1], boundaries[bar]
        ticks = end - start
        tab_bar = bar - shift
        wanted = [2 * tab_bar - 1, 2 * tab_bar] if half else [tab_bar]
        if ticks > 0 and all(1 <= index <= len(tab_lengths) for index in wanted):
            length = sum(tab_lengths[index - 1] for index in wanted)
            out.append((start, max(1, round(length * 1000 * ppq / ticks))))
            matched += 1
            continue
        out.append((start, tempo_at(tempos, start)))
        out.extend((tick, microseconds) for tick, microseconds in tempos if start < tick < end)
    return clean_tempos(out), matched


def write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
