"""Read drum MIDIs and the RC-10R song list in songs/ready.json.

The finder uses this module to parse MIDI files and count kit hits per bar.
Run it directly to check the song list. The eight original songs have locked
start × length values in EXPECTED. This module does not rewrite MIDI files.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAP_PATH = ROOT / "songs" / "ready.json"

DIVISION_ORDER = (
    ("intro", "Intro"),
    ("pattern1", "Pattern 1"),
    ("pattern1Fill", "Pattern 1 fill"),
    ("pattern2", "Pattern 2"),
    ("pattern2Fill", "Pattern 2 fill"),
    ("ending", "Ending"),
)

STORED_KEYS = ("intro", "pattern1", "pattern2", "ending")

# Start measure and length. Length is last bar - first bar + 1.
# A mismatch with songs/ready.json fails the check.
EXPECTED = {
    "thunderstruck": {
        "intro": (1, 91),
        "pattern1": (92, 12),
        "pattern1Fill": (84, 1),
        "pattern2": (142, 16),
        "pattern2Fill": (157, 1),
        "ending": (158, 5),
    },
    "panama": {
        "intro": (1, 42),
        "pattern1": (43, 8),
        "pattern1Fill": (41, 1),
        "pattern2": (83, 20),
        "pattern2Fill": (120, 1),
        "ending": (120, 5),
    },
    "good-times-bad-times": {
        "intro": (1, 17),
        "pattern1": (18, 5),
        "pattern1Fill": (17, 1),
        "pattern2": (23, 8),
        "pattern2Fill": (67, 1),
        "ending": (49, 20),
    },
    "aint-talkin-bout-love": {
        "intro": (1, 24),
        "pattern1": (25, 8),
        "pattern1Fill": (26, 1),
        "pattern2": (65, 16),
        "pattern2Fill": (124, 1),
        "ending": (121, 11),
    },
    "communication-breakdown": {
        "intro": (1, 24),
        "pattern1": (25, 12),
        "pattern1Fill": (24, 1),
        "pattern2": (63, 14),
        "pattern2Fill": (112, 1),
        "ending": (89, 28),
    },
    "ego-brain": {
        "intro": (1, 18),
        "pattern1": (19, 8),
        "pattern1Fill": (18, 1),
        "pattern2": (45, 10),
        "pattern2Fill": (66, 1),
        "ending": (67, 1),
    },
    "why-dont-you-get-a-job": {
        "intro": (1, 17),
        "pattern1": (18, 15),
        "pattern1Fill": (17, 1),
        "pattern2": (33, 16),
        "pattern2Fill": (48, 1),
        "ending": (74, 1),
    },
    "ramble-on": {
        "intro": (1, 29),
        "pattern1": (30, 8),
        "pattern1Fill": (63, 1),
        "pattern2": (86, 13),
        "pattern2Fill": (98, 1),
        "ending": (99, 9),
    },
}


def read_vlq(data: bytes, index: int) -> tuple[int, int]:
    value = 0
    while index < len(data):
        byte = data[index]
        index += 1
        value = (value << 7) | (byte & 0x7F)
        if byte < 0x80:
            return value, index
    raise ValueError("truncated variable-length quantity")


def parse_track(
    data: bytes,
) -> tuple[
    list[tuple[int, int, int, int]],
    list[tuple[int, int]],
    list[tuple[int, int, int]],
    int,
    str,
    list[tuple[int, int]],
    list[str],
]:
    index = 0
    tick = 0
    running = None
    notes: list[tuple[int, int, int, int]] = []
    tempos: list[tuple[int, int]] = []
    signatures: list[tuple[int, int, int]] = []
    programs: list[tuple[int, int]] = []
    instruments: list[str] = []
    end_tick = 0
    name = ""

    while index < len(data):
        delta, index = read_vlq(data, index)
        tick += delta
        end_tick = tick
        if index >= len(data):
            break

        byte = data[index]
        if byte >= 0x80:
            status = byte
            index += 1
            if status < 0xF0:
                running = status
            elif status in (0xF0, 0xF7) or status == 0xFF:
                running = None
            elif 0xF8 <= status < 0xFF:
                continue
        else:
            if running is None:
                raise ValueError(f"running status missing at byte {index}")
            status = running

        if status == 0xFF:
            meta_type = data[index]
            index += 1
            length, index = read_vlq(data, index)
            payload = data[index : index + length]
            index += length
            if meta_type == 0x03 and not name:
                name = payload.decode("latin1", errors="replace").strip()
            elif meta_type == 0x04:
                instrument = payload.decode("latin1", errors="replace").strip()
                if instrument:
                    instruments.append(instrument)
            elif meta_type == 0x51 and len(payload) == 3:
                microseconds = (payload[0] << 16) | (payload[1] << 8) | payload[2]
                if microseconds:
                    tempos.append((tick, microseconds))
            elif meta_type == 0x58 and len(payload) >= 2:
                numerator = payload[0]
                denominator = 1 << payload[1]
                signatures.append((tick, numerator, denominator))
            elif meta_type == 0x2F:
                break
        elif status in (0xF0, 0xF7):
            length, index = read_vlq(data, index)
            index += length
        elif status == 0xF2:
            index += 2
        elif status in (0xF1, 0xF3):
            index += 1
        elif status == 0xF6:
            continue
        else:
            kind = status & 0xF0
            if kind in (0xC0, 0xD0):
                if kind == 0xC0:
                    programs.append((status & 0x0F, data[index]))
                index += 1
            elif kind in (0x80, 0x90, 0xA0, 0xB0, 0xE0):
                note = data[index]
                velocity = data[index + 1]
                index += 2
                if kind == 0x90 and velocity > 0:
                    notes.append((tick, status & 0x0F, note, velocity))
            else:
                raise ValueError(f"unsupported status {status:#x} at tick {tick}")

    return notes, tempos, signatures, end_tick, name, programs, instruments


def parse_midi(path: Path) -> dict:
    data = path.read_bytes()
    if data[:4] != b"MThd":
        raise ValueError(f"{path.name} is not a Standard MIDI file")
    header_len = int.from_bytes(data[4:8], "big")
    track_count = int.from_bytes(data[10:12], "big")
    division = int.from_bytes(data[12:14], "big")
    if division & 0x8000:
        raise ValueError(f"{path.name} uses SMPTE timing, which this chart does not read")
    ppq = division

    position = 8 + header_len
    tracks = []
    tempos: list[tuple[int, int]] = []
    signatures: list[tuple[int, int, int]] = []
    end_tick = 0
    for _ in range(track_count):
        if data[position : position + 4] != b"MTrk":
            raise ValueError(f"{path.name} is missing a track chunk")
        length = int.from_bytes(data[position + 4 : position + 8], "big")
        track = data[position + 8 : position + 8 + length]
        position += 8 + length
        track_notes, track_tempos, track_sigs, track_end, track_name, track_programs, track_instruments = parse_track(track)
        tracks.append(
            {
                "name": track_name,
                "notes": track_notes,
                "programs": track_programs,
                "instruments": track_instruments,
            }
        )
        tempos.extend(track_tempos)
        signatures.extend(track_sigs)
        end_tick = max(end_tick, track_end)

    drum_events = kit_events(tracks)
    drum_ticks = [tick for tick, _note, _velocity in drum_events]
    if drum_ticks:
        end_tick = max(drum_ticks) + 1
    track_summaries = []
    for track in tracks:
        channels = sorted({channel for _tick, channel, _note, _velocity in track["notes"]})
        track_summaries.append(
            {
                "name": track["name"],
                "notes": len(track["notes"]),
                "channels": channels,
                "programs": track["programs"],
                "instruments": track["instruments"],
            }
        )

    return {
        "ppq": ppq,
        "notes": drum_ticks,
        "events": drum_events,
        "tempos": tempos,
        "signatures": signatures,
        "end_tick": end_tick,
        "tracks": track_summaries,
    }


def is_kit_track(name: str) -> bool:
    text = " ".join(name.lower().split())
    if "perc" in text or "steel" in text:
        return False
    return "drum" in text


def kit_events(tracks: list[dict]) -> list[tuple[int, int, int]]:
    """Hits from the kit track, or channel 10 when the file has no kit track.

    These MIDIs are full arrangements. Guitar and bass notes are not the
    drum part. A separate percussion track, such as Ramble On's congas,
    stays out of the kit so acoustic sections read as empty.
    """
    kit_tracks = [track for track in tracks if is_kit_track(track["name"])]
    source = kit_tracks if kit_tracks else tracks
    events = []
    for track in source:
        for tick, channel, note, velocity in track["notes"]:
            if kit_tracks or channel == 9:
                events.append((tick, note, velocity))
    events.sort()
    return events


def tick_to_ms(tempos: list[tuple[int, int]], ppq: int):
    """Return a function that converts a tick to milliseconds using the tempo map."""
    ordered = sorted((tick, us) for tick, us in tempos if us > 0)
    points: list[tuple[int, int]] = []
    for tick, us in ordered:
        if points and points[-1][0] == tick:
            points[-1] = (tick, us)
        else:
            points.append((tick, us))
    if not points or points[0][0] > 0:
        first = points[0][1] if points else 500_000
        points = [(0, first)] + [item for item in points if item[0] > 0]
    anchors: list[tuple[int, int, float]] = []
    seconds = 0.0
    last_tick = 0
    microseconds = points[0][1]
    for tick, new_us in points:
        if tick > last_tick:
            seconds += (tick - last_tick) * microseconds / ppq / 1_000_000
        anchors.append((tick, new_us, seconds))
        last_tick = tick
        microseconds = new_us

    def convert(tick: int) -> int:
        chosen = anchors[0]
        for anchor in anchors:
            if anchor[0] <= tick:
                chosen = anchor
            else:
                break
        at, micro, base = chosen
        return round((base + (tick - at) * micro / ppq / 1_000_000) * 1000)

    return convert


def build_measures(midi: dict) -> tuple[list[int], list[tuple[int, int, int]], list[int]]:
    """Return hit counts, meter changes, and the tick at the start of every bar plus the end tick."""
    ppq = midi["ppq"]
    signatures = sorted(midi["signatures"])
    if not signatures or signatures[0][0] != 0:
        signatures = [(0, 4, 4)] + [item for item in signatures if item[0] != 0]
    collapsed: list[tuple[int, int, int]] = []
    for item in signatures:
        if collapsed and collapsed[-1][0] == item[0]:
            collapsed[-1] = item
        else:
            collapsed.append(item)

    notes = sorted(midi["notes"])
    end_tick = midi["end_tick"]

    hits: list[int] = []
    meters: list[tuple[int, int, int]] = []
    boundaries = [0]
    tick = 0
    sig_index = 0
    numerator, denominator = collapsed[0][1], collapsed[0][2]
    meters.append((1, numerator, denominator))
    note_index = 0

    while tick < end_tick and len(hits) < 20000:
        while sig_index + 1 < len(collapsed) and collapsed[sig_index + 1][0] <= tick:
            sig_index += 1
            numerator, denominator = collapsed[sig_index][1], collapsed[sig_index][2]
            meters.append((len(hits) + 1, numerator, denominator))
        ticks_per_bar = ppq * numerator * 4 // denominator
        if ticks_per_bar <= 0:
            raise ValueError(f"bad meter {numerator}/{denominator}")
        bar_end = tick + ticks_per_bar
        if sig_index + 1 < len(collapsed) and tick < collapsed[sig_index + 1][0] < bar_end:
            bar_end = collapsed[sig_index + 1][0]
        count = 0
        while note_index < len(notes) and notes[note_index] < bar_end:
            if notes[note_index] >= tick:
                count += 1
            note_index += 1
        hits.append(count)
        tick = bar_end
        boundaries.append(tick)

    if not hits:
        boundaries = [0, 0]
    return hits, meters, boundaries


def tempo_text(tempos: list[tuple[int, int]]) -> str:
    if not tempos:
        return "No tempo in the file"
    ordered = sorted(tempos)
    first = round(60_000_000 / ordered[0][1])
    distinct = {round(60_000_000 / us) for _, us in ordered}
    if len(distinct) == 1:
        return f"{first} BPM"
    return f"{len(ordered)} tempo changes, starting at {first} BPM"


def meter_text(meters: list[tuple[int, int, int]]) -> str:
    parts: list[str] = []
    previous = None
    for bar, numerator, denominator in meters:
        label = f"{numerator}/{denominator}"
        if label == previous:
            continue
        parts.append(label if bar == 1 else f"{label} at bar {bar}")
        previous = label
    return ", ".join(parts) if parts else "4/4"


def length_of(span: list[int]) -> int:
    return span[1] - span[0] + 1


def check_expected(song: dict) -> None:
    expected = EXPECTED.get(song["id"])
    if expected is None:
        typed = ", ".join(
            f"{label} {song['divisions'][key][0]}×{length_of(song['divisions'][key])}"
            for key, label in DIVISION_ORDER
        )
        print(f"{song['title']}: new song, using the map ({typed})")
        return
    for key, _label in DIVISION_ORDER:
        start, end = song["divisions"][key]
        got = (start, length_of(song["divisions"][key]))
        if got != expected[key]:
            raise SystemExit(
                f"{song['title']} {key} is {got[0]} × {got[1]}, expected {expected[key][0]} × {expected[key][1]}"
            )


def paint_roles(count: int, divisions: dict) -> list[str]:
    roles = ["unstored"] * count
    pattern1 = divisions["pattern1"]
    pattern2 = divisions["pattern2"]
    keys = list(STORED_KEYS)
    if pattern1 == pattern2:
        keys = [key for key in keys if key != "pattern2"]
    for key in keys:
        start, end = divisions[key]
        for bar in range(start, end + 1):
            index = bar - 1
            if 0 <= index < count:
                roles[index] = key
    return roles


def role_spans(roles: list[str]) -> list[tuple[str, int, int]]:
    spans: list[tuple[str, int, int]] = []
    index = 0
    while index < len(roles):
        end = index
        while end < len(roles) and roles[end] == roles[index]:
            end += 1
        spans.append((roles[index], index + 1, end))
        index = end
    return spans


LOOP_KEYS = {"pattern1", "pattern1Fill", "pattern2", "pattern2Fill"}


def drum_root(catalog: dict) -> Path:
    """The drum MIDI folder. A relative drumRoot is read from the project folder, so the project can move."""
    root = Path(catalog["drumRoot"])
    if not root.is_absolute():
        root = ROOT / root
    return root.resolve()


def main() -> None:
    """Check the song list: every MIDI is there, and the eight original songs keep their ranges."""
    catalog = json.loads(MAP_PATH.read_text(encoding="utf-8"))
    root = drum_root(catalog)
    for song in catalog["songs"]:
        path = root / song["file"]
        if not path.is_file():
            raise SystemExit(f"Missing MIDI file: {path}")
        check_expected(song)
        midi = parse_midi(path)
        hits, meters, _boundaries = build_measures(midi)
        last_needed = max(span[1] for span in song["divisions"].values())
        short = f"  The ranges use bar {last_needed}." if len(hits) < last_needed else ""
        print(f"{song['title']}: {len(hits)} measures, {tempo_text(midi['tempos'])}, {meter_text(meters)}.{short}")
    print(f"{len(catalog['songs'])} songs checked.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(exc, file=sys.stderr)
        raise
