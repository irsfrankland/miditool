"""Read a song's sections, tempo, and meter from its Songsterr tab.

Songsterr supplies names, bar counts, tempo, and meter only. The drum
notes in its tab are used to line its bars up with the MIDI's bars, not
to replace the MIDI.
"""

from __future__ import annotations

import gzip
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

BROWSER = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
API = "https://www.songsterr.com/api"
TRACK_HOST = "https://dqsljvtekg760.cloudfront.net"
DRUM_INSTRUMENT = 1024
MAX_SHIFT = 24


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

    @property
    def count(self) -> int:
        return len(self.bars)

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
    if with_track:
        try:
            load_track(tab)
        except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError) as exc:
            tab.error = f"Songsterr's tab data could not be read ({exc})."
    return tab


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
    meters: list[tuple[int, int]] = []
    meter = (4, 4)
    for measure in measures:
        signature = measure.get("signature")
        if isinstance(signature, list) and len(signature) == 2 and signature[1]:
            meter = (int(signature[0]), int(signature[1]))
        meters.append(meter)
    tempo_by_measure: dict[int, float] = {}
    for mark in tempo_marks:
        try:
            tempo_by_measure[int(mark.get("measure", 0))] = float(mark.get("bpm"))
        except (TypeError, ValueError):
            continue

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
