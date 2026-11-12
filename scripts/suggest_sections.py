"""Suggest loopable RC-10R sections from a drum kit.

Pattern 1 is one loop of the densest groove that carries the song.
Pattern 2 is a later section, never the same bars as Pattern 1.
Intro is the opening before that first loop. The ending is the closing
phrase. Each fill is one bar.

The numbers are what you type into Rhythm Converter. This does not
rewrite the MIDI file.
"""

from __future__ import annotations

from dataclasses import dataclass

KICK = {35, 36}
SNARE = {38, 40}
HAT = {42, 44, 46}
TOM = {41, 43, 45, 47, 48, 50}
PHRASES = (16, 12, 8, 4)


@dataclass
class Bar:
    count: int
    kick: tuple[int, ...]
    snare: tuple[int, ...]
    hat: tuple[int, ...]
    toms: int


@dataclass
class Suggestion:
    divisions: dict[str, list[int]]
    summary: str


def _slots(ticks: list[int], start: int, span: int) -> tuple[int, ...]:
    return tuple(sorted({min(7, int((tick - start) / span * 8)) for tick in ticks}))


def _features(events: list[tuple[int, int, int]], boundaries: list[int]) -> list[Bar]:
    bars: list[Bar] = []
    index = 0
    for start, end in zip(boundaries, boundaries[1:]):
        span = max(1, end - start)
        kicks: list[int] = []
        snares: list[int] = []
        hats: list[int] = []
        toms = 0
        count = 0
        while index < len(events) and events[index][0] < end:
            tick, note, _velocity = events[index]
            index += 1
            if tick < start:
                continue
            count += 1
            if note in KICK:
                kicks.append(tick)
            elif note in SNARE:
                snares.append(tick)
            elif note in HAT:
                hats.append(tick)
            elif note in TOM:
                toms += 1
        bars.append(Bar(count, _slots(kicks, start, span), _slots(snares, start, span), _slots(hats, start, span), toms))
    return bars


def _jaccard(left: tuple[int, ...], right: tuple[int, ...]) -> float:
    a, b = set(left), set(right)
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _alike(left: Bar, right: Bar) -> float:
    if left.count == 0 and right.count == 0:
        return 1.0
    if left.count == 0 or right.count == 0:
        return 0.0
    kick = _jaccard(left.kick, right.kick)
    snare = _jaccard(left.snare, right.snare)
    if left.kick or left.snare or right.kick or right.snare:
        return 0.55 * kick + 0.45 * snare
    return _jaccard(left.hat, right.hat)


def _families(bars: list[Bar]) -> list[int]:
    prototypes: list[Bar] = []
    labels: list[int] = []
    for bar in bars:
        if bar.count == 0:
            labels.append(-1)
            continue
        best = -1
        score = 0.5
        for index, prototype in enumerate(prototypes):
            likeness = _alike(bar, prototype)
            if likeness >= score:
                best = index
                score = likeness
        if best < 0:
            prototypes.append(bar)
            best = len(prototypes) - 1
        labels.append(best)
    return labels


def _smooth(labels: list[int]) -> list[int]:
    """A one-bar fill between two bars of the same groove stays in that groove."""
    smoothed = labels[:]
    for index in range(1, len(smoothed) - 1):
        around = smoothed[index - 1]
        if around >= 0 and around == smoothed[index + 1] and smoothed[index] != around:
            smoothed[index] = around
    return smoothed


def _runs(labels: list[int]) -> list[tuple[int, int, int]]:
    runs: list[tuple[int, int, int]] = []
    index = 0
    while index < len(labels):
        end = index
        while end < len(labels) and labels[end] == labels[index]:
            end += 1
        runs.append((labels[index], index + 1, end))
        index = end
    return runs


def _phrase(length: int) -> int:
    if length <= 16:
        return max(1, length)
    for size in PHRASES:
        if length >= size:
            return size
    return 16


def _median(values: list[int]) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def suggest(events: list[tuple[int, int, int]], boundaries: list[int]) -> Suggestion:
    """Return intro, two patterns, two fills, and an ending. Bars start at 1."""
    count = max(0, len(boundaries) - 1)
    if count <= 0:
        empty = {key: [1, 1] for key in ("intro", "pattern1", "pattern1Fill", "pattern2", "pattern2Fill", "ending")}
        return Suggestion(empty, "This file has no measures.")
    bars = _features(events, boundaries)
    labels = _smooth(_families(bars))
    grouped = _runs(labels)
    by_family: dict[int, list[tuple[int, int]]] = {}
    for family, start, end in grouped:
        if family < 0:
            continue
        by_family.setdefault(family, []).append((start, end))

    def weight(family: int) -> float:
        spans = by_family[family]
        played = [bars[bar - 1].count for start, end in spans for bar in range(start, end + 1)]
        total = sum(end - start + 1 for start, end in spans)
        density = _median(played) or 1
        returning = 1.2 if len(spans) > 1 else 1.0
        return (density ** 1.4) * (total ** 0.65) * returning

    ranked = sorted(by_family, key=weight, reverse=True)
    if not ranked:
        return _short(count, "The kit is silent, so the whole file is the intro and the ending.")

    early = [family for family in ranked if by_family[family][0][0] <= max(8, int(count * 0.5))]
    main = early[0] if early else ranked[0]
    usable = [span for span in by_family[main] if span[1] - span[0] + 1 >= 4] or by_family[main]
    first = usable[0]
    loop = _phrase(first[1] - first[0] + 1)
    pattern_start = first[0]
    if pattern_start <= 4 and first[1] - first[0] + 1 >= loop * 2:
        pattern_start = first[0] + loop
    pattern_end = min(count, pattern_start + loop - 1)
    if pattern_end < pattern_start:
        pattern_start, pattern_end = 1, min(count, loop)

    intro_end = max(1, pattern_start - 1)
    if pattern_start == 1:
        intro_end = 1
        if count > 1:
            pattern_start = 2
            pattern_end = min(count, pattern_start + loop - 1)

    ending_start = _ending_start(labels, bars, pattern_end, count)
    pattern2_start, pattern2_end, same = _pattern2(
        by_family, bars, ranked, main, pattern_start, pattern_end, ending_start, count
    )
    if pattern2_end >= ending_start and not same:
        trimmed = ending_start - 1
        if trimmed - pattern2_start + 1 >= 2 and [pattern2_start, trimmed] != [pattern_start, pattern_end]:
            pattern2_end = trimmed
        else:
            pattern2_start, pattern2_end, same = _later(pattern_start, pattern_end, ending_start, count)
    if same:
        pattern2_start, pattern2_end, same = _later(pattern_start, pattern_end, ending_start, count)

    fill1 = pattern_start - 1 if pattern_start > 1 and bars[pattern_start - 2].count else pattern_end
    fill2 = pattern2_end
    divisions = {
        "intro": [1, intro_end],
        "pattern1": [pattern_start, pattern_end],
        "pattern1Fill": [max(1, min(count, fill1)), max(1, min(count, fill1))],
        "pattern2": [pattern2_start, pattern2_end],
        "pattern2Fill": [max(1, min(count, fill2)), max(1, min(count, fill2))],
        "ending": [ending_start, count],
    }
    if same:
        summary = (
            "Suggested from the drum groove. This file is only one bar, so the two patterns share it. "
            "Type these into Rhythm Converter. Intro and the ending play once. Patterns and fills loop."
        )
    else:
        summary = (
            "Suggested from the drum groove. Pattern 1 is one loop of the main groove. "
            "Pattern 2 is a later section, on different bars. "
            "Type these into Rhythm Converter. Intro and the ending play once. Patterns and fills loop."
        )
    return Suggestion(divisions, summary)


def _ending_start(labels: list[int], bars: list[Bar], pattern_end: int, count: int) -> int:
    if count <= 4:
        return count
    tail = min(16, max(4, count // 8))
    start = count - tail + 1
    closing = labels[count - 1]
    if closing >= 0:
        while start > 1 and labels[start - 2] == closing and count - (start - 1) < 16:
            earlier = start - 1
            if earlier <= pattern_end:
                break
            start = earlier
    body = [bar.count for bar in bars[: max(1, count - 4)] if bar.count]
    typical = _median(body) or 1
    while start < count and bars[start - 1].count >= typical:
        start += 1
    if count - start + 1 < 1:
        start = count
    if start <= pattern_end:
        start = min(count, pattern_end + 1)
    return max(1, start)


def _pattern2(
    by_family: dict[int, list[tuple[int, int]]],
    bars: list[Bar],
    ranked: list[int],
    main: int,
    pattern_start: int,
    pattern_end: int,
    ending_start: int,
    count: int,
) -> tuple[int, int, bool]:
    main_proto = bars[by_family[main][0][0] - 1]
    best: tuple[int, int] | None = None
    best_weight = -1.0
    for family in ranked[1:]:
        prototype = bars[by_family[family][0][0] - 1]
        if _alike(main_proto, prototype) >= 0.78:
            continue
        for start, end in by_family[family]:
            if end < pattern_end or start >= ending_start:
                continue
            length = min(end, ending_start - 1) - max(start, pattern_end) + 1
            if length < 4 and end - start + 1 < 4:
                continue
            use_start = start if start > pattern_end else pattern_end + 1
            use_end = min(end, ending_start - 1)
            if use_end - use_start + 1 < 2:
                continue
            phrase = _phrase(use_end - use_start + 1)
            candidate = (use_start, min(use_end, use_start + phrase - 1))
            weight = candidate[1] - candidate[0] + 1
            if weight > best_weight:
                best = candidate
                best_weight = weight
    if best is None:
        return _later(pattern_start, pattern_end, ending_start, count)
    return best[0], best[1], False


def _later(pattern_start: int, pattern_end: int, ending_start: int, count: int) -> tuple[int, int, bool]:
    """A later phrase. Pattern 2 is not allowed to copy Pattern 1."""
    length = max(2, min(16, pattern_end - pattern_start + 1))
    start = pattern_end + 1
    room = ending_start - 1
    if room - start + 1 >= 2:
        return start, min(room, start + length - 1), False
    if count - start + 1 >= 2:
        return start, min(count, start + length - 1), False
    if pattern_start > 2:
        end = pattern_start - 1
        earlier = max(1, end - length + 1)
        if [earlier, end] != [pattern_start, pattern_end]:
            return earlier, end, False
    return pattern_start, pattern_end, count <= 1


def _coalesce(runs: list[tuple[int, int, int]]) -> list[tuple[int, int, int]]:
    """Fold one- and two-bar fills into the section before them."""
    kept: list[tuple[int, int, int]] = []
    for family, start, end in runs:
        if kept and end - start + 1 < 4:
            previous, prev_start, _prev_end = kept[-1]
            kept[-1] = (previous, prev_start, end)
            continue
        if kept and kept[-1][0] == family:
            kept[-1] = (family, kept[-1][1], end)
            continue
        kept.append((family, start, end))
    if len(kept) >= 2 and kept[0][2] - kept[0][1] + 1 < 4:
        family, _start, end = kept[1]
        kept[1] = (family, kept[0][1], end)
        kept.pop(0)
    return kept


def song_parts(events: list[tuple[int, int, int]], boundaries: list[int]) -> list[tuple[int, int, str]]:
    """Name the verses, choruses, solo, and other parts the drum groove suggests.

    These sit above the intro and pattern ranges. They are labels for the
    chart, not a second set of pedal assignments.
    """
    count = max(0, len(boundaries) - 1)
    if count <= 0:
        return []
    bars = _features(events, boundaries)
    sections = _coalesce(_runs(_smooth(_families(bars))))
    totals: dict[int, int] = {}
    for family, start, end in sections:
        if family < 0:
            continue
        totals[family] = totals.get(family, 0) + end - start + 1
    big = [family for family, total in totals.items() if total >= 8]
    if len(big) < 2:
        return _phrase_parts(count)
    chorus = max(big, key=lambda family: totals[family])
    first_chorus = min(start for family, start, _end in sections if family == chorus)
    verse = next(
        (
            family
            for family in big
            if family != chorus and any(start < first_chorus for fam, start, _end in sections if fam == family)
        ),
        None,
    )
    names: list[tuple[int, int, str]] = []
    verse_n = chorus_n = bridge_n = 0
    solo_used = False
    for index, (family, start, end) in enumerate(sections):
        length = end - start + 1
        last = index == len(sections) - 1
        if last and length <= 8 and family != chorus:
            name = "Outro"
        elif family < 0:
            name = "Intro" if start <= 2 else "Break"
        elif index == 0 and family != chorus and length <= 16:
            name = "Intro"
        elif family == chorus:
            chorus_n += 1
            name = f"Chorus {chorus_n}"
        elif family == verse:
            verse_n += 1
            name = f"Verse {verse_n}"
        elif not solo_used and length >= 8 and count * 0.15 < start < count * 0.9:
            solo_used = True
            name = "Guitar solo"
        else:
            bridge_n += 1
            name = "Bridge" if bridge_n == 1 else f"Bridge {bridge_n}"
        names.append((start, end, name))
    return names


def _phrase_parts(count: int) -> list[tuple[int, int, str]]:
    """A single drum groove has no verse-versus-chorus change, so use rock phrases."""
    phrase = 16 if count >= 48 else 8
    slots: list[tuple[int, int]] = []
    start = 1
    while start <= count:
        end = min(count, start + phrase - 1)
        slots.append((start, end))
        if end == count:
            break
        start = end + 1
    amount = len(slots)
    if amount <= 3:
        plan = ["Verse 1", "Chorus 1", "Outro"][:amount]
    elif amount == 4:
        plan = ["Verse 1", "Chorus 1", "Guitar solo", "Outro"]
    elif amount == 5:
        plan = ["Verse 1", "Chorus 1", "Verse 2", "Guitar solo", "Outro"]
    else:
        plan = ["Verse 1", "Chorus 1", "Verse 2", "Chorus 2", "Guitar solo"]
        chorus_n = 3
        while len(plan) < amount - 1:
            plan.append(f"Chorus {chorus_n}")
            chorus_n += 1
        plan.append("Outro")
    return [(start, end, plan[index]) for index, (start, end) in enumerate(slots)]


def _short(count: int, summary: str) -> Suggestion:
    end = max(1, count)
    if end == 1:
        first, second, fill_a, fill_b = [1, 1], [1, 1], [1, 1], [1, 1]
    else:
        split = max(1, end // 2)
        first, second = [1, split], [split + 1, end]
        fill_a, fill_b = [split, split], [end, end]
    divisions = {
        "intro": [1, 1],
        "pattern1": first,
        "pattern1Fill": fill_a,
        "pattern2": second,
        "pattern2Fill": fill_b,
        "ending": [end, end],
    }
    return Suggestion(divisions, summary)


def recommend_kit(midi: dict, artist: str, title: str, meters: list[tuple[int, int, int]]) -> tuple[str, str]:
    """The RC-10R kit to pick in Rhythm Converter, and why."""
    named = _kit_from_file(midi)
    if named is not None:
        return named
    return _kit_from_song(midi, artist, title, meters)


def _kit_from_file(midi: dict) -> tuple[str, str] | None:
    programs = [
        program
        for track in midi.get("tracks", [])
        for channel, program in track.get("programs", [])
        if channel == 9
    ]
    if programs:
        label, kit = _PROGRAMS.get(programs[-1], ("", ""))
        if kit:
            return kit, f"The file selects a {label} kit. In Rhythm Converter that is {kit}."
    words = " ".join(
        part
        for track in midi.get("tracks", [])
        for part in [track.get("name", ""), *track.get("instruments", [])]
        if part
    ).lower()
    for keys, kit, label in _NAMED_KITS:
        if any(key in words for key in keys):
            return kit, f"The file names a {label}. In Rhythm Converter that is {kit}."
    return None


def _kit_from_song(midi: dict, artist: str, title: str, meters: list[tuple[int, int, int]]) -> tuple[str, str]:
    text = f"{artist} {title}".lower()
    notes = {note for _tick, note, _velocity in midi.get("events", [])}
    standard = any(
        program == 0
        for track in midi.get("tracks", [])
        for channel, program in track.get("programs", [])
        if channel == 9
    )
    prefix = "The file uses the standard drum kit. " if standard else ""
    if any(word in text for word in ("metal", "djent", "thrash")) or "system of a down" in text:
        return "Metal", prefix + "This is a metal song, so use Metal."
    if any(word in text for word in ("808", "909", "trap")):
        return "808+909", prefix + "The song calls for an 808 or 909 kit."
    if "techno" in text:
        return "Techno", prefix + "This is a techno track, so use Techno."
    if any(word in text for word in ("drum and bass", "drum & bass", "dnb")):
        return "Drum&Bs", prefix + "This is drum and bass, so use Drum&Bs."
    if any(word in text for word in ("hip hop", "hip-hop", "hiphop")):
        return "Hiphop", prefix + "This is hip-hop, so use Hiphop."
    if any(word in text for word in ("r&b", "rnb", "motown")):
        return "R&B", prefix + "This is R&B, so use R&B."
    if "brush" in text:
        return "Brushes", prefix + "The song asks for brushes."
    if any(word in text for word in ("cajon", "unplugged")):
        return "Cajon", prefix + "This is an acoustic or cajon song, so use Cajon."
    if any(word in text for word in ("jazz", "swing", "bebop", "bossa")):
        return "Jazz", prefix + "This is a jazz song, so use Jazz."
    if notes & {62, 63, 64} and not (notes & {35, 36, 38, 40}):
        return "Cajon", prefix + "The file is hand percussion, so use Cajon."
    if 37 in notes and not (notes & {36, 38, 40}):
        return "Jazz", prefix + "The file is mostly side stick, so use Jazz."
    if any(word in text for word in ("punk", "rock", "halen", "zeppelin", "ac/dc", "acdc", "offspring")):
        return "Rock", prefix + "This is a rock song, so use Rock."
    if any(numerator in (6, 12) and denominator == 8 for _bar, numerator, denominator in meters):
        return "Rock", prefix + "The groove is a rock feel in a compound meter, so use Rock."
    if notes & {35, 36, 38, 40}:
        return "Rock", prefix + "The kit is a backbeat, so use Rock."
    return "Studio", prefix + "Nothing names a special kit, so start with Studio."


_PROGRAMS = {
    8: ("Room", "Live"),
    16: ("Power", "Rock"),
    17: ("Power", "Rock"),
    24: ("Electronic", "Dance"),
    25: ("TR-808", "808+909"),
    26: ("Dance", "Dance"),
    27: ("Jazz", "Jazz"),
    30: ("TR-909", "808+909"),
    32: ("Jazz", "Jazz"),
    40: ("Brush", "Brushes"),
    48: ("Orchestra", "Studio"),
}

_NAMED_KITS = (
    (("tr-808", "tr808", "808"), "808+909", "TR-808 kit"),
    (("tr-909", "tr909", "909"), "808+909", "TR-909 kit"),
    (("brush",), "Brushes", "brush kit"),
    (("cajon",), "Cajon", "cajon kit"),
    (("jazz",), "Jazz", "jazz kit"),
    (("metal",), "Metal", "metal kit"),
    (("techno",), "Techno", "techno kit"),
    (("hip hop", "hip-hop", "hiphop"), "Hiphop", "hip-hop kit"),
    (("drum & bass", "drum and bass"), "Drum&Bs", "drum and bass kit"),
    (("r&b", "rnb"), "R&B", "R&B kit"),
    (("electronic",), "Dance", "electronic kit"),
    (("power",), "Rock", "power kit"),
    (("room",), "Live", "room kit"),
)
