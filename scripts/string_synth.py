"""A small guitar, bass and voice sound, so they can be heard with the drums.

It is not a recording. Each note is a few harmonics under an envelope, built once per
pitch and mixed in like the drum hits. Notes are (ms, length ms, pitch, velocity, kind, flags).
Guitar and bass die away like plucked strings. The vocal is a soft held tone that rings for as
long as the note does. Palm-muted notes are short and dull, dead notes are a click, ghost notes are quiet.
"""

from __future__ import annotations

import math
import random

TABLE = 512
RAMP_S = 0.004
RELEASE_S = 0.06
BUCKET = 160  # note lengths are rounded up to this many samples so segments can be reused

# kind -> (bright harmonics, mellow harmonics, bright decay s, mellow decay s, longest ring s)
VOICES = {
    "guitar": ((1.0, 0.8, 0.6, 0.45, 0.35, 0.25, 0.2, 0.15, 0.1, 0.08), (1.0, 0.25, 0.08), 0.22, 0.8, 1.6),
    "bass": ((1.0, 0.55, 0.3, 0.15), (1.0, 0.2), 0.35, 1.1, 2.0),
    "vocal": ((1.0, 0.5, 0.3, 0.15, 0.08), (1.0, 0.15), 3.0, 6.0, 4.0),
}
BRIGHT_WEIGHT = {"guitar": 0.6, "bass": 0.45, "vocal": 0.3}
ATTACK_S = {"vocal": 0.03}

_VOICE: dict[tuple, list[float]] = {}
_SEGMENTS: dict[tuple, list[float]] = {}


def frequency(pitch: int) -> float:
    return 440.0 * 2 ** ((pitch - 69) / 12)


def from_kept(tracks, convert) -> list[tuple]:
    """Notes from keep_tracks.Kept parts. `convert` turns a tick into song milliseconds."""
    out = []
    for track in tracks:
        for tick, length, key, velocity in track.notes:
            start = convert(tick)
            out.append((start, max(1, convert(tick + length) - start), key, velocity, track.kind, ""))
    out.sort(key=lambda item: (item[0], item[2]))
    return out


def from_tab(mapped, tuning: list[int], kind: str) -> list[tuple]:
    """Notes from tab_roll.map_notes output: (ms, length ms, string, fret, flags). Tied notes ring on."""
    out: list[list] = []
    last: dict[int, list] = {}
    for ms, length, string, fret, flags in mapped:
        if string >= len(tuning):
            continue
        previous = last.get(string)
        if "t" in flags and previous is not None and previous[0] + previous[1] >= ms - 60:
            previous[1] = max(previous[1], ms + length - previous[0])
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
        pitch = max(0, min(127, int(tuning[string]) + int(fret)))
        note = [ms, max(1, length), pitch, velocity, kind, flags]
        out.append(note)
        last[string] = note
    return [tuple(note) for note in out]


def _table(harmonics: tuple, frequency_hz: float, rate: int) -> list[float]:
    """One cycle of a tone made of these harmonics, leaving out any that would not fit under the sample rate."""
    usable = [amount if (number + 1) * frequency_hz < rate * 0.45 else 0.0 for number, amount in enumerate(harmonics)]
    cycle = [
        sum(amount * math.sin(math.tau * (number + 1) * index / TABLE) for number, amount in enumerate(usable) if amount)
        for index in range(TABLE)
    ]
    peak = max(abs(value) for value in cycle) or 1.0
    return [value / peak for value in cycle]


def _voice(kind: str, pitch: int, style: str, rate: int) -> list[float]:
    key = (kind, pitch, style, rate)
    cached = _VOICE.get(key)
    if cached is not None:
        return cached
    if style == "dead":
        rng = random.Random(pitch)
        total = int(rate * 0.06)
        wave = [rng.uniform(-1, 1) * math.exp(-index / (rate * 0.012)) * 0.5 for index in range(total)]
        _VOICE[key] = wave
        return wave
    bright, mellow, bright_s, mellow_s, longest = VOICES[kind]
    hz = frequency(pitch)
    shrink = 0.25 if style == "mute" else 1.0
    weight = BRIGHT_WEIGHT[kind] * (0.35 if style == "mute" else 1.0)
    total = int(rate * (0.35 if style == "mute" else longest))
    table_bright = _table(bright, hz, rate)
    table_mellow = _table(mellow, hz, rate)
    step = hz * TABLE / rate
    ramp = max(1, int(rate * ATTACK_S.get(kind, RAMP_S)))
    bright_k = 1.0 / (rate * bright_s * shrink)
    mellow_k = 1.0 / (rate * mellow_s * shrink)
    wave = []
    for index in range(total):
        spot = int(index * step) & (TABLE - 1)
        value = table_bright[spot] * weight * math.exp(-index * bright_k) + table_mellow[spot] * (1 - weight) * math.exp(-index * mellow_k)
        wave.append(value * (index / ramp if index < ramp else 1.0))
    _VOICE[key] = wave
    return wave


def _segment(kind: str, pitch: int, style: str, rate: int, length_s: float) -> list[float]:
    """The note's sound cut off after length_s, with a short fade so it does not click."""
    voice = _voice(kind, pitch, style, rate)
    wanted = int((length_s + RELEASE_S) * rate)
    wanted = ((wanted + BUCKET - 1) // BUCKET) * BUCKET
    if wanted >= len(voice):
        return voice
    key = (kind, pitch, style, rate, wanted)
    cached = _SEGMENTS.get(key)
    if cached is None:
        cached = voice[:wanted]
        fade = min(len(cached), int(RELEASE_S * rate))
        for index in range(fade):
            cached[len(cached) - fade + index] *= 1 - (index + 1) / fade
        if len(_SEGMENTS) > 4000:
            _SEGMENTS.clear()
        _SEGMENTS[key] = cached
    return cached


def mix_notes(mix: list[float], notes: list[tuple], start_ms: float, end_ms: float, speed: float, rate: int, gain: float) -> int:
    """Add the notes that start in [start_ms, end_ms) to the mix. Returns how many were added."""
    total = len(mix)
    added = 0
    for ms, length, pitch, velocity, kind, flags in notes:
        if ms < start_ms or ms >= end_ms:
            continue
        pos = int((ms - start_ms) / 1000 / speed * rate)
        if pos >= total:
            continue
        style = "dead" if "d" in flags else "mute" if "p" in flags else "plain"
        length_s = max(0.1, length / 1000 / speed)
        segment = _segment(kind if kind in VOICES else "guitar", pitch, style, rate, length_s)
        level = max(0.2, velocity / 127) * gain
        end = min(total, pos + len(segment))
        mix[pos:end] = [old + new * level for old, new in zip(mix[pos:end], segment)]
        added += 1
    return added
