"""Shared helpers for the tests: put scripts/ on the path and build small MIDI files."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import midi_export  # noqa: E402

PPQ = 480
BAR = PPQ * 4
KICK, SNARE, HAT = 36, 38, 42


def vlq(value: int) -> bytes:
    return midi_export._vlq(value)


def track_chunk(events: list[tuple[int, bytes]]) -> bytes:
    """events: (absolute tick, raw message bytes). Adds delta times and an end-of-track."""
    body = bytearray()
    cursor = 0
    for tick, data in sorted(events, key=lambda item: item[0]):
        body += vlq(tick - cursor) + data
        cursor = tick
    body += vlq(0) + b"\xff\x2f\x00"
    return b"MTrk" + len(body).to_bytes(4, "big") + bytes(body)


def midi_file(tracks: list[bytes], ppq: int = PPQ, fmt: int = 1) -> bytes:
    header = b"MThd" + (6).to_bytes(4, "big") + fmt.to_bytes(2, "big") + len(tracks).to_bytes(2, "big") + ppq.to_bytes(2, "big")
    return header + b"".join(tracks)


def name_event(name: str) -> tuple[int, bytes]:
    raw = name.encode("latin1")
    return 0, b"\xff\x03" + vlq(len(raw)) + raw


def tempo_event(tick: int, bpm: float) -> tuple[int, bytes]:
    return tick, b"\xff\x51\x03" + int(60_000_000 / bpm).to_bytes(3, "big")


def meter_event(tick: int, num: int, den: int) -> tuple[int, bytes]:
    return tick, b"\xff\x58\x04" + bytes([num, den.bit_length() - 1, 24, 8])


def hit(tick: int, note: int = SNARE, velocity: int = 100, channel: int = 9) -> list[tuple[int, bytes]]:
    return [(tick, bytes([0x90 | channel, note, velocity])), (tick + 10, bytes([0x80 | channel, note, 0]))]


def drum_midi(bar_hits: list[int], bpm: float = 120, name: str = "Drums") -> bytes:
    """A 4/4 kit track with the given number of evenly spread hits in each bar."""
    events = [name_event(name), tempo_event(0, bpm), meter_event(0, 4, 4)]
    for bar, count in enumerate(bar_hits):
        for index in range(count):
            events += hit(bar * BAR + index * (BAR // max(count, 1)))
    # a trailing marker so empty last bars still count
    return midi_file([track_chunk(events)])


def fake_drum_track(bars: int) -> dict:
    """A Songsterr-style drum part: four quarter-note beats a bar, kick/hat then snare/hat, a crash on each bar's first beat."""
    measures = []
    for _ in range(bars):
        beats = []
        for index, drum in enumerate((36, 38, 36, 38)):
            notes = [{"fret": drum}, {"fret": 42}]
            if index == 0:
                notes.append({"fret": 49})
            beats.append({"duration": [1, 4], "notes": notes})
        measures.append({"signature": [4, 4], "voices": [{"beats": beats}]})
    return {"name": "Drums | Kit", "instrumentId": 1024, "measures": measures,
            "automations": {"tempo": [{"measure": 0, "bpm": 120, "type": 4}]}}


def band_midi(bar_hits: list[int], bpm: float = 120, vocal: bool = False) -> bytes:
    """drum_midi plus a guitar track (a half-bar note each bar), a bass track (a bar-long note each bar)
    and a piano track that must not be kept. With vocal, also a "Lead Vocal" track (a half-bar note each bar)."""
    singer = [name_event("Lead Vocal"), (0, bytes([0xC3, 54]))]
    drums = [name_event("Drums"), tempo_event(0, bpm), meter_event(0, 4, 4)]
    guitar = [name_event("Lead Guitar"), (0, bytes([0xC0, 29]))]
    bass = [name_event("Bass"), (0, bytes([0xC1, 33]))]
    piano = [name_event("Piano"), (0, bytes([0xC2, 0]))]
    for bar, count in enumerate(bar_hits):
        for index in range(count):
            drums += hit(bar * BAR + index * (BAR // max(count, 1)))
        guitar += [(bar * BAR, bytes([0x90, 64 + bar % 12, 90])), (bar * BAR + BAR // 2, bytes([0x80, 64 + bar % 12, 0]))]
        bass += [(bar * BAR, bytes([0x91, 40, 100])), (bar * BAR + BAR - 1, bytes([0x81, 40, 0]))]
        piano += hit(bar * BAR, 60, 80, channel=2)
        singer += [(bar * BAR, bytes([0x93, 60 + bar % 5, 90])), (bar * BAR + BAR // 2, bytes([0x83, 60 + bar % 5, 0]))]
    tracks = [track_chunk(drums), track_chunk(guitar), track_chunk(bass), track_chunk(piano)]
    return midi_file(tracks + ([track_chunk(singer)] if vocal else []))


def fake_string_track(bars: int, name: str, instrument: int, tuning: list[int], string: int = 1, fret: int = 3) -> dict:
    """A Songsterr-style part with one whole-note a bar on one string."""
    measures = [
        {"signature": [4, 4], "voices": [{"beats": [{"duration": [1, 1], "notes": [{"string": string, "fret": fret}]}]}]}
        for _ in range(bars)
    ]
    return {"name": name, "instrumentId": instrument, "strings": len(tuning), "tuning": tuning, "measures": measures}


def fake_guitar_track(bars: int) -> dict:
    return fake_string_track(bars, "Lead Guitar", 29, [64, 59, 55, 50, 45, 40])


def fake_vocal_track(bars: int) -> dict:
    """A Songsterr vocal: one pitched note a bar on the top string, rising a step each bar."""
    track = fake_string_track(bars, "Lead Vocals", 56, [69, 64, 60, 55], string=0, fret=0)
    track["hash"] = "vocals_0"
    for number, measure in enumerate(track["measures"]):
        measure["voices"][0]["beats"][0]["notes"][0]["fret"] = number % 5
    return track


def fake_bass_track(bars: int) -> dict:
    return fake_string_track(bars, "Bass", 33, [43, 38, 33, 28], string=2, fret=0)
