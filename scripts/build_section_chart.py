"""Build a labeled measure chart for the eight ready RC-10R songs.

Reads songs/ready.json and the drum MIDIs it names, counts kit hits per file
measure, and writes docs/charts/index.html. Ranges are taken from the map.
This script does not rewrite MIDI files.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
MAP_PATH = ROOT / "songs" / "ready.json"
OUT_PATH = ROOT / "docs" / "charts" / "index.html"

DIVISION_ORDER = (
    ("intro", "Intro"),
    ("pattern1", "Pattern 1"),
    ("pattern1Fill", "Pattern 1 fill"),
    ("pattern2", "Pattern 2"),
    ("pattern2Fill", "Pattern 2 fill"),
    ("ending", "Ending"),
)

STORED_KEYS = ("intro", "pattern1", "pattern2", "ending")

COLORS = {
    "intro": "#1f7a4d",
    "pattern1": "#1d5c99",
    "pattern2": "#c46b12",
    "ending": "#6d3d86",
    "unstored": "#d9d4cc",
}

# Start measure and length. Length is last bar - first bar + 1.
# A mismatch with songs/ready.json fails the build.
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
        "pattern1": (18, 56),
        "pattern1Fill": (17, 1),
        "pattern2": (18, 56),
        "pattern2Fill": (73, 1),
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
) -> tuple[list[tuple[int, int]], list[tuple[int, int]], list[tuple[int, int, int]], int, str]:
    index = 0
    tick = 0
    running = None
    notes: list[tuple[int, int]] = []
    tempos: list[tuple[int, int]] = []
    signatures: list[tuple[int, int, int]] = []
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
                index += 1
            elif kind in (0x80, 0x90, 0xA0, 0xB0, 0xE0):
                velocity = data[index + 1]
                index += 2
                if kind == 0x90 and velocity > 0:
                    notes.append((tick, status & 0x0F))
            else:
                raise ValueError(f"unsupported status {status:#x} at tick {tick}")

    return notes, tempos, signatures, end_tick, name


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
        track_notes, track_tempos, track_sigs, track_end, track_name = parse_track(track)
        tracks.append({"name": track_name, "notes": track_notes})
        tempos.extend(track_tempos)
        signatures.extend(track_sigs)
        end_tick = max(end_tick, track_end)

    drum_ticks = drum_note_ticks(tracks)
    if drum_ticks:
        end_tick = max(drum_ticks) + 1

    return {
        "ppq": ppq,
        "notes": drum_ticks,
        "tempos": tempos,
        "signatures": signatures,
        "end_tick": end_tick,
    }


def is_kit_track(name: str) -> bool:
    text = " ".join(name.lower().split())
    if "perc" in text or "steel" in text:
        return False
    return "drum" in text


def drum_note_ticks(tracks: list[dict]) -> list[int]:
    """Hits from the kit track, or channel 10 when the file has no kit track.

    These MIDIs are full arrangements. Guitar and bass notes are not the
    drum part. A separate percussion track, such as Ramble On's congas,
    stays out of the kit so acoustic sections read as empty.
    """
    kit_tracks = [track for track in tracks if is_kit_track(track["name"])]
    if kit_tracks:
        return [tick for track in kit_tracks for tick, _channel in track["notes"]]
    return [tick for track in tracks for tick, channel in track["notes"] if channel == 9]


def build_measures(midi: dict) -> tuple[list[int], list[tuple[int, int, int]]]:
    """Return hit counts per measure and each meter change as (bar, num, den)."""
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

    return hits, meters


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
    expected = EXPECTED[song["id"]]
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


def attr(value: str) -> str:
    return escape(value, {'"': "&quot;"})


def render_chart(song: dict, hits: list[int], roles: list[str]) -> str:
    step = 10
    bar_w = 8
    pad_l = 46
    pad_r = 140
    fill_label_h = 36
    div_label_h = 56
    plot_h = 150
    axis_h = 36
    width = pad_l + max(len(hits), 1) * step + pad_r
    height = fill_label_h + div_label_h + plot_h + axis_h
    plot_top = fill_label_h + div_label_h
    plot_bottom = plot_top + plot_h
    peak = max(hits) if hits else 1
    peak = max(peak, 1)

    def bar_left(bar: int) -> float:
        return pad_l + (bar - 1) * step

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-labelledby="{attr(song["id"])}-title">',
        f'<title id="{attr(song["id"])}-title">{attr(song["title"])} measure chart</title>',
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="#fff"/>',
        f'<text x="8" y="{plot_top + 12}" fill="#5c564c" font-size="11" font-family="Segoe UI, sans-serif">{peak}</text>',
        f'<text x="8" y="{plot_bottom}" fill="#5c564c" font-size="11" font-family="Segoe UI, sans-serif">0</text>',
        f'<line x1="{pad_l}" y1="{plot_bottom}" x2="{width - pad_r}" y2="{plot_bottom}" stroke="#cfc6b8" stroke-width="1"/>',
    ]

    divisions = song["divisions"]
    names = dict(DIVISION_ORDER)
    fill_labels: dict[int, list[str]] = {}
    for key, label in (("pattern1Fill", "Pattern 1 fill"), ("pattern2Fill", "Pattern 2 fill")):
        bar = divisions[key][0]
        home = "pattern1" if key == "pattern1Fill" else "pattern2"
        if 1 <= bar <= len(hits):
            parent = roles[bar - 1]
            if parent == home:
                label = f"{label} · bar {bar} · inside {names[home]}"
            elif parent == "unstored":
                label = f"{label} · bar {bar} · not on {names[home]}"
            else:
                label = f"{label} · bar {bar} · on {names[parent]}"
        fill_labels.setdefault(bar, []).append(label)

    for bar, count in enumerate(hits, start=1):
        role = roles[bar - 1]
        column_h = 3 if count == 0 else max(4, round(count / peak * (plot_h - 16)))
        x = bar_left(bar)
        y = plot_bottom - column_h
        tip_role = role if role != "unstored" else "not stored"
        fill_name = " and ".join(fill_labels.get(bar, []))
        hit_word = "hit" if count == 1 else "hits"
        tip = f"Bar {bar} · {tip_role} · {count} {hit_word}"
        if fill_name:
            tip += f" · {fill_name}"
        stroke = ' stroke="#161513" stroke-width="2"' if bar in fill_labels else ""
        parts.append(
            f'<rect x="{x}" y="{y}" width="{bar_w}" height="{column_h}" rx="1" '
            f'fill="{COLORS[role]}"{stroke}><title>{attr(tip)}</title></rect>'
        )

    same_pattern = divisions["pattern1"] == divisions["pattern2"]
    region_labels: list[tuple[str, float, float, float]] = []
    for role, start, end in role_spans(roles):
        if role == "unstored":
            bars = end - start + 1
            if bars < 8:
                continue
            x = bar_left(start)
            span_w = (end - start + 1) * step
            parts.append(
                f'<text x="{x + span_w / 2}" y="{fill_label_h + 32}" text-anchor="middle" '
                f'fill="#8a8378" font-size="12" font-family="Segoe UI, sans-serif">not stored</text>'
            )
            continue
        label = names[role]
        length = end - start + 1
        text = f"{label} · {start} × {length}"
        x1 = bar_left(start)
        x2 = bar_left(end) + bar_w
        text_width = len(text) * 7.0
        bracket_y = plot_top - 8
        parts.append(
            f'<line x1="{x1}" y1="{bracket_y}" x2="{x2}" y2="{bracket_y}" stroke="{COLORS[role]}" stroke-width="2"/>'
        )
        parts.append(f'<line x1="{x1}" y1="{bracket_y - 5}" x2="{x1}" y2="{bracket_y}" stroke="{COLORS[role]}" stroke-width="2"/>')
        parts.append(f'<line x1="{x2}" y1="{bracket_y - 5}" x2="{x2}" y2="{bracket_y}" stroke="{COLORS[role]}" stroke-width="2"/>')
        region_labels.append((text, x1, x2, text_width))

    occupied: list[tuple[float, float, int]] = []
    for index, (text, x1, x2, text_width) in enumerate(region_labels):
        left_limit = region_labels[index - 1][2] if index else pad_l
        right_limit = region_labels[index + 1][1] if index + 1 < len(region_labels) else width - 8
        if text_width <= (x2 - x1):
            anchor = "middle"
            tx = (x1 + x2) / 2
            left = tx - text_width / 2
        else:
            room_left = x1 - left_limit
            room_right = right_limit - x2
            if room_right >= room_left and x1 + text_width <= right_limit:
                anchor = "start"
                tx = x1
                left = tx
            elif x2 - text_width >= left_limit:
                anchor = "end"
                tx = x2
                left = tx - text_width
            else:
                anchor = "start"
                tx = max(pad_l, min(x1, width - 8 - text_width))
                left = tx
        right = left + text_width
        row = 0
        for other_left, other_right, other_row in occupied:
            if other_row == row and not (right < other_left - 8 or left > other_right + 8):
                row = 1
                break
        occupied.append((left, right, row))
        parts.append(
            f'<text x="{tx}" y="{fill_label_h + 14 + row * 16}" text-anchor="{anchor}" fill="#1c1915" '
            f'font-size="13" font-weight="600" font-family="Segoe UI, sans-serif">{attr(text)}</text>'
        )

    for bar, labels in fill_labels.items():
        if bar < 1 or bar > len(hits):
            continue
        text = " / ".join(labels)
        x = bar_left(bar) + bar_w / 2
        text_width = len(text) * 6.2
        if x + text_width / 2 > width - 8:
            anchor = "end"
            tx = min(x, width - 8)
        elif x - text_width / 2 < pad_l:
            anchor = "start"
            tx = x
        else:
            anchor = "middle"
            tx = x
        parts.append(
            f'<text x="{tx}" y="16" text-anchor="{anchor}" fill="#161513" font-size="12" '
            f'font-weight="600" font-family="Segoe UI, sans-serif">{attr(text)}</text>'
        )
        parts.append(
            f'<polygon points="{x - 5},{plot_top - 2} {x + 5},{plot_top - 2} {x},{plot_top + 8}" fill="#161513"/>'
        )

    division_starts = [
        divisions[key][0]
        for key in ("intro", "pattern1", "pattern2", "ending")
        if not (same_pattern and key == "pattern2")
    ]
    specials = set(division_starts)
    for bar in (divisions["pattern1Fill"][0], divisions["pattern2Fill"][0]):
        if all(abs(bar - start) > 2 for start in division_starts):
            specials.add(bar)
    axis_bars = {1, len(hits)}
    axis_bars.update(bar for bar in specials if 1 <= bar <= len(hits))
    for bar in range(8, len(hits) + 1, 8):
        if all(abs(bar - special) > 3 for special in specials):
            axis_bars.add(bar)
    for bar in sorted(axis_bars):
        x = bar_left(bar) + bar_w / 2
        weight = ' font-weight="700"' if bar in specials else ""
        parts.append(
            f'<text x="{x}" y="{plot_bottom + 18}" text-anchor="middle" fill="#1c1915" '
            f'font-size="11" font-family="Segoe UI, sans-serif"{weight}>{bar}</text>'
        )

    parts.append("</svg>")
    return "".join(parts)


def typing_rows(song: dict) -> str:
    rows = []
    same = song["divisions"]["pattern1"] == song["divisions"]["pattern2"]
    for key, label in DIVISION_ORDER:
        start, end = song["divisions"][key]
        length = length_of(song["divisions"][key])
        swatch_class = "fill" if key.endswith("Fill") else key
        extra = ""
        if key == "pattern2" and same:
            extra = '<div class="range">same measures as Pattern 1</div>'
        else:
            extra = f'<div class="range">bars {start}–{end}</div>'
        rows.append(
            "<tr>"
            f'<td class="name"><span class="swatch {swatch_class}"></span>{escape(label)}</td>'
            f'<td class="num">{start}</td>'
            f'<td class="num">{length}</td>'
            f"<td>{extra}</td>"
            "</tr>"
        )
    return "\n".join(rows)


def render_song(song: dict, midi_path: Path) -> str:
    check_expected(song)
    midi = parse_midi(midi_path)
    hits, meters = build_measures(midi)
    last_needed = max(span[1] for span in song["divisions"].values())
    flag = ""
    if len(hits) < last_needed:
        flag = (
            f'<p class="flag">This file has {len(hits)} measures. The map uses bar {last_needed}. '
            "Do not type these values until the file covers that bar.</p>"
        )
    roles = paint_roles(len(hits), song["divisions"])
    notes = "".join(f"<li>{escape(note)}</li>" for note in song["notes"])
    note_block = f'<ul class="notes">{notes}</ul>' if notes else ""
    file_label = midi_path.name
    meta = (
        f"{escape(file_label)} · {escape(tempo_text(midi['tempos']))} · "
        f"{escape(meter_text(meters))} · {len(hits)} measures in the file"
    )
    print(
        f"{song['title']}: measures={len(hits)} {tempo_text(midi['tempos'])} "
        f"meter={meter_text(meters)} maxHits={max(hits) if hits else 0} "
        f"first8={hits[:8]}"
    )
    return f"""
<article id="{attr(song["id"])}">
  <h2>{escape(song["artist"])} — {escape(song["title"])}</h2>
  <p class="meta">{meta}</p>
  {flag}
  <div class="chart-scroll">
    {render_chart(song, hits, roles)}
  </div>
  <p class="caption">Column height is hits on the drum kit. Gray measures are not stored on the pedal. A black outline and a triangle mark a one-bar fill.</p>
  <table class="sheet">
    <caption>Type these into Rhythm Converter</caption>
    <thead>
      <tr><th>Division</th><th>Start measure</th><th>Length</th><th>File bars</th></tr>
    </thead>
    <tbody>
      {typing_rows(song)}
    </tbody>
  </table>
  {note_block}
</article>
"""


def render_page(catalog: dict, songs_html: str) -> str:
    nav = "\n".join(
        f'<a href="#{attr(song["id"])}">{escape(song["title"])}</a>' for song in catalog["songs"]
    )
    excluded = "\n".join(
        f"<li><strong>{escape(item['artist'])} — {escape(item['title'])}.</strong> {escape(item['reason'])}</li>"
        for item in catalog["excluded"]
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>RC-10R section charts</title>
  <style>
    :root {{
      --paper: #f3efe6;
      --card: #fffdf8;
      --ink: #1c1915;
      --muted: #5c564c;
      --line: #e4ddd0;
      --intro: #1f7a4d;
      --pattern1: #1d5c99;
      --pattern2: #c46b12;
      --ending: #6d3d86;
      --unstored: #d9d4cc;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Segoe UI", system-ui, sans-serif;
      background: var(--paper);
      color: var(--ink);
      line-height: 1.45;
    }}
    header.page, .excluded {{
      padding: 28px 32px 8px;
      max-width: 980px;
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: 1.85rem;
      font-weight: 650;
      letter-spacing: -0.02em;
    }}
    .lede {{ margin: 0 0 14px; color: var(--muted); max-width: 68ch; }}
    nav {{ display: flex; flex-wrap: wrap; gap: 8px; margin: 0 0 14px; }}
    nav a {{
      color: var(--ink);
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 4px 10px;
      text-decoration: none;
      font-size: 0.92rem;
    }}
    .legend {{ display: flex; flex-wrap: wrap; gap: 14px 18px; font-size: 0.92rem; margin: 0; padding: 0; list-style: none; }}
    .swatch {{
      width: 14px;
      height: 14px;
      display: inline-block;
      vertical-align: -2px;
      margin-right: 6px;
      border-radius: 2px;
    }}
    .swatch.intro {{ background: var(--intro); }}
    .swatch.pattern1 {{ background: var(--pattern1); }}
    .swatch.pattern2 {{ background: var(--pattern2); }}
    .swatch.ending {{ background: var(--ending); }}
    .swatch.unstored {{ background: var(--unstored); }}
    .swatch.fill {{ background: #fff; border: 2px solid #161513; width: 12px; height: 12px; }}
    main {{ padding: 12px 32px 24px; }}
    article {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 20px 20px 8px;
      margin: 0 0 22px;
    }}
    article h2 {{ margin: 0 0 4px; font-size: 1.4rem; }}
    .meta {{ color: var(--muted); margin: 0 0 12px; }}
    .flag {{
      background: #fde8e4;
      border: 1px solid #e3b0a6;
      color: #6d2418;
      padding: 8px 12px;
      border-radius: 8px;
      margin: 0 0 12px;
    }}
    .chart-scroll {{
      overflow-x: auto;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
    }}
    .caption {{ color: var(--muted); font-size: 0.92rem; margin: 8px 0 0; }}
    table.sheet {{
      border-collapse: collapse;
      width: 100%;
      max-width: 760px;
      margin: 8px 0 12px;
    }}
    table.sheet caption {{
      caption-side: top;
      text-align: left;
      font-weight: 650;
      padding: 12px 0 6px;
    }}
    table.sheet th {{
      text-align: left;
      font-size: 0.75rem;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      color: var(--muted);
      font-weight: 650;
      padding: 6px 10px;
      border-bottom: 1px solid var(--line);
    }}
    table.sheet td {{
      padding: 8px 10px;
      border-bottom: 1px solid var(--line);
      vertical-align: middle;
    }}
    table.sheet td.num {{
      font-size: 1.55rem;
      font-weight: 700;
      font-variant-numeric: tabular-nums;
      width: 8.5rem;
    }}
    .range {{ color: var(--muted); font-size: 0.9rem; }}
    .notes {{ margin: 0 0 12px; padding-left: 18px; }}
    .excluded h2 {{ font-size: 1.25rem; margin-bottom: 8px; }}
    .excluded ul {{ padding-left: 18px; }}
    .excluded li {{ margin: 6px 0; }}
  </style>
</head>
<body>
  <header class="page">
    <h1>RC-10R section charts</h1>
    <p class="lede">Open the MIDI in Rhythm Converter, delete the user pattern already in that memory, then type the start measure and length for each division. Fills are one bar and can sit inside another division. Ranges are the file’s own measures, starting at 1.</p>
    <nav>{nav}</nav>
    <ul class="legend">
      <li><span class="swatch intro"></span>Intro</li>
      <li><span class="swatch pattern1"></span>Pattern 1</li>
      <li><span class="swatch pattern2"></span>Pattern 2</li>
      <li><span class="swatch ending"></span>Ending</li>
      <li><span class="swatch unstored"></span>Not stored</li>
      <li><span class="swatch fill"></span>Fill, one bar</li>
    </ul>
  </header>
  <main>
    {songs_html}
  </main>
  <section class="excluded" id="not-ready">
    <h2>Do not assign</h2>
    <p class="lede">These files do not match the song’s tempo, meter, or length, so they have no typing numbers.</p>
    <ul>
      {excluded}
    </ul>
  </section>
</body>
</html>
"""


def main() -> None:
    catalog = json.loads(MAP_PATH.read_text(encoding="utf-8"))
    root = Path(catalog["drumRoot"])
    blocks = []
    for song in catalog["songs"]:
        path = root / song["file"]
        if not path.is_file():
            raise SystemExit(f"Missing MIDI file: {path}")
        blocks.append(render_song(song, path))
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(render_page(catalog, "\n".join(blocks)), encoding="utf-8")
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(exc, file=sys.stderr)
        raise
