"""Read a song's sections, tempo, meter, and drums from its Songsterr tab.

The drum notes line the tab's bars up with the MIDI's bars. When asked,
drums_to_midi() also turns them into a MIDI timeline that replaces the
file's drums.
"""

from __future__ import annotations

import gzip
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from fractions import Fraction
from pathlib import Path

BROWSER = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
API = "https://www.songsterr.com/api"
TRACK_HOST = "https://dqsljvtekg760.cloudfront.net"
DRUM_INSTRUMENT = 1024
MAX_SHIFT = 24
PART_KINDS = ("drums", "guitar", "bass")


@dataclass
class Part:
    index: int
    kind: str
    name: str
    views: int = 0


def part_kind(track: dict) -> str:
    instrument = str(track.get("instrument") or "").lower()
    prefix = str(track.get("hash") or "").split("_", 1)[0].lower()
    if track.get("instrumentId") == DRUM_INSTRUMENT or prefix == "drums" or "drum" in instrument:
        return "drums"
    if prefix in ("bass", "guitar", "vocals"):
        return prefix
    if "bass" in instrument:
        return "bass"
    if "guitar" in instrument:
        return "guitar"
    return "other"


def part_name(name: str) -> str:
    """'Angus Young | Gibson SG Standard | Lead Guitar' becomes 'Lead Guitar (Angus Young)'."""
    pieces = [piece.strip() for piece in name.split("|") if piece.strip()]
    if len(pieces) >= 2:
        return f"{pieces[-1]} ({pieces[0]})"
    return pieces[0] if pieces else "Unnamed part"


@dataclass
class Tab:
    song_id: int
    artist: str
    title: str
    url: str
    label: str
    drum_track: int | None = None
    bars: list[dict] = field(default_factory=list)
    sections: list[tuple[int, int, str]] = field(default_factory=list)
    tempos: list[tuple[int, float]] = field(default_factory=list)
    error: str = ""
    base_url: str = ""
    parts: list[Part] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.bars)

    def part_url(self, index: int) -> str:
        return f"{self.base_url or self.url}t{index}"

    def best_part(self, kind: str) -> Part | None:
        """The most viewed part of that kind, which is the one Songsterr opens first."""
        matches = [part for part in self.parts if part.kind == kind]
        return max(matches, key=lambda part: part.views) if matches else None

    def bar_hits(self) -> list[int]:
        return [bar["hits"] for bar in self.bars]

    def seconds(self) -> float:
        total = 0.0
        tempo_at = dict(self.tempos)
        bpm = self.tempos[0][1] if self.tempos else 120.0
        for index, bar in enumerate(self.bars, start=1):
            bpm = tempo_at.get(index, bpm)
            numerator, denominator = bar["meter"]
            total += numerator * (4 / denominator) * 60 / max(bpm, 1)
        return total

    def first_bpm(self) -> float | None:
        return self.tempos[0][1] if self.tempos else None

    def meter_text(self) -> str:
        parts: list[str] = []
        previous = None
        for index, bar in enumerate(self.bars, start=1):
            label = f"{bar['meter'][0]}/{bar['meter'][1]}"
            if label != previous:
                parts.append(label if index == 1 else f"{label} at bar {index}")
                previous = label
        return ", ".join(parts) or "4/4"


def slug(text: str) -> str:
    cleaned = "".join(char.lower() if char.isalnum() else "-" for char in text)
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-") or "song"


def same_title(left: str, right: str) -> bool:
    """TNT and T.N.T. are the same title; only letters and digits are compared."""
    def letters(text: str) -> str:
        return "".join(char for char in text.lower() if char.isalnum())

    return bool(letters(left)) and letters(left) == letters(right)


def _get(url: str, timeout: int = 15) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": BROWSER})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = response.read()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return data


def _json(url: str):
    return json.loads(_get(url).decode("utf-8"))


def search_url(artist: str, title: str) -> str:
    query = " ".join(part for part in (artist.strip(), title.strip()) if part)
    return "https://www.songsterr.com/?pattern=" + urllib.parse.quote(query)


def find(artist: str, title: str, with_track: bool = True) -> Tab | None:
    """Look the song up. Returns None when Songsterr has nothing for it."""
    query = " ".join(part for part in (artist.strip(), title.strip()) if part)
    songs = _json(f"{API}/songs?pattern=" + urllib.parse.quote(query))
    if not isinstance(songs, list) or not songs:
        return None
    song = songs[0]
    song_id = song.get("songId")
    if not song_id:
        return None
    found_artist = str(song.get("artist") or artist).strip()
    found_title = str(song.get("title") or title).strip()
    tracks = song.get("tracks") or []
    drum = next(
        (index for index, track in enumerate(tracks) if str(track.get("instrument", "")).lower() == "drums"),
        None,
    )
    base = f"https://www.songsterr.com/a/wsa/{slug(found_artist)}-{slug(found_title)}-tab-s{song_id}"
    if drum is None:
        tab = Tab(song_id, found_artist, found_title, base, f"Songsterr tab · {found_artist} — {found_title}")
    else:
        tab = Tab(
            song_id,
            found_artist,
            found_title,
            f"{base}t{drum}",
            f"Songsterr drum tab · {found_artist} — {found_title}",
            drum_track=drum,
        )
    tab.base_url = base
    for index, track in enumerate(tracks):
        try:
            views = int(track.get("views") or 0)
        except (TypeError, ValueError):
            views = 0
        tab.parts.append(Part(index, part_kind(track), part_name(str(track.get("name") or track.get("instrument") or "")), views))
    if with_track:
        try:
            load_track(tab)
        except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError) as exc:
            tab.error = f"Songsterr's tab data could not be read ({exc})."
    return tab


CACHE_DAYS = 7
MISS_DAYS = 1


def find_cached(artist: str, title: str, folder: Path) -> Tab | None:
    """find(), remembered on disk so a song is only looked up again after CACHE_DAYS.

    A song Songsterr does not have is remembered for MISS_DAYS. Failed reads are
    not remembered, so they are retried next time.
    """
    path = folder / f"{slug(artist)}--{slug(title)}.json"
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
        age_days = (time.time() - float(saved["saved"])) / 86400
        if saved.get("tab") is None and age_days < MISS_DAYS:
            return None
        if saved.get("tab") is not None and age_days < CACHE_DAYS:
            return _tab_from(saved["tab"])
    except (OSError, ValueError, KeyError, TypeError):
        pass
    tab = find(artist, title)
    if tab is None or not tab.error:
        try:
            folder.mkdir(parents=True, exist_ok=True)
            data = {"saved": time.time(), "tab": asdict(tab) if tab else None}
            path.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
        except OSError:
            pass
    return tab


def _tab_from(data: dict) -> Tab:
    data = dict(data)
    data["parts"] = [Part(**part) for part in data.get("parts") or []]
    data["sections"] = [tuple(item) for item in data.get("sections") or []]
    data["tempos"] = [tuple(item) for item in data.get("tempos") or []]
    for bar in data.get("bars") or []:
        bar["meter"] = tuple(bar["meter"])
    return Tab(**data)


def load_track(tab: Tab) -> None:
    meta = _json(f"{API}/meta/{tab.song_id}")
    part = tab.drum_track
    if part is None:
        part = meta.get("popularTrackDrum")
    if part is None:
        part = next(
            (index for index, track in enumerate(meta.get("tracks") or []) if track.get("instrumentId") == DRUM_INSTRUMENT),
            None,
        )
    if part is None:
        part = meta.get("defaultTrack", 0)
    base = f"{TRACK_HOST}/{tab.song_id}/{meta['revisionId']}/{meta['image']}"
    track = _json(f"{base}/{part}.json")
    measures = track.get("measures") or []
    is_drums = track.get("instrumentId") == DRUM_INSTRUMENT
    if not any(isinstance(measure.get("marker"), dict) for measure in measures):
        for other in (meta.get("defaultTrack"), meta.get("popularTrack"), 0):
            if other is None or other == part:
                continue
            try:
                donor = _json(f"{base}/{other}.json").get("measures") or []
            except (OSError, ValueError, urllib.error.URLError):
                continue
            if len(donor) == len(measures) and any(isinstance(item.get("marker"), dict) for item in donor):
                for measure, item in zip(measures, donor):
                    if isinstance(item.get("marker"), dict):
                        measure["marker"] = item["marker"]
                break
    bars, tempos = read_measures(measures, (track.get("automations") or {}).get("tempo") or [], is_drums)
    tab.bars = bars
    tab.tempos = tempos
    tab.sections = sections_from(bars)


def _beat_hits(beat: dict) -> int:
    if beat.get("rest"):
        return 0
    return sum(1 for note in beat.get("notes") or [] if not note.get("rest") and not note.get("tie"))


def read_measures(
    measures: list[dict], tempo_marks: list[dict], is_drums: bool
) -> tuple[list[dict], list[tuple[int, float]]]:
    """Play the tab's repeats out so its bars count the way the recording does.

    Returns one dict per played bar: meter, drum hits, marker text, and the
    tab measure it came from. Tempo marks are keyed by played bar.
    """
    meters = measure_meters(measures)
    tempo_by_measure: dict[int, float] = {}
    for mark in tempo_marks:
        try:
            tempo_by_measure[int(mark.get("measure", 0))] = float(mark.get("bpm"))
        except (TypeError, ValueError):
            continue
    order = play_order(measures)

    bars: list[dict] = []
    tempos: list[tuple[int, float]] = []
    seen_tempo: set[int] = set()
    for played, source in enumerate(order, start=1):
        measure = measures[source]
        hits = 0
        if is_drums and not measure.get("rest"):
            for voice in measure.get("voices") or []:
                for beat in voice.get("beats") or []:
                    hits += _beat_hits(beat)
        marker = measure.get("marker") or {}
        text = str(marker.get("text") or "").strip() if isinstance(marker, dict) else ""
        if source in tempo_by_measure and source not in seen_tempo:
            seen_tempo.add(source)
            tempos.append((played, tempo_by_measure[source]))
        bars.append({"meter": meters[source], "hits": hits, "marker": text, "source": source + 1})
    if tempos and tempos[0][0] != 1:
        tempos.insert(0, (1, tempos[0][1]))
    return bars, tempos


def measure_meters(measures: list[dict]) -> list[tuple[int, int]]:
    meters: list[tuple[int, int]] = []
    meter = (4, 4)
    for measure in measures:
        signature = measure.get("signature")
        if isinstance(signature, list) and len(signature) == 2 and signature[1]:
            meter = (int(signature[0]), int(signature[1]))
        meters.append(meter)
    return meters


def play_order(measures: list[dict]) -> list[int]:
    """Tab measure indexes in the order the recording plays them, with repeats played out."""
    order: list[int] = []
    index = 0
    repeat_start = 0
    passes: dict[int, int] = {}
    guard = 0
    while index < len(measures) and guard < 20000:
        guard += 1
        measure = measures[index]
        if measure.get("repeatStart"):
            if passes.get(index) is None:
                repeat_start = index
        ending = measure.get("alternateEnding") or measure.get("alternateEndings")
        current_pass = passes.get(repeat_start, 0) + 1
        if ending:
            allowed = ending if isinstance(ending, list) else [ending]
            try:
                allowed_numbers = {int(item) for item in allowed}
            except (TypeError, ValueError):
                allowed_numbers = set()
            if allowed_numbers and current_pass not in allowed_numbers:
                index += 1
                continue
        order.append(index)
        repeat = measure.get("repeat")
        if isinstance(repeat, int) and repeat > 1:
            done = passes.get(repeat_start, 0) + 1
            if done < repeat:
                passes[repeat_start] = done
                index = repeat_start
                continue
            passes[repeat_start] = 0
            repeat_start = index + 1
        index += 1
    return order


# Guitar Pro drum sounds past the General MIDI kit, moved to the nearest GM drum.
GP_EXTRA_DRUMS = {
    91: 38, 92: 46, 93: 51, 94: 51, 95: 55, 96: 52, 97: 49, 98: 57,
    99: 56, 100: 56, 101: 56, 102: 56, 103: 56, 104: 39, 105: 39,
    106: 39, 107: 39, 108: 39, 109: 39, 126: 53, 127: 49,
}
DYNAMICS = {"ppp": 30, "pp": 45, "p": 60, "mp": 75, "mf": 88, "f": 100, "ff": 112, "fff": 124}


@dataclass
class TabDrums:
    """The tab's drum part as a MIDI timeline at the caller's PPQ."""

    part_name: str
    ticks: list[list[int]]
    bounds: list[int]
    meters: list[tuple[int, int]]
    tempos: list[tuple[int, int]]
    sections: list[tuple[int, int, str]]
    moved: dict[int, int]
    dropped: dict[int, int]


def drum_part(tab: Tab) -> tuple[dict, dict]:
    """Download the drum part load_track() reads. Returns (meta, track)."""
    meta = _json(f"{API}/meta/{tab.song_id}")
    part = tab.drum_track
    if part is None:
        part = meta.get("popularTrackDrum")
    if part is None:
        part = next(
            (index for index, track in enumerate(meta.get("tracks") or []) if track.get("instrumentId") == DRUM_INSTRUMENT),
            None,
        )
    if part is None:
        raise ValueError("Songsterr has no drum part for this song.")
    base = f"{TRACK_HOST}/{tab.song_id}/{meta['revisionId']}/{meta['image']}"
    track = _json(f"{base}/{part}.json")
    if track.get("instrumentId") != DRUM_INSTRUMENT:
        raise ValueError("Songsterr's drum part for this song is not a drum track.")
    return meta, track


def drums_to_midi(track: dict, ppq: int) -> TabDrums:
    """Turn a Songsterr drum part into note, bar, meter, and tempo lists.

    Repeats are played out like read_measures(). Each note's fret is its
    drum number. Grace notes sit just before their beat and take no time
    from the bar. Dynamics carry over from beat to beat; accents are
    louder and ghost notes quieter.
    """
    measures = track.get("measures") or []
    if not measures:
        raise ValueError("Songsterr's drum part has no bars.")
    meters = measure_meters(measures)
    order = play_order(measures)
    whole = 4 * ppq
    tempo_by_measure: dict[int, float] = {}
    for mark in (track.get("automations") or {}).get("tempo") or []:
        try:
            bpm = float(mark.get("bpm"))
            unit = int(mark.get("type") or 4)
            tempo_by_measure[int(mark.get("measure", 0))] = bpm * 4 / unit if unit in (1, 2, 4, 8, 16) else bpm
        except (TypeError, ValueError):
            continue

    ticks: list[list[int]] = []
    bounds: list[int] = [0]
    bar_meters: list[tuple[int, int]] = []
    tempos: list[tuple[int, int]] = []
    moved: dict[int, int] = {}
    dropped: dict[int, int] = {}
    start = Fraction(0)
    bpm = tempo_by_measure[min(tempo_by_measure)] if tempo_by_measure else 120.0
    for played, source in enumerate(order):
        measure = measures[source]
        meter = meters[source]
        length = Fraction(meter[0], meter[1])
        if played == 0 and track.get("anacrusis"):
            filled = max(
                (
                    sum(Fraction(*beat["duration"]) for beat in voice.get("beats") or [] if not beat.get("graceNote"))
                    for voice in measure.get("voices") or []
                ),
                default=length,
            )
            if 0 < filled < length:
                length = filled
        bpm = tempo_by_measure.get(source, bpm)
        tempos.append((round(start * whole), round(60_000_000 / max(bpm, 1))))
        for voice in measure.get("voices") or []:
            position = Fraction(0)
            dynamic = DYNAMICS["f"]
            for beat in voice.get("beats") or []:
                duration = Fraction(*beat.get("duration") or (0, 1))
                if beat.get("velocity") in DYNAMICS:
                    dynamic = DYNAMICS[beat["velocity"]]
                grace = beat.get("graceNote")
                at = position - min(duration, Fraction(1, 32)) if grace == "beforeBeat" else position
                if not grace:
                    position += duration
                if beat.get("rest") or at >= length:
                    continue
                for note in beat.get("notes") or []:
                    if note.get("rest") or note.get("tie") or note.get("dead") or "fret" not in note:
                        continue
                    drum = int(note["fret"])
                    if drum in GP_EXTRA_DRUMS:
                        moved[drum] = moved.get(drum, 0) + 1
                        drum = GP_EXTRA_DRUMS[drum]
                    elif not 27 <= drum <= 87:
                        dropped[drum] = dropped.get(drum, 0) + 1
                        continue
                    velocity = dynamic
                    if note.get("ghost"):
                        velocity = round(velocity * 0.45)
                    elif note.get("accentuated"):
                        velocity += 27 if note["accentuated"] == 2 else 18
                    if grace:
                        velocity = round(velocity * 0.7)
                    ticks.append([max(0, round((start + at) * whole)), drum, max(1, min(127, velocity))])
        start += length
        bounds.append(round(start * whole))
        bar_meters.append(meter)
    ticks.sort()
    bars, _tempos = read_measures(measures, [], True)
    return TabDrums(
        part_name=part_name(str(track.get("name") or "Drums")),
        ticks=ticks,
        bounds=bounds,
        meters=bar_meters,
        tempos=tempos,
        sections=sections_from(bars),
        moved=moved,
        dropped=dropped,
    )


def sections_from(bars: list[dict]) -> list[tuple[int, int, str]]:
    sections: list[tuple[int, int, str]] = []
    for index, bar in enumerate(bars, start=1):
        if bar["marker"]:
            if sections:
                start, _end, name = sections[-1]
                sections[-1] = (start, index - 1, name)
            sections.append((index, len(bars), clean_name(bar["marker"])))
    if sections and sections[0][0] > 1:
        sections.insert(0, (1, sections[0][0] - 1, "Intro"))
    return sections


def clean_name(text: str) -> str:
    text = re.sub(r"^\s*\[[A-Za-z0-9]{1,3}\]\s*", "", text)
    text = text.strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    return " ".join(text.split()) or "Part"


def kind(name: str) -> str:
    text = name.lower()
    if re.search(r"pre[\s-]?chorus", text):
        return "prechorus"
    if "chorus" in text and not text.startswith(("ending", "outro")):
        return "chorus"
    if any(word in text for word in ("outro", "ending", "coda", "fade")):
        return "outro"
    if "bridge" in text or "middle 8" in text or "middle eight" in text:
        return "bridge"
    if "intro" in text:
        return "intro"
    if "verse" in text:
        return "verse"
    if "solo" in text:
        return "solo"
    return "other"


SURE = 0.6


def align(tab_hits: list[int], midi_hits: list[int], max_shift: int = MAX_SHIFT) -> tuple[int, float, bool]:
    """Find how many bars to move the tab so its drums match the MIDI's.

    Returns (shift, score, sure). MIDI bar = tab bar + shift. When the best
    match is weak, the shift stays 0 and sure is False.
    """
    if not tab_hits or not midi_hits or not any(tab_hits) or not any(midi_hits):
        return 0, 0.0, False
    best = (0, -2.0)
    for shift in range(-max_shift, max_shift + 1):
        score = shift_score(tab_hits, midi_hits, shift)
        if score is None:
            continue
        score -= abs(shift) * 0.002
        if score > best[1] + 1e-9:
            best = (shift, score)
    shift, score = best
    score = max(0.0, score)
    if score < SURE:
        zero = shift_score(tab_hits, midi_hits, 0) or 0.0
        return 0, max(0.0, zero), False
    return shift, score, True


def shift_score(tab_hits: list[int], midi_hits: list[int], shift: int) -> float | None:
    tab_peak = max(tab_hits) or 1
    midi_peak = max(midi_hits) or 1
    indexes = [index for index in range(len(tab_hits)) if 0 <= index + shift < len(midi_hits)]
    if len(indexes) < max(4, min(len(tab_hits), len(midi_hits)) // 2):
        return None
    pairs = [(tab_hits[index] / tab_peak, midi_hits[index + shift] / midi_peak) for index in indexes]
    close = sum(
        1
        for index in indexes
        if abs(tab_hits[index] - midi_hits[index + shift]) <= max(1, 0.1 * max(tab_hits[index], midi_hits[index + shift]))
    ) / len(indexes)
    return 0.6 * _similarity(pairs) + 0.4 * close


def _similarity(pairs: list[tuple[float, float]]) -> float:
    count = len(pairs)
    left = [a for a, _b in pairs]
    right = [b for _a, b in pairs]
    mean_left = sum(left) / count
    mean_right = sum(right) / count
    top = sum((a - mean_left) * (b - mean_right) for a, b in pairs)
    spread_left = sum((a - mean_left) ** 2 for a in left) ** 0.5
    spread_right = sum((b - mean_right) ** 2 for b in right) ** 0.5
    correlation = top / (spread_left * spread_right) if spread_left and spread_right else 0.0
    silence = sum(1 for a, b in pairs if (a == 0) == (b == 0)) / count
    return 0.6 * correlation + 0.4 * silence


def fit(tab_hits: list[int], midi_hits: list[int]) -> tuple[int, float, bool, bool]:
    """Line the tab up with the MIDI. Returns (shift, score, sure, half_time).

    A MIDI written in half time has one bar for every two tab bars, so the
    tab is folded in pairs before matching when the counts are about 2 to 1.
    """
    shift, score, sure = align(tab_hits, midi_hits)
    if tab_hits and midi_hits and 1.7 <= len(tab_hits) / len(midi_hits) <= 2.3:
        folded = [sum(tab_hits[index : index + 2]) for index in range(0, len(tab_hits), 2)]
        half_shift, half_score, half_sure = align(folded, midi_hits)
        if not sure or half_score > score + 0.05:
            return half_shift, half_score, half_sure, True
    return shift, score, sure, False


def place(
    sections: list[tuple[int, int, str]], shift: int, count: int, half: bool = False
) -> list[tuple[int, int, str]]:
    """Move tab sections onto MIDI bars and drop the ones that fall off the file."""
    placed = []
    for start, end, name in sections:
        if half:
            start = (start - 1) // 2 + 1
            end = (end + 1) // 2
        left = max(1, start + shift)
        right = min(count, end + shift)
        if right < left:
            continue
        placed.append((left, right, name))
    return placed


def divisions_from_sections(sections: list[tuple[int, int, str]], count: int) -> tuple[dict[str, list[int]], list[str]]:
    """Pedal ranges from named sections already placed on MIDI bars.

    Intro is the opening through the bar before the first chorus. Pattern 1
    is that chorus. Pattern 2 is the first bridge after it, or the next
    chorus, or the next different section. The ending is the outro through
    the last bar. Fills are the bar before Pattern 1 and the last bar of
    Pattern 2.
    """
    notes: list[str] = []
    if count < 1 or not sections:
        raise ValueError("Songsterr has no sections for this song.")
    kinds = [kind(name) for _start, _end, name in sections]
    first = next((index for index, item in enumerate(kinds) if item == "chorus"), None)
    if first is None:
        first = next((index for index, item in enumerate(kinds) if item not in ("intro",)), 0)
        notes.append(f"No chorus is marked, so Pattern 1 is {sections[first][2]}.")
    p1_start, p1_end, p1_name = sections[first]
    second = next((index for index in range(first + 1, len(sections)) if kinds[index] == "bridge"), None)
    if second is None:
        second = next((index for index in range(first + 1, len(sections)) if kinds[index] == "chorus"), None)
        if second is not None:
            notes.append(f"No bridge is marked, so Pattern 2 is {sections[second][2]}.")
    if second is None:
        second = next(
            (
                index
                for index in range(first + 1, len(sections))
                if kinds[index] not in ("outro",) and sections[index][:2] != (p1_start, p1_end)
            ),
            None,
        )
        if second is not None:
            notes.append(f"No bridge or second chorus is marked, so Pattern 2 is {sections[second][2]}.")
    if second is None:
        second = first
        notes.append("Only one section can loop, so both patterns use it.")
    p2_start, p2_end, p2_name = sections[second]
    ending_index = next(
        (index for index in range(len(sections) - 1, first, -1) if kinds[index] == "outro"),
        None,
    )
    if ending_index is not None:
        while ending_index - 1 > max(first, second) and kinds[ending_index - 1] == "outro":
            ending_index -= 1
        end_start = sections[ending_index][0]
    else:
        end_start = sections[-1][0]
        notes.append(f"No outro is marked, so the ending is the last section, {sections[-1][2]}.")
    end_start = min(max(1, end_start), count)
    intro_end = max(1, p1_start - 1)
    divisions = {
        "intro": [1, intro_end],
        "pattern1": [p1_start, p1_end],
        "pattern1Fill": [max(1, p1_start - 1)] * 2,
        "pattern2": [p2_start, p2_end],
        "pattern2Fill": [p2_end, p2_end],
        "ending": [end_start, count],
    }
    for key, (start, end) in divisions.items():
        start = min(max(1, start), count)
        end = min(max(start, end), count)
        divisions[key] = [start, end]
    notes.insert(0, f"Pattern 1 is {p1_name}. Pattern 2 is {p2_name}.")
    return divisions, notes
