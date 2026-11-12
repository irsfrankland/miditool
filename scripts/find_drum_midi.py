"""Find a free MIDI on MIDIfind or MIDIWORLD, or build one from a Songsterr drum tab, then read its drum kit.

The saved song list opens in this window with the section chart. A new
download gets a suggested intro, two loops, two fills, and an ending.
You still type those numbers into Rhythm Converter.

Run:
  python scripts/find_drum_midi.py
"""

from __future__ import annotations

import ctypes
import hashlib
import html
import http.cookiejar
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
import winsound
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont

sys.path.insert(0, str(Path(__file__).resolve().parent))
import album_art
import build_section_chart as chart
import midi_export
from chrome_bridge import ChromeBridge
import songsterr
import suggest_sections
from pedal_sim import PedalMachine, PedalWindow
from preview_ui import ROLE, SectionChart, bar_lanes, render_section

ROOT = Path(__file__).resolve().parents[1]
ARTWORK = ROOT / "artwork"
ICON = ROOT / "assets" / "drum-finder.ico"
INCOMING = ROOT / "incoming"
ALLOWED_HOSTS = {"midifind.com", "www.midifind.com", "midiworld.com", "www.midiworld.com"}
BROWSER = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
INK = "#101114"
PANEL = "#181b22"
FIELD = "#22262f"
LINE = "#343844"
TEXT = "#f3f1eb"
MUTED = "#bdb9af"
ACCENT = "#e4a24a"
ACCENT_INK = "#1c1408"
HOVER = "#2c313c"
ACCENT_HOVER = "#f0b35c"
DISABLED = "#2a2d34"
DISABLED_TEXT = "#8a867e"
UI_FONT = "FinderUI"
TITLE_FONT = "FinderTitle"
REPORT_FONT = "FinderMono"
SECTION_FONT = "FinderSection"
SONG_FONT = "FinderSong"
SMALL_FONT = "FinderSmall"
FONT_SPECS = {
    UI_FONT: ("Segoe UI", 10, "normal"),
    TITLE_FONT: ("Segoe UI", 14, "normal"),
    REPORT_FONT: ("Cascadia Mono", 10, "normal"),
    SECTION_FONT: ("Segoe UI", 9, "normal"),
    SONG_FONT: ("Segoe UI", 14, "normal"),
    SMALL_FONT: ("Segoe UI", 9, "normal"),
}
TEXT_SCALES = (80, 150)

DIVISIONS = (
    ("intro", "Intro", False),
    ("pattern1", "Pattern 1", False),
    ("pattern1Fill", "Pattern 1 fill", True),
    ("pattern2", "Pattern 2", False),
    ("pattern2Fill", "Pattern 2 fill", True),
    ("ending", "Ending", False),
)


@dataclass
class Hit:
    site: str
    title: str
    page_url: str
    download_url: str
    more: bool = False
    tab: songsterr.Tab | None = None


class Web:
    def __init__(self) -> None:
        jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    def get(self, url: str, referer: str, limit: int) -> tuple[str, bytes]:
        host = urllib.parse.urlparse(url).hostname or ""
        if host not in ALLOWED_HOSTS:
            raise ValueError(f"Refusing to open {host or url}")
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": BROWSER,
                "Accept": "*/*",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": referer,
            },
        )
        with self.opener.open(request, timeout=30) as response:
            final = response.geturl()
            final_host = urllib.parse.urlparse(final).hostname or ""
            if final_host not in ALLOWED_HOSTS:
                raise ValueError("The download left MIDIfind and MIDIWORLD")
            data = response.read(limit + 1)
        if len(data) > limit:
            raise ValueError("That file is larger than the finder will save")
        return final, data


def words(text: str) -> list[str]:
    return [word for word in re.findall(r"[a-z0-9']+", text.lower()) if len(word) > 2]


def rank(title: str, artist: str, song: str) -> int:
    text = title.lower()
    song_words = words(song)
    artist_words = words(artist)
    points = 3 * sum(word in text for word in song_words)
    points += sum(word in text for word in artist_words)
    if song_words and all(word in text for word in song_words):
        points += 10
    if "drum" in text:
        points += 2
    if song_words and not any(word in text for word in song_words):
        points -= 5
    return points


def visible_text(fragment: str) -> str:
    text = re.sub(r"<[^>]+>", " ", fragment)
    return " ".join(html.unescape(text).split())


def search_midifind(web: Web, query: str) -> tuple[list[Hit], bool]:
    web.get("https://midifind.com/", "https://midifind.com/", 500_000)
    short = query if len(query) <= 30 else query[:30].rsplit(" ", 1)[0]
    url = "https://midifind.com/search/?" + urllib.parse.urlencode({"q": short or query[:30]})
    _final, data = web.get(url, "https://midifind.com/", 1_500_000)
    page = re.sub(r"<noindex>.*?</noindex>", "", data.decode("utf-8", "replace"), flags=re.S)
    found: list[Hit] = []
    seen: set[str] = set()
    for href, inner in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', page, flags=re.S):
        if not re.search(r"/files/.+/\d+-\d+-\d+-\d+$", href):
            continue
        title = visible_text(inner)
        if not title or href in seen:
            continue
        seen.add(href)
        page_url = urllib.parse.urljoin("https://midifind.com/", href)
        found.append(Hit("MIDIfind", title, page_url, ""))
    more = "swchItem-next" in page
    return found, more


def search_midiworld(web: Web, query: str) -> tuple[list[Hit], bool]:
    url = "https://www.midiworld.com/search/?" + urllib.parse.urlencode({"q": query})
    _final, data = web.get(url, "https://www.midiworld.com/", 1_500_000)
    page = data.decode("utf-8", "replace")
    found: list[Hit] = []
    seen: set[str] = set()
    pattern = re.compile(
        r"<li>\s*(.*?)\s*-\s*<a href=\"(https://www\.midiworld\.com/download/\d+)\"",
        re.S,
    )
    for inner, href in pattern.findall(page):
        title = visible_text(inner)
        if not title or href in seen:
            continue
        seen.add(href)
        found.append(Hit("MIDIWORLD", title, href, href))
    more = bool(re.search(r">\s*next", page, re.I))
    return found, more


def search(artist: str, song: str, sites: list[str]) -> tuple[list[Hit], str]:
    query = " ".join(part for part in (artist.strip(), song.strip()) if part)
    web = Web()
    notes: list[str] = []
    hits: list[Hit] = []
    if "MIDIfind" in sites:
        found, more = search_midifind(web, query)
        hits.extend(found)
        if len(query) > 30:
            notes.append("MIDIfind only accepts 30 characters, so that search was shortened.")
        if more:
            notes.append("MIDIfind has another page. Add a word and search again.")
    if "MIDIWORLD" in sites:
        # MIDIWORLD lists a song as "Title (Artist)", so the song title matches.
        # The artist still moves the closer files to the top.
        found, more = search_midiworld(web, song.strip() or query)
        hits.extend(found)
        if more:
            notes.append("MIDIWORLD has another page. Add a word and search again.")
    if "Songsterr" in sites:
        hits.extend(search_songsterr(query))
    hits.sort(key=lambda hit: rank(hit.title, artist, song), reverse=True)
    return hits, " ".join(notes)


def search_songsterr(query: str) -> list[Hit]:
    """Songsterr songs with a drum part. Downloading one turns that part into a MIDI."""
    found = []
    for tab in songsterr.search_tabs(query):
        part = next((part for part in tab.parts if part.index == tab.drum_track), None)
        player = f"  ·  {part.name}" if part and part.name not in ("Drums", "Unnamed part") else ""
        found.append(Hit("Songsterr", f"{tab.artist} — {tab.title}{player}", tab.url, "", tab=tab))
    return found


def songsterr_midi(tab: songsterr.Tab, ppq: int = 480) -> Path:
    """Write the tab's drum part as a type 0 MIDI in incoming."""
    _meta, track = songsterr.drum_part(tab)
    drums = songsterr.drums_to_midi(track, ppq)
    if not drums.ticks:
        raise ValueError("Songsterr's drum part for this song has no notes.")
    data = midi_export.build(ppq, drums.ticks, drums.tempos, drums.bounds, drums.meters, f"{tab.artist} - {tab.title}")
    INCOMING.mkdir(parents=True, exist_ok=True)
    name = safe_name(f"{tab.artist} - {tab.title} [Songsterr {tab.song_id}].mid", "songsterr.mid")
    path = unique_path(INCOMING / name)
    path.write_bytes(data)
    return path


def resolve_download(web: Web, hit: Hit) -> tuple[str, str]:
    if hit.site == "MIDIWORLD":
        return hit.download_url, hit.title + ".mid"
    _final, data = web.get(hit.page_url, "https://midifind.com/", 1_500_000)
    page = data.decode("utf-8", "replace")
    match = re.search(
        r'href="(/files/0-0-1-\d+-\d+)"[^>]*download="([^"]+)"',
        page,
    )
    if not match:
        raise ValueError("MIDIfind did not offer a .mid download on that page")
    return urllib.parse.urljoin("https://midifind.com/", match.group(1)), match.group(2)


def save_midi(web: Web, hit: Hit) -> Path:
    if hit.tab is not None:
        return songsterr_midi(hit.tab)
    referer = hit.page_url if hit.site == "MIDIfind" else "https://www.midiworld.com/"
    url, filename = resolve_download(web, hit)
    _final, data = web.get(url, referer, 8_000_000)
    if not data.startswith(b"MThd"):
        raise ValueError("The site did not return a MIDI file")
    INCOMING.mkdir(parents=True, exist_ok=True)
    cleaned = safe_name(filename, "download.mid")
    if not cleaned.lower().endswith((".mid", ".midi")):
        cleaned += ".mid"
    path = unique_path(INCOMING / cleaned)
    path.write_bytes(data)
    return path


def song_id(title: str) -> str:
    text = re.sub(r"['\u2018\u2019]", "", title.lower())
    slug = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return slug or "song"


def safe_name(name: str, fallback: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*]', "_", name).strip().rstrip(".")
    return cleaned or fallback


def artist_dir(root: Path, artist: str) -> Path:
    wanted = safe_name(artist, "")
    if not wanted:
        raise ValueError("Enter an artist so the file has a folder.")
    if root.is_dir():
        for child in root.iterdir():
            if child.is_dir() and child.name.lower() == wanted.lower():
                return child
    return root / wanted


def audio_command(command: str, optional: bool = False) -> None:
    error = ctypes.windll.winmm.mciSendStringW(command, None, 0, None)
    if error and not optional:
        raise RuntimeError(f"Playback failed ({error})")


def open_preview(path: Path) -> None:
    audio_command("close drum", optional=True)
    audio_command(f'open "{path}" type waveaudio alias drum')
    audio_command("set drum time format milliseconds")


def play_at(ms: int) -> None:
    audio_command(f"seek drum to {max(0, int(round(ms)))}")
    audio_command("play drum")


def pause_preview() -> None:
    audio_command("pause drum")


def resume_preview() -> None:
    audio_command("resume drum")


def close_preview() -> None:
    audio_command("stop drum", optional=True)
    audio_command("close drum", optional=True)


RHYTHM_CONVERTER = Path(r"C:\Program Files (x86)\BOSS\RC-10R Rhythm Converter\RC-10R Rhythm Converter.exe")
EXPORTS = ROOT / "exports"
SETTINGS_PATH = Path(os.environ.get("APPDATA") or ROOT) / "MidiTool" / "finder-settings.json"
# Caches stay on this computer, out of OneDrive.
LOCAL_DATA = Path(os.environ.get("LOCALAPPDATA") or ROOT) / "MidiTool"
SONGSTERR_CACHE = LOCAL_DATA / "songsterr"
PREVIEWS = LOCAL_DATA / "previews"
KEEP_PREVIEWS = 4
KEEP_SESSIONS = 20
MIDI_SUFFIXES = {".mid", ".midi", ".kar"}
COVER_SIZE = 88
PART_NOUNS = {"drums": "drum", "guitar": "guitar", "bass": "bass"}
SCAN_MS = 4000
HISTORY_LIMIT = 200
# Undo steps that do not change the saved MIDI, so they leave nothing unsaved.
LINEUP_LABELS = {"move the Songsterr line-up", "line up Songsterr"}


def load_settings() -> dict:
    try:
        settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return settings if isinstance(settings, dict) else {}


def save_settings(settings: dict) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")


def clean_name(text: str) -> str:
    """Drop download-site tags, copy numbers and underscores from a file name."""
    text = text.replace("_", " ")
    text = re.sub(r"\[[^\]]*\]", "", text)
    text = re.sub(r"\((?:\d+|RC-10R[^)]*|drums?)\)", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+drums?\s*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^(.*?)\s*\(The\)", r"The \1", text.strip(), flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip(" -—")


def guess_artist_title(path: Path) -> tuple[str, str]:
    """Artist and title from "Artist - Title.mid", or from an artist folder above the file."""
    stem = clean_name(path.stem)
    parts = re.split(r"\s+[-—–]\s+", stem, maxsplit=1)
    if len(parts) == 2:
        return clean_name(parts[0]), clean_name(parts[1])
    parent = path.parent
    if parent.parent.name.casefold() == "artists":
        return parent.name, stem
    return "", stem


def work_area() -> tuple[int, int, int, int]:
    """Left, top, width and height of the desktop area not covered by the taskbar."""
    rect = (ctypes.c_long * 4)()
    if ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(rect), 0):  # SPI_GETWORKAREA
        return rect[0], rect[1], rect[2] - rect[0], rect[3] - rect[1]
    return 0, 0, 1280, 720


def start_geometry(saved: str) -> str:
    """The last window size and place if it still fits on screen, else a compact window."""
    left, top, width, height = work_area()
    found = re.fullmatch(r"(\d+)x(\d+)\+(-?\d+)\+(-?\d+)", saved or "")
    if found:
        w, h, x, y = map(int, found.groups())
        if w <= width and h <= height - 30 and left <= x <= left + width - 200 and top <= y <= top + height - 100:
            return saved
    w = min(1240, width - 40)
    h = min(820, height - 40)
    return f"{w}x{h}+{left + (width - w) // 2}+{top + max(0, (height - h - 30) // 2)}"


def brief_meters(text: str, keep: int = 3) -> str:
    """The first few meters of a list like "4/4, 3/4 at bar 20, …", with a count of the rest."""
    parts = text.split(", ")
    if len(parts) <= keep:
        return text
    return ", ".join(parts[:keep]) + f" and {len(parts) - keep} more meter changes"


def short_path(path: Path) -> str:
    parts = path.parts
    return str(path) if len(parts) <= 3 else "…\\" + "\\".join(parts[-2:])


def artist_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.casefold())


def same_file(a: Path, b: Path) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def scan_midi(folder: Path) -> list[Path]:
    """Every MIDI file under a folder, sorted by path."""
    found = []
    for base, _dirs, names in os.walk(folder):
        found.extend(Path(base) / name for name in names if Path(name).suffix.casefold() in MIDI_SUFFIXES)
    return sorted(found, key=lambda path: str(path).casefold())


def source_artist(path: Path, folder: Path, known: dict[str, str]) -> str:
    """The artist a source file belongs under: a saved artist if the folder or file name matches one."""
    artist, _title = guess_artist_title(path)
    try:
        relative = path.relative_to(folder)
        folder_name = relative.parts[0] if len(relative.parts) > 1 else ""
    except ValueError:
        folder_name = ""
    for name in (folder_name, artist):
        if name and artist_key(name) in known:
            return known[artist_key(name)]
    return folder_name or artist or "Other files"


class _FileOp(ctypes.Structure):
    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("wFunc", ctypes.c_uint),
        ("pFrom", ctypes.c_wchar_p),
        ("pTo", ctypes.c_wchar_p),
        ("fFlags", ctypes.c_ushort),
        ("fAnyOperationsAborted", ctypes.c_int),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", ctypes.c_wchar_p),
    ]


def to_recycle_bin(path: Path) -> None:
    """Move a file to the Recycle Bin so it can still be restored."""
    operation = _FileOp()
    operation.wFunc = 3  # FO_DELETE
    operation.pFrom = str(path.resolve()) + "\0"
    operation.fFlags = 0x40 | 0x10 | 0x4 | 0x400  # allow undo, no confirm, silent, no error UI
    result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(operation))
    if result != 0 or operation.fAnyOperationsAborted:
        raise OSError(f"Windows could not move {path.name} to the Recycle Bin (code {result}).")


def session_key(path: Path, editable: bool) -> str:
    name = hashlib.md5(os.path.normcase(str(path)).encode("utf-8")).hexdigest()[:12]
    return f"{'edit' if editable else 'saved'}-{name}"


def file_stamp(path: Path) -> int | None:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def preview_path(key: str, generation: int) -> Path:
    return PREVIEWS / f"{key}-{generation}.wav"


def clear_previews(keep: set[Path] | None = None) -> None:
    """Delete preview wavs, except the ones in keep. Old INCOMING previews are swept too."""
    keep = keep or set()
    for folder, pattern in ((PREVIEWS, "*.wav"), (INCOMING, "preview-*.wav")):
        if not folder.is_dir():
            continue
        for path in folder.glob(pattern):
            if path in keep:
                continue
            try:
                path.unlink()
            except OSError:
                pass


def lookup_songsterr(artist: str, title: str) -> tuple[songsterr.Tab | None, str]:
    """The Songsterr tab with its sections, or None and a reason."""
    try:
        return songsterr.find_cached(artist, title, SONGSTERR_CACHE), ""
    except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError) as exc:
        return None, f"Songsterr could not be reached ({exc})."


def tempo_at(tempos: list[tuple[int, int]], tick: int) -> int:
    return midi_export.tempo_at(tempos, tick)


def cut_tempos(tempos: list[tuple[int, int]], cut_a: int, cut_b: int) -> list[tuple[int, int]]:
    """Tempo map after the ticks from cut_a to cut_b are removed."""
    gap = cut_b - cut_a
    kept = [(tick, us) for tick, us in tempos if tick < cut_a]
    kept.append((cut_a, tempo_at(tempos, cut_b)))
    kept.extend((tick - gap, us) for tick, us in tempos if tick > cut_b)
    return midi_export.clean_tempos(kept)


def insert_tempos(tempos: list[tuple[int, int]], anchor: int, gap: int) -> list[tuple[int, int]]:
    """Tempo map after gap ticks of silence are added at anchor, at the tempo playing there."""
    kept = [(tick, us) for tick, us in tempos if tick < anchor]
    kept.append((anchor, tempo_at(tempos, anchor)))
    kept.extend((tick + gap, us) for tick, us in tempos if tick >= anchor)
    return midi_export.clean_tempos(kept)


def bar_meters(meters: list[tuple[int, int, int]], count: int) -> list[tuple[int, int]]:
    """One (numerator, denominator) per bar from the list of meter changes."""
    changes = {bar: (numerator, denominator) for bar, numerator, denominator in meters}
    current = changes.get(1, (4, 4))
    out = []
    for bar in range(1, count + 1):
        current = changes.get(bar, current)
        out.append(current)
    return out


def hits_per_bar(events: list, boundaries: list[int]) -> list[int]:
    count = max(0, len(boundaries) - 1)
    hits = [0] * count
    index = 0
    for tick, _note, _velocity in events:
        while index < count - 1 and tick >= boundaries[index + 1]:
            index += 1
        if count and boundaries[0] <= tick < boundaries[-1]:
            hits[index] += 1
    return hits


def shift_parts_for_cut(parts: list, cut_start: int, cut_end: int, new_length: int) -> list:
    shifted_parts = []
    for start, end, name in parts:
        shifted = shift_span(start, end, cut_start, cut_end)
        if shifted is None:
            continue
        left, right = shifted
        if right < 1 or left > new_length:
            continue
        left = min(max(1, left), new_length)
        right = min(max(left, right), new_length)
        shifted_parts.append((left, right, name))
    return shifted_parts


def shift_parts_for_insert(parts: list, at: int, count: int, new_length: int) -> list:
    moved = []
    for start, end, name in parts:
        if end < at:
            left, right = start, end
        elif start >= at:
            left, right = start + count, end + count
        else:
            left, right = start, end + count
        left = min(max(1, left), new_length)
        right = min(max(left, right), new_length)
        moved.append((left, right, name))
    return moved


def song_length(ms: float) -> str:
    seconds = max(0, round(ms / 1000))
    return f"{seconds // 60}:{seconds % 60:02d}"


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    number = 2
    while True:
        candidate = path.with_name(f"{path.stem} ({number}){path.suffix}")
        if not candidate.exists():
            return candidate
        number += 1


def file_notes(path: Path) -> list[str]:
    """Warnings about the file, saved with the song in the song list."""
    midi = chart.parse_midi(path)
    _bar_hits, meters, _boundaries = chart.build_measures(midi)
    kit_named = [track for track in midi["tracks"] if chart.is_kit_track(track["name"])]
    notes: list[str] = []
    tempos = [us for _tick, us in midi["tempos"] if us]
    if len({round(60_000_000 / us) for us in tempos}) > 1:
        notes.append(
            "This file has many tempo changes. Set one tempo in Rhythm Converter before you assign. "
            "This chart does not rewrite the file."
        )
    if len({(numerator, denominator) for _bar, numerator, denominator in meters}) > 1:
        notes.append("The meter changes, so a straight bar count can land on the wrong music.")
    if not kit_named:
        notes.append("No track is named drum. Channel 10 was counted, the same way as the charts.")
    if not midi["events"]:
        notes.append("This file has no drum-kit notes.")
    return notes


def parse_length(start_text: str, length_text: str, label: str) -> tuple[int, int]:
    try:
        start = int(start_text.strip())
        length = int(length_text.strip())
    except ValueError:
        raise ValueError(f"{label} needs a whole number for the start measure and the length.") from None
    if start < 1 or length < 1:
        raise ValueError(f"{label} starts at measure 1 or later, and the length is at least 1.")
    return start, length


def divisions_from(raw: dict[str, tuple[str, str]]) -> dict[str, list[int]]:
    divisions: dict[str, list[int]] = {}
    for key, label, _fill in DIVISIONS:
        start, length = parse_length(*raw[key], label)
        divisions[key] = [start, start + length - 1]
    return divisions


def typing_lines(divisions: dict[str, list[int]]) -> list[str]:
    lines = []
    for key, label, _fill in DIVISIONS:
        start, end = divisions[key]
        lines.append(f"{label} {start} × {end - start + 1}")
    return lines


def load_catalog(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def format_catalog(catalog: dict, newline: str = "\n") -> str:
    text = json.dumps(catalog, indent=2, ensure_ascii=False)
    text = re.sub(r"\[\n\s+(\d+),\n\s+(\d+)\n\s+\]", r"[\1, \2]", text)
    if newline != "\n":
        text = text.replace("\n", newline)
    if not text.endswith(newline):
        text += newline
    return text


def write_catalog(path: Path, catalog: dict) -> None:
    newline = "\r\n" if path.is_file() and b"\r\n" in path.read_bytes() else "\n"
    path.write_bytes(format_catalog(catalog, newline).encode("utf-8"))


def song_entry(
    artist: str,
    title: str,
    relative: str,
    divisions: dict[str, list[int]],
    notes: list[str],
) -> dict:
    cleaned = [note.strip() for note in notes if note.strip()]
    return {
        "id": song_id(title),
        "title": title,
        "artist": artist,
        "file": relative,
        "divisions": divisions,
        "notes": cleaned,
    }


def place_midi(src: Path, artist: str, drum_root: Path) -> tuple[str, Path, bool]:
    if not src.is_file():
        raise ValueError(f"The downloaded file is missing: {src}")
    try:
        relative = src.resolve().relative_to(drum_root.resolve()).as_posix()
        return relative, src, False
    except ValueError:
        pass
    folder = artist_dir(drum_root, artist)
    folder.mkdir(parents=True, exist_ok=True)
    filename = safe_name(src.name, "download.mid")
    if not filename.lower().endswith((".mid", ".midi")):
        filename += ".mid"
    dest = unique_path(folder / filename)
    created = not dest.exists()
    shutil.copy2(src, dest)
    relative = dest.relative_to(drum_root).as_posix()
    return relative, dest, created


def add_to_list(
    src: Path,
    artist: str,
    title: str,
    divisions: dict[str, list[int]],
    extra_note: str = "",
    *,
    catalog_path: Path = chart.MAP_PATH,
    drum_root: Path | None = None,
) -> str:
    artist = artist.strip()
    title = title.strip()
    if not artist or not title:
        raise ValueError("Enter the artist and the song title.")
    slug = song_id(title)
    previous = catalog_path.read_bytes()
    catalog = json.loads(previous.decode("utf-8"))
    if chart.is_locked(slug, catalog):
        raise ValueError(
            f"{title} is already in the song list with fixed ranges. This finder will not replace it."
        )
    root = drum_root or chart.drum_root(catalog)
    notes = file_notes(src)
    relative, dest, created = place_midi(src, artist, root)
    if extra_note.strip():
        notes.append(extra_note.strip())
    entry = song_entry(artist, title, relative, divisions, notes)
    replaced = False
    for index, current in enumerate(catalog["songs"]):
        if current["id"] == slug:
            catalog["songs"][index] = entry
            replaced = True
            break
    if not replaced:
        catalog["songs"].append(entry)
    try:
        write_catalog(catalog_path, catalog)
    except Exception:
        catalog_path.write_bytes(previous)
        if created and dest.exists():
            dest.unlink()
        raise
    verb = "Replaced" if replaced else "Added"
    return (
        f"{verb} {artist} — {title} in the song list.\n"
        f"File: {dest}\n"
        + "\n".join(typing_lines(divisions))
    )


_FONTS: dict[str, tkfont.Font] = {}


def make_fonts(root: tk.Misc) -> None:
    for name, (family, size, weight) in FONT_SPECS.items():
        _FONTS[name] = tkfont.Font(root, name=name, family=family, size=size, weight=weight)


def scale_fonts(root: tk.Misc, scale: float) -> None:
    """Resize every named finder font; widgets using them follow on their own."""
    for name, (_family, size, _weight) in FONT_SPECS.items():
        _FONTS[name].configure(size=max(7, round(size * scale)))
    line = _FONTS[UI_FONT].metrics("linespace")
    ttk.Style(root).configure("Songs.Treeview", rowheight=line + 8)


def apply_theme(root: tk.Tk) -> None:
    make_fonts(root)
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", background=INK, foreground=TEXT, font=UI_FONT)
    style.configure("TFrame", background=INK)
    style.configure("TLabel", background=INK, foreground=TEXT)
    style.configure("Title.TLabel", font=TITLE_FONT, background=INK, foreground=TEXT)
    style.configure("Muted.TLabel", background=INK, foreground=MUTED)
    style.configure("Section.TLabel", background=INK, foreground=MUTED, font=SECTION_FONT)
    style.configure("Song.TLabel", background=INK, foreground=TEXT, font=SONG_FONT)
    style.configure("Kit.TLabel", background=INK, foreground=ACCENT, font=UI_FONT)
    style.configure("TNotebook", background=INK, borderwidth=0, tabmargins=(0, 0, 0, 0))
    style.configure(
        "TNotebook.Tab",
        background=PANEL,
        foreground=MUTED,
        padding=(14, 5),
        borderwidth=0,
        lightcolor=INK,
        bordercolor=INK,
        font=UI_FONT,
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", FIELD), ("active", HOVER)],
        foreground=[("selected", TEXT)],
        lightcolor=[("selected", FIELD)],
    )
    style.configure(
        "Horizontal.TScale",
        background="#9aa0ad",
        troughcolor=FIELD,
        bordercolor=INK,
        lightcolor=PANEL,
        darkcolor=PANEL,
        sliderthickness=14,
    )
    style.map("Horizontal.TScale", background=[("active", ACCENT)])
    style.configure(
        "TEntry",
        fieldbackground=FIELD,
        foreground=TEXT,
        bordercolor=LINE,
        lightcolor=LINE,
        darkcolor=LINE,
        insertcolor=TEXT,
        padding=(6, 4),
        relief="flat",
    )
    style.map(
        "TEntry",
        bordercolor=[("focus", ACCENT)],
        lightcolor=[("focus", ACCENT)],
        darkcolor=[("focus", ACCENT)],
        fieldbackground=[("disabled", PANEL)],
        foreground=[("disabled", TEXT)],
    )
    style.configure(
        "TButton",
        background=PANEL,
        foreground=TEXT,
        borderwidth=0,
        focusthickness=0,
        focuscolor=INK,
        padding=(12, 5),
        relief="flat",
    )
    style.map(
        "TButton",
        background=[("active", HOVER), ("disabled", DISABLED)],
        foreground=[("disabled", DISABLED_TEXT)],
    )
    style.configure(
        "Accent.TButton",
        background=ACCENT,
        foreground=ACCENT_INK,
        borderwidth=0,
        focusthickness=0,
        focuscolor=INK,
        padding=(14, 5),
    )
    style.map(
        "Accent.TButton",
        background=[("active", ACCENT_HOVER), ("disabled", "#3a342c")],
        foreground=[("disabled", "#7a7268")],
    )
    style.configure(
        "Menu.TMenubutton",
        background=PANEL,
        foreground=TEXT,
        arrowsize=0,
        borderwidth=0,
        relief="flat",
        padding=(10, 4),
        width=-2,
        font=UI_FONT,
    )
    style.layout("Menu.TMenubutton", [("Menubutton.border", {"sticky": "nswe", "children": [
        ("Menubutton.padding", {"sticky": "nswe", "children": [("Menubutton.label", {"sticky": "nswe"})]})
    ]})])
    style.map("Menu.TMenubutton", background=[("active", HOVER), ("pressed", HOVER)])
    style.configure("Small.TButton", padding=(10, 4), width=-3)
    style.configure("SmallAccent.TButton", background=ACCENT, foreground=ACCENT_INK, padding=(10, 4), width=-3)
    style.map(
        "SmallAccent.TButton",
        background=[("active", ACCENT_HOVER), ("disabled", "#3a342c")],
        foreground=[("disabled", "#7a7268")],
    )
    style.configure(
        "TCheckbutton",
        background=INK,
        foreground=TEXT,
        indicatorbackground=FIELD,
        indicatorforeground=ACCENT_INK,
    )
    style.map(
        "TCheckbutton",
        background=[("active", INK)],
        indicatorbackground=[("selected", ACCENT), ("!selected", FIELD)],
    )
    style.configure(
        "Dark.Vertical.TScrollbar",
        background=FIELD,
        troughcolor=PANEL,
        bordercolor=INK,
        arrowcolor=MUTED,
        lightcolor=FIELD,
        darkcolor=FIELD,
    )
    style.map("Dark.Vertical.TScrollbar", background=[("active", HOVER)])
    style.configure(
        "Dark.Horizontal.TScrollbar",
        background=FIELD,
        troughcolor=PANEL,
        bordercolor=INK,
        arrowcolor=MUTED,
        lightcolor=FIELD,
        darkcolor=FIELD,
    )
    style.map("Dark.Horizontal.TScrollbar", background=[("active", HOVER)])
    style.configure(
        "Songs.Treeview",
        background=FIELD,
        fieldbackground=FIELD,
        foreground=TEXT,
        borderwidth=0,
        relief="flat",
        rowheight=30,
        font=UI_FONT,
        indent=16,
    )
    style.map(
        "Songs.Treeview",
        background=[("selected", ACCENT)],
        foreground=[("selected", ACCENT_INK)],
    )
    style.layout("Songs.Treeview", [("Songs.Treeview.treearea", {"sticky": "nswe"})])


def dark_titlebar(window: tk.Misc) -> None:
    try:
        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        value = ctypes.c_int(1)
        for attribute in (20, 19):
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)
            )
    except Exception:
        return


def styled_list(parent: tk.Misc) -> tk.Listbox:
    return tk.Listbox(
        parent,
        height=8,
        font=UI_FONT,
        bg=FIELD,
        fg=TEXT,
        selectbackground=ACCENT,
        selectforeground=ACCENT_INK,
        activestyle="none",
        exportselection=False,
        relief="flat",
        borderwidth=0,
        highlightthickness=1,
        highlightbackground=LINE,
        highlightcolor=ACCENT,
    )


def styled_tree(parent: tk.Misc) -> ttk.Treeview:
    return ttk.Treeview(parent, style="Songs.Treeview", show="tree", selectmode="browse")


def scroll_around(parent: tk.Misc, build) -> tuple[ttk.Frame, tk.Misc]:
    holder = ttk.Frame(parent)
    widget = build(holder)
    ybar = ttk.Scrollbar(holder, orient="vertical", command=widget.yview, style="Dark.Vertical.TScrollbar")
    xbar = ttk.Scrollbar(holder, orient="horizontal", command=widget.xview, style="Dark.Horizontal.TScrollbar")
    widget.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
    widget.grid(row=0, column=0, sticky="nsew")
    ybar.grid(row=0, column=1, sticky="ns")
    xbar.grid(row=1, column=0, sticky="ew")
    holder.rowconfigure(0, weight=1)
    holder.columnconfigure(0, weight=1)
    return holder, widget


class Tooltip:
    """Text that appears when the pointer rests on a widget."""

    def __init__(self, widget: tk.Misc, text: str = "") -> None:
        self.widget = widget
        self.text = text
        self.tip: tk.Toplevel | None = None
        self.job: str | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def __setitem__(self, key: str, value: str) -> None:
        self.text = value

    def __getitem__(self, key: str) -> str:
        return self.text

    def _schedule(self, _event: object = None) -> None:
        self._hide()
        if self.text:
            self.job = self.widget.after(450, self._show)

    def _show(self) -> None:
        self.job = None
        if not self.text or not self.widget.winfo_viewable():
            return
        tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)
        tip.attributes("-topmost", True)
        tk.Label(
            tip, text=self.text, bg="#2a2f3a", fg=TEXT, font=SMALL_FONT, justify="left",
            wraplength=440, padx=10, pady=6, relief="solid", bd=1,
        ).pack()
        x = self.widget.winfo_rootx() + 6
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        tip.wm_geometry(f"+{x}+{y}")
        self.tip = tip

    def _hide(self, _event: object = None) -> None:
        if self.job is not None:
            self.widget.after_cancel(self.job)
            self.job = None
        if self.tip is not None:
            self.tip.destroy()
            self.tip = None


class MenuEntry:
    """A menu item that is enabled, disabled, and relabelled the way a button is."""

    def __init__(self, places: list[tuple[tk.Menu, int]], prefix: str = "") -> None:
        self.places = places
        self.prefix = prefix
        menu, index = places[0]
        self.text = menu.entrycget(index, "label")[len(prefix):]

    def configure(self, state: str | None = None, text: str | None = None, **_ignored: object) -> None:
        for menu, index in self.places:
            if state is not None:
                menu.entryconfigure(index, state=state)
            if text is not None:
                menu.entryconfigure(index, label=self.prefix + (text or "—"))
        if text is not None:
            self.text = text

    config = configure

    def __setitem__(self, key: str, value: str) -> None:
        self.configure(**{key: value})

    def __getitem__(self, key: str) -> str:
        if key == "text":
            return self.text
        menu, index = self.places[0]
        return str(menu.entrycget(index, key))


class Flow(ttk.Frame):
    """Children laid out left to right, wrapping onto more rows when the frame is narrow."""

    def __init__(self, parent: tk.Misc, gap: int = 6, row_gap: int = 4) -> None:
        super().__init__(parent, width=200, height=30)
        self.items: list[tuple[tk.Misc, int]] = []
        self.gap = gap
        self.row_gap = row_gap
        self._job: str | None = None
        self.bind("<Configure>", lambda _event: self.relayout_soon())

    def add(self, widget: tk.Misc, space: int = 0) -> tk.Misc:
        self.items.append((widget, space))
        widget.bind("<Configure>", lambda _event: self.relayout_soon(), add="+")
        self.relayout_soon()
        return widget

    def relayout_soon(self) -> None:
        if self._job is None:
            self._job = self.after_idle(self.relayout)

    def relayout(self) -> None:
        self._job = None
        width = max(self.winfo_width(), 200)
        rows: list[list[tuple[tk.Misc, int, int, int]]] = [[]]
        x = 0
        for widget, space in self.items:
            w, h = widget.winfo_reqwidth(), widget.winfo_reqheight()
            lead = self.gap + space if x else 0
            if x and x + lead + w > width:
                rows.append([])
                x, lead = 0, 0
            rows[-1].append((widget, x + lead, w, h))
            x += lead + w
        y = 0
        for row in rows:
            tall = max((h for _w, _x, _width, h in row), default=0)
            for widget, left, _width, h in row:
                widget.place(x=left, y=y + (tall - h) // 2)
            y += tall + self.row_gap
        height = max(1, y - self.row_gap)
        if int(self.cget("height")) != height:
            self.configure(height=height)


class ScrollArea(ttk.Frame):
    """Fills the space it is given, and scrolls when what is inside is taller."""

    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.canvas = tk.Canvas(self, bg=INK, highlightthickness=0, bd=0, width=200, height=200)
        self.bar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview, style="Dark.Vertical.TScrollbar")
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.inner = ttk.Frame(self.canvas)
        self.item = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.showing = False
        self._size = (0, 0)
        self.canvas.bind("<Configure>", lambda _event: self.fit())
        self._poll()

    def _poll(self) -> None:
        self.fit()
        self.after(200, self._poll)

    def fit(self) -> None:
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        if width < 2:
            return
        need = self.inner.winfo_reqheight()
        tall = need > height + 1
        if tall != self.showing:
            self.showing = tall
            if tall:
                self.bar.grid(row=0, column=1, sticky="ns", padx=(4, 0))
            else:
                self.bar.grid_remove()
                self.canvas.yview_moveto(0)
        size = (width, max(height, need))
        if size != self._size:
            self._size = size
            self.canvas.itemconfigure(self.item, width=size[0], height=size[1])
            self.canvas.configure(scrollregion=(0, 0, size[0], size[1]))

    def contains(self, widget: tk.Misc | None) -> bool:
        return widget is not None and str(widget).startswith(str(self.canvas))

    def scroll(self, units: int) -> None:
        if self.showing:
            self.canvas.yview_scroll(units, "units")


class Finder:
    def __init__(self) -> None:
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("MidiTool.DrumFinder")
        except Exception:
            pass
        self.root = tk.Tk()
        self.root.title("Drum MIDI finder")
        if ICON.is_file():
            self.root.iconbitmap(default=str(ICON))
        self.settings = load_settings()
        self.root.geometry(start_geometry(self.settings.get("geometry", "")))
        self.root.minsize(860, 520)
        self.root.configure(bg=INK)
        apply_theme(self.root)
        dark_titlebar(self.root)
        self.root.bind_all("<Key-space>", self.on_space)
        self.root.bind_all("<Delete>", self.on_delete_key)
        self.root.bind_all("<Left>", self.on_arrow)
        self.root.bind_all("<Right>", self.on_arrow)
        self.hits: list[Hit] = []
        self.songs: list[dict] = []
        self.catalog: dict = {}
        self.busy = False
        self.view: dict | None = None
        self.playing: dict | None = None
        self.preparing = False
        self.play_token = 0
        self.selection: tuple[int, int] | None = None
        self.loop_on = tk.BooleanVar(value=False)
        self.songsterr_token = 0
        self.songsterr_search = ""
        self.link_tab: songsterr.Tab | None = None
        self.songsterr_queue: queue.Queue[tuple[int, songsterr.Tab | None, str]] = queue.Queue()
        self.worker_calls: queue.Queue[Callable[[], None]] = queue.Queue()
        self.tab: songsterr.Tab | None = None
        self.show_tab_parts = tk.BooleanVar(value=True)
        self.undo_stack: list[dict] = []
        self.redo_stack: list[dict] = []
        self.pedal = PedalMachine()
        self.pedal_window: PedalWindow | None = None
        self.pending_pedal = False
        self.bridge = ChromeBridge()
        self.follow_chrome = tk.BooleanVar(value=True)
        self.pending_follow = False
        self.follow_retry_at = 0.0
        self.chrome_played: int | None = None
        self.chrome_mark: tuple | None = None
        self.chrome_text = ""
        self.loop_start = tk.StringVar()
        self.loop_bars = tk.StringVar()
        self._selecting = False
        self.rendered_generation: int | None = None
        self.sessions: dict[str, dict] = {}
        clear_previews()
        self.device_open = False
        self.pending_start: tuple[int, str, tuple[int, int] | None] | None = None
        self.cover_token = 0
        self.cover_photo: tk.PhotoImage | None = None
        self.cover_queue: queue.Queue[tuple[int, tuple[Path, str] | None]] = queue.Queue()
        self.after_id: str | None = None
        self._loading = False
        self.starts: dict[str, tk.StringVar] = {}
        self.lengths: dict[str, tk.StringVar] = {}
        self.source_files: list[Path] = []
        self.file_by_iid: dict[str, Path] = {}
        self.scan_queue: queue.Queue[tuple[str, list[Path]]] = queue.Queue()
        self.scanning = False
        self.dirty = False
        self.text_scale = tk.IntVar(value=int(round(float(self.settings.get("textScale", 1.0)) * 100)))
        self._applied_scale: int | None = None
        self._scale_job: str | None = None
        self.build_menus()

        outer = ttk.Frame(self.root, padding=(10, 6, 10, 4))
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)

        top = ttk.Frame(outer)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        top.columnconfigure(0, weight=1)
        bar = Flow(top, gap=4)
        bar.grid(row=0, column=0, sticky="ew")
        for label, menu in (
            ("File", self.file_menu),
            ("Edit", self.edit_menu),
            ("View", self.view_menu),
            ("Songsterr", self.songsterr_menu),
            ("Help", self.help_menu),
        ):
            bar.add(ttk.Menubutton(bar, text=label, menu=menu, style="Menu.TMenubutton"))

        def tool(text: str, command, accent: bool = False, space: int = 0, tip: str = "") -> ttk.Button:
            button = ttk.Button(bar, text=text, command=command, style="SmallAccent.TButton" if accent else "Small.TButton")
            bar.add(button, space)
            if tip:
                Tooltip(button, tip)
            return button

        self.play_all = tool("▶  Play", lambda: self.play("all"), accent=True, space=16, tip="Play the song from the selected bar. Space plays and pauses.")
        self.pause_button = tool("Pause", self.on_pause_button)
        tool("Stop", self.on_stop)
        loop = ttk.Frame(bar)
        ttk.Checkbutton(loop, text="Loop", variable=self.loop_on, command=self.on_loop_toggle).pack(side="left")
        self.loop_start_entry = ttk.Entry(loop, textvariable=self.loop_start, width=4)
        self.loop_start_entry.pack(side="left", padx=(6, 0))
        ttk.Label(loop, text="×", style="Muted.TLabel").pack(side="left", padx=4)
        self.loop_bars_entry = ttk.Entry(loop, textvariable=self.loop_bars, width=3)
        self.loop_bars_entry.pack(side="left")
        bar.add(loop, 12)
        Tooltip(loop, "Loop from a bar, for a number of bars. Drag on the bars to pick them. Left and Right move the loop; Shift resizes it.")
        bars_button = self.bars_button = ttk.Menubutton(bar, text="Selected bars  ▾", menu=self.bars_menu, style="Menu.TMenubutton")
        bar.add(bars_button, 12)
        Tooltip(bars_button, "Make the selected bars a pedal part, or delete or blank them. A fill uses the last selected bar.")
        tool("Pedal rehearsal", self.open_pedal, space=12, tip="Practise the RC-10R footswitch along with this song.")

        sizer = ttk.Frame(top)
        sizer.grid(row=0, column=1, sticky="ne", padx=(12, 0), pady=(4, 0))
        ttk.Label(sizer, text="Text size", style="Section.TLabel").pack(side="left")
        ttk.Scale(
            sizer,
            from_=TEXT_SCALES[0],
            to=TEXT_SCALES[1],
            orient="horizontal",
            length=110,
            variable=self.text_scale,
            command=lambda _value: self.on_text_scale(),
        ).pack(side="left", padx=(8, 6))
        self.scale_label = ttk.Label(sizer, text="", style="Section.TLabel", width=5)
        self.scale_label.pack(side="left")

        body = tk.PanedWindow(outer, orient=tk.HORIZONTAL, bg=LINE, sashwidth=6, sashrelief=tk.FLAT, bd=0, opaqueresize=True)
        body.grid(row=1, column=0, sticky="nsew")

        left = ttk.Frame(body, padding=(0, 0, 8, 0))
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)
        tabs = ttk.Notebook(left)
        tabs.grid(row=0, column=0, sticky="nsew")
        self.left_tabs = tabs
        songs = ttk.Frame(tabs, padding=(0, 6, 0, 0))
        songs.rowconfigure(1, weight=1)
        songs.columnconfigure(0, weight=1)
        self.saved_label = ttk.Label(songs, text="", style="Section.TLabel")
        self.saved_label.grid(row=0, column=0, sticky="w", pady=(0, 4))
        Tooltip(self.saved_label, "White songs are in the song list with saved ranges. Grey ones are MIDI files in your source folder; click one to parse it.")
        saved_holder, self.saved_list = scroll_around(songs, lambda parent: styled_tree(parent))
        saved_holder.grid(row=1, column=0, sticky="nsew")
        self.saved_list.bind("<<TreeviewSelect>>", self.on_saved)
        tabs.add(songs, text="Songs")

        find = ttk.Frame(tabs, padding=(0, 6, 0, 0))
        find.columnconfigure(0, weight=1)
        find.rowconfigure(6, weight=1)
        ttk.Label(find, text="Artist", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        self.artist = ttk.Entry(find)
        self.artist.grid(row=1, column=0, sticky="ew", pady=(2, 6))
        ttk.Label(find, text="Song title", style="Section.TLabel").grid(row=2, column=0, sticky="w")
        self.title = ttk.Entry(find)
        self.title.grid(row=3, column=0, sticky="ew", pady=(2, 6))
        for entry in (self.artist, self.title):
            entry.bind("<Return>", lambda _event: self.on_search())
        self.use_midifind = tk.BooleanVar(value=True)
        self.use_midiworld = tk.BooleanVar(value=True)
        sites = ttk.Frame(find)
        sites.grid(row=4, column=0, sticky="w")
        self.use_songsterr = tk.BooleanVar(value=True)
        songsterr_box = ttk.Checkbutton(sites, text="Songsterr drum tabs", variable=self.use_songsterr)
        songsterr_box.grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Checkbutton(sites, text="MIDIfind", variable=self.use_midifind).grid(row=1, column=0, sticky="w", pady=(2, 0))
        ttk.Checkbutton(sites, text="MIDIWORLD", variable=self.use_midiworld).grid(row=1, column=1, sticky="w", padx=(10, 0), pady=(2, 0))
        Tooltip(songsterr_box, "Search Songsterr's tabs for any song. Downloading one turns its drum tab into a MIDI.")
        self.search_button = ttk.Button(find, text="Search", style="Accent.TButton", command=self.on_search)
        self.search_button.grid(row=5, column=0, sticky="ew", pady=6)
        result_holder, self.results = scroll_around(find, lambda parent: styled_list(parent))
        self.results.configure(width=24)
        result_holder.grid(row=6, column=0, sticky="nsew")
        self.results.bind("<<ListboxSelect>>", lambda _event: self.refresh_buttons())
        self.download_button = ttk.Button(find, text="Download and parse", command=self.on_download)
        self.download_button.grid(row=7, column=0, sticky="ew", pady=(6, 0))
        tabs.add(find, text="Find online")

        cover = ttk.Frame(left)
        cover.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        self.cover_holder = tk.Frame(cover, width=COVER_SIZE, height=COVER_SIZE, bg=FIELD, highlightthickness=1, highlightbackground=LINE)
        self.cover_holder.pack_propagate(False)
        self.cover_holder.pack(side="left")
        self.cover_label = tk.Label(self.cover_holder, bg=FIELD, fg=MUTED, font=SMALL_FONT, text="")
        self.cover_label.pack(fill="both", expand=True)
        self.album_caption = ttk.Label(cover, text="", style="Muted.TLabel", wraplength=140, justify="left")
        self.album_caption.pack(side="left", padx=(10, 0))

        self.right_area = ScrollArea(body)
        right = self.right_area.inner
        right.configure(padding=(10, 0, 4, 0))
        body.add(left, minsize=200, width=260, stretch="never")
        body.add(self.right_area, minsize=520, stretch="always")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(3, weight=1)
        self.heading = ttk.Label(right, text="Pick a song", style="Song.TLabel")
        self.heading.grid(row=0, column=0, sticky="w")
        self.meta = ttk.Label(right, text="", style="Muted.TLabel")
        self.meta.grid(row=1, column=0, sticky="w")
        info = ttk.Frame(right)
        info.grid(row=2, column=0, sticky="ew", pady=(2, 6))
        info.columnconfigure(0, weight=1)
        self.tab_info = ttk.Label(info, text="Songsterr: pick a song.", style="Muted.TLabel", justify="left")
        self.tab_info.grid(row=0, column=0, sticky="w")
        self.tab_tip = Tooltip(self.tab_info)
        self.chrome_label = ttk.Label(info, text="", style="Muted.TLabel")
        self.chrome_label.grid(row=0, column=1, sticky="e", padx=(12, 0))
        info.bind(
            "<Configure>",
            lambda event: self.tab_info.configure(wraplength=max(240, event.width - self.chrome_label.winfo_reqwidth() - 24)),
        )
        for entry in (self.loop_start_entry, self.loop_bars_entry):
            entry.bind("<FocusOut>", self.on_loop_fields)
            entry.bind("<Return>", self.on_loop_fields)
        self.chart = SectionChart(right)
        self.chart.on_zoom_changed = self.refresh_zoom
        self.chart.on_bar_click = self.on_bar_click
        self.chart.on_bars_chosen = self.on_bars_chosen
        self.chart.on_bars_inserted = self.on_bars_inserted
        self.chart.on_deselect = self.clear_selection
        self.chart.on_bar_double = self.on_bar_double
        self.chart.grid(row=3, column=0, sticky="nsew", pady=(0, 6))

        lower = ttk.Frame(right)
        lower.grid(row=4, column=0, sticky="ew")
        lower.columnconfigure(1, weight=1)
        header = ttk.Frame(lower)
        header.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 4))
        ranges_label = ttk.Label(header, text="Rhythm Converter ranges", style="Section.TLabel")
        ranges_label.pack(side="left")
        self.summary = Tooltip(ranges_label)
        self.kit_value = ttk.Label(header, text="", style="Kit.TLabel")
        self.kit_value.pack(side="left", padx=(14, 0))
        self.kit_reason = Tooltip(self.kit_value)
        ranges = ttk.Frame(lower)
        ranges.grid(row=1, column=0, sticky="nw")
        self.range_entries: list[ttk.Entry] = []
        for index, (key, label, _fill) in enumerate(DIVISIONS):
            row = index % 3
            base = 0 if index < 3 else 7
            family = {"pattern1Fill": "pattern1", "pattern2Fill": "pattern2"}.get(key, key)
            swatch_color = "#f3f1eb" if key.endswith("Fill") else ROLE[family]
            tk.Label(ranges, text="  ", bg=swatch_color, width=2).grid(
                row=row, column=base, sticky="w", pady=2, padx=(0 if base == 0 else 20, 6)
            )
            ttk.Label(ranges, text=label, width=12).grid(row=row, column=base + 1, sticky="w")
            start = tk.StringVar()
            length = tk.StringVar()
            self.starts[key] = start
            self.lengths[key] = length
            start_entry = ttk.Entry(ranges, textvariable=start, width=5)
            length_entry = ttk.Entry(ranges, textvariable=length, width=5)
            start_entry.grid(row=row, column=base + 2, sticky="w", padx=(2, 4))
            ttk.Label(ranges, text="×", style="Muted.TLabel").grid(row=row, column=base + 3, sticky="w")
            length_entry.grid(row=row, column=base + 4, sticky="w", padx=(4, 4))
            for entry in (start_entry, length_entry):
                entry.bind("<FocusOut>", self.on_ranges)
                entry.bind("<Return>", self.on_ranges)
                self.range_entries.append(entry)
            ttk.Button(ranges, text="▶", style="Small.TButton", width=2, command=lambda key=key: self.play(key)).grid(
                row=row, column=base + 5, sticky="w"
            )
        actions = ttk.Frame(lower)
        actions.grid(row=1, column=2, sticky="ne", padx=(16, 0))
        self.export_button = ttk.Button(actions, text="Save MIDI…", style="Accent.TButton", command=self.open_export)
        self.export_button.pack(fill="x")
        Tooltip(self.export_button, "Write a type 0 drum MIDI with your edits for Rhythm Converter (Ctrl+S).")
        self.add_button = ttk.Button(actions, text="Add to song list…", command=self.on_add)
        self.add_button.pack(fill="x", pady=(4, 0))
        Tooltip(self.add_button, "Save these ranges in the song list.")
        self.copy_button = ttk.Button(actions, text="Copy ranges", command=self.copy_ranges)
        self.copy_button.pack(fill="x", pady=(4, 0))

        self.status = ttk.Label(outer, text="", style="Muted.TLabel")
        self.status.grid(row=2, column=0, sticky="ew", pady=(4, 0))
        outer.bind("<Configure>", lambda event: self.status.configure(wraplength=max(400, event.width - 30)), add="+")
        self.root.bind_all("<MouseWheel>", self.on_wheel, add="+")
        self.root.after(100, self.poll_covers)
        self.root.after(30, self.poll_chrome)
        self.root.protocol("WM_DELETE_WINDOW", self.on_quit)
        self.source_files = scan_midi(self.source_folder()) if self.source_folder().is_dir() else []
        self.root.after(SCAN_MS, self.poll_sources)
        self.on_text_scale()
        self.chart.set_scale(self.text_scale.get() / 100)
        self.reload_songs()
        self.refresh_buttons()
        self.refresh_transport()
        first = next(iter(self.song_iids.values()), None)
        if first:
            self.saved_list.selection_set(first)
            self.saved_list.focus(first)
            self.saved_list.see(first)
            self.on_saved()

    def chosen_sites(self) -> list[str]:
        sites = []
        if self.use_midifind.get():
            sites.append("MIDIfind")
        if self.use_midiworld.get():
            sites.append("MIDIWORLD")
        if self.use_songsterr.get():
            sites.append("Songsterr")
        return sites

    def set_busy(self, busy: bool) -> None:
        self.busy = busy
        self.refresh_buttons()

    def refresh_buttons(self) -> None:
        state = "disabled" if self.busy else "normal"
        self.search_button["state"] = state
        selected = bool(self.results.curselection()) and not self.busy
        self.download_button["state"] = "normal" if selected else "disabled"
        editable = bool(self.view and self.view["editable"] and not self.busy)
        self.add_button["state"] = "normal" if editable else "disabled"
        for entry in self.range_entries:
            entry.configure(state="normal" if editable else "disabled")

    def set_status(self, text: str) -> None:
        self.status["text"] = text

    def reload_songs(self) -> None:
        self.catalog = load_catalog(chart.ensure_song_list())
        self.songs = list(self.catalog["songs"])
        tree = self.saved_list
        children = tree.get_children("")
        closed = {iid for iid in children if not tree.item(iid, "open")}
        selected = tree.selection()
        if children:
            tree.delete(*children)
        self.song_by_iid = {}
        self.song_iids = {}
        self.file_by_iid = {}
        root = chart.drum_root(self.catalog)
        saved_paths = [root / song["file"] for song in self.songs]
        known = {artist_key(song["artist"]): song["artist"] for song in self.songs}
        groups: dict[str, list[tuple[str, str, object]]] = {}
        names: dict[str, str] = {}

        def group(artist: str) -> list:
            key = artist_key(artist) or artist.casefold()
            names.setdefault(key, artist)
            return groups.setdefault(key, [])

        for song in self.songs:
            group(song["artist"]).append(("song", song["title"], song))
        extra = 0
        folder = self.source_folder()
        for path in self.source_files:
            if any(same_file(path, saved) for saved in saved_paths):
                continue
            _artist, title = guess_artist_title(path)
            group(source_artist(path, folder, known)).append(("file", title or path.stem, path))
            extra += 1
        for key in sorted(groups, key=lambda item: names[item].casefold()):
            parent = tree.insert("", "end", iid=f"artist:{key or 'other'}", text=names[key], open=f"artist:{key or 'other'}" not in closed)
            entries = sorted(groups[key], key=lambda entry: (entry[0] != "song", entry[1].casefold()))
            titles = [text.casefold() for _kind, text, _item in entries]
            for kind, text, item in entries:
                if kind == "song":
                    iid = tree.insert(parent, "end", iid=f"song:{item['id']}", text=text)
                    self.song_by_iid[iid] = item
                    self.song_iids[item["id"]] = iid
                    continue
                iid = "file:" + hashlib.md5(os.path.normcase(str(item)).encode("utf-8")).hexdigest()[:16]
                label = f"{text}  ·  {item.name}" if titles.count(text.casefold()) > 1 else text
                tree.insert(parent, "end", iid=iid, text=label, tags=("file",))
                self.file_by_iid[iid] = item
        tree.tag_configure("file", foreground=MUTED)
        kept = [iid for iid in selected if tree.exists(iid)]
        if kept:
            self._keep_selection = kept[0]
            tree.selection_set(kept[0])
        more = f"  ·  {extra} new" if extra else ""
        self.saved_label["text"] = f"Songs  ·  {len(self.songs)} saved{more}"
        if not self.songs and not extra:
            tree.insert("", "end", iid="hint:empty", text="No songs yet.", tags=("hint",))
            tree.insert("", "end", iid="hint:find", text="Use Find online to search", tags=("hint",))
            tree.insert("", "end", iid="hint:sites", text="Songsterr, MIDIfind, or MIDIWORLD.", tags=("hint",))
            tree.tag_configure("hint", foreground=MUTED)

    def on_saved(self, _event: object = None) -> None:
        selected = self.saved_list.selection()
        if not selected:
            return
        if getattr(self, "_keep_selection", None) == selected[0]:
            self._keep_selection = None
            return
        self._keep_selection = None
        if selected[0].startswith("hint:"):
            self.saved_list.selection_set(())
            self.left_tabs.select(1)
            self.title.focus_set()
            return
        path = self.file_by_iid.get(selected[0])
        if path is not None:
            self.open_source(path)
            return
        song = self.song_by_iid.get(selected[0])
        if song is None:
            return
        path = chart.drum_root(self.catalog) / song["file"]
        if not path.is_file():
            self.set_status(f"Missing MIDI file: {path}")
            return
        self.results.selection_clear(0, "end")
        self.show_file(
            path,
            song["artist"],
            song["title"],
            {key: list(span) for key, span in song["divisions"].items()},
            editable=False,
            summary="Saved in the song list. Type these into Rhythm Converter. Intro and the ending play once. Patterns and fills loop.",
        )
        self.view["song_id"] = song["id"]

    def show_file(
        self,
        path: Path,
        artist: str,
        title: str,
        divisions: dict[str, list[int]],
        editable: bool,
        summary: str,
        midi: dict | None = None,
    ) -> bool:
        """Show a song. Returns True when it was open before and comes back as it was left."""
        self.park_view()
        self.stop(drop_audio=True)
        self.pedal.stop_all()
        self.pedal.clear_tracks()
        self.chrome_played = None
        self.chrome_mark = None
        key = session_key(path, editable)
        session = self.sessions.pop(key, None)
        if session is not None and session["stamp"] == file_stamp(path):
            self.resume_session(session)
            self.show_view(resumed=True)
            if self.link_tab is None:
                self.request_songsterr(self.view["artist"], self.view["title"])
            return True
        if midi is None:
            midi = chart.parse_midi(path)
        hits, meters, boundaries = chart.build_measures(midi)
        parts = suggest_sections.song_parts(midi["events"], boundaries)
        kit, kit_reason = suggest_sections.recommend_kit(midi, artist, title, meters)
        tempo = chart.tempo_text(midi["tempos"])
        meter = brief_meters(chart.meter_text(meters))
        self.view = {
            "path": path,
            "artist": artist,
            "title": title,
            "ppq": midi["ppq"],
            "ticks": [[tick, note, velocity] for tick, note, velocity in midi["events"]],
            "bounds": list(boundaries),
            "tempos": midi_export.clean_tempos(midi["tempos"]),
            "meters": bar_meters(meters, len(hits)),
            "divisions": divisions,
            "parts": parts,
            "tab_parts": [],
            "tab_shift": 0,
            "tab_half": False,
            "tab_sure": False,
            "tab_score": 0.0,
            "bars_changed": False,
            "kit": kit,
            "kit_reason": kit_reason,
            "editable": editable,
            "summary": summary,
            "tempo": tempo,
            "meter": meter,
            "generation": 1,
            "key": key,
            "stamp": file_stamp(path),
        }
        self.derive()
        self.undo_stack = []
        self.redo_stack = []
        self.dirty = False
        self.tab = None
        self.link_tab = None
        self.selection = None
        self._loading = True
        self.loop_on.set(False)
        self.loop_start.set("")
        self.loop_bars.set("")
        self._loading = False
        self.show_view(resumed=False)
        self.request_songsterr(artist, title)
        return False

    def show_view(self, resumed: bool) -> None:
        """Fill the panel from self.view, for a song just parsed or one coming back."""
        view = self.view
        self.heading["text"] = f"{view['artist']} — {view['title']}"
        self.kit_value["text"] = f"RC-10R kit: {view['kit']}"
        self.kit_reason["text"] = view["kit_reason"]
        self.summary["text"] = view["summary"]
        self.meta["text"] = f"{view['tempo']}  ·  {view['meter']}  ·  {len(view['hits'])} measures  ·  {song_length(view['bar_ms'][-1] - view['bar_ms'][0])}"
        self.sync_entries()
        self.chart._selection = self.selection
        self.chart.show_selection(*(self.selection or (None, None)))
        self.redraw_chart()
        self.refresh_buttons()
        self.refresh_transport()
        self.refresh_tab_panel(keep_info=resumed)
        self.update_pedal_display()
        self.set_status("Back where you left this song." if resumed and self.undo_stack else "")
        self.load_cover(view["artist"], view["title"])

    SESSION_FIELDS = ("view", "undo_stack", "redo_stack", "dirty", "tab", "link_tab", "songsterr_search", "selection", "rendered_generation")

    def park_view(self) -> None:
        """Keep the open song's state so selecting it again needs no parsing, lookup, or render."""
        view = self.view
        if view is None or "key" not in view:
            return
        session = {name: getattr(self, name) for name in self.SESSION_FIELDS}
        session["stamp"] = view["stamp"]
        session["loop"] = (self.loop_on.get(), self.loop_start.get(), self.loop_bars.get())
        session["tab_info"] = (self.tab_info["text"], self.tab_tip["text"])
        self.sessions[view["key"]] = session
        while len(self.sessions) > KEEP_SESSIONS:
            oldest = next((key for key, item in self.sessions.items() if not item["dirty"]), None)
            if oldest is None:
                break
            del self.sessions[oldest]
        self.view = None
        self.songsterr_token += 1

    def resume_session(self, session: dict) -> None:
        for name in self.SESSION_FIELDS:
            setattr(self, name, session[name])
        self._loading = True
        looping, start, bars = session["loop"]
        self.loop_on.set(looping)
        self.loop_start.set(start)
        self.loop_bars.set(bars)
        self._loading = False
        self.set_tab_info(*session["tab_info"])

    def prune_previews(self) -> None:
        """Keep rendered audio for the open song and the few most recent others."""
        keep: set[Path] = set()
        if self.view and self.rendered_generation is not None:
            keep.add(preview_path(self.view["key"], self.rendered_generation))
        recent = list(self.sessions.items())[::-1]
        for index, (key, session) in enumerate(recent):
            if session["rendered_generation"] is None:
                continue
            if len(keep) < KEEP_PREVIEWS:
                keep.add(preview_path(key, session["rendered_generation"]))
            else:
                session["rendered_generation"] = None
        clear_previews(keep)

    def forget_session(self, path: Path) -> None:
        for editable in (True, False):
            self.sessions.pop(session_key(path, editable), None)

    def unsaved_songs(self) -> int:
        count = sum(1 for session in self.sessions.values() if session["dirty"])
        return count + (1 if self.dirty and self.view else 0)

    def derive(self) -> None:
        """Recompute times, hit counts, and drum lanes from the tick timeline."""
        view = self.view
        if view is None:
            return
        convert = chart.tick_to_ms(view["tempos"], view["ppq"])
        view["bar_ms"] = [convert(tick) for tick in view["bounds"]]
        view["events"] = [[convert(tick), note, velocity] for tick, note, velocity in view["ticks"]]
        view["hits"] = hits_per_bar(view["ticks"], view["bounds"])
        view["lanes"], view["crashes"] = bar_lanes(view["ticks"], view["bounds"])
        view["tempo"] = chart.tempo_text(view["tempos"])
        view["meter"] = brief_meters(chart.meter_text([(bar, *meter) for bar, meter in enumerate(view["meters"], start=1)]))
        self.meta["text"] = f"{view['tempo']}  ·  {view['meter']}  ·  {len(view['hits'])} measures  ·  {song_length(view['bar_ms'][-1] - view['bar_ms'][0])}"

    def chart_parts(self) -> list:
        view = self.view
        if view is None:
            return []
        if self.show_tab_parts.get() and view.get("tab_parts"):
            return view["tab_parts"]
        return view["parts"]

    def redraw_chart(self) -> None:
        view = self.view
        if view is None:
            return
        self.chart.draw(
            view["hits"], view["divisions"], self.chart_parts(), view["lanes"], view["crashes"], view["meters"], view["bar_ms"]
        )
        self.refresh_zoom()

    def sync_entries(self) -> None:
        if self.view is None:
            return
        self._loading = True
        for key, _label, _fill in DIVISIONS:
            start, end = self.view["divisions"][key]
            self.starts[key].set(str(start))
            self.lengths[key].set(str(end - start + 1))
        self._loading = False

    SNAPSHOT_KEYS = (
        "ticks", "bounds", "tempos", "meters", "divisions", "parts", "tab_parts",
        "tab_shift", "tab_half", "tab_sure", "tab_score", "bars_changed",
    )

    def snapshot(self, label: str) -> dict:
        view = self.view
        state = {key: view[key] for key in self.SNAPSHOT_KEYS}
        state["divisions"] = {key: list(span) for key, span in view["divisions"].items()}
        state["label"] = label
        state["selection"] = self.selection
        return state

    def remember(self, label: str) -> None:
        """Save the current state so Undo can bring it back."""
        if self.view is None:
            return
        self.undo_stack.append(self.snapshot(label))
        if len(self.undo_stack) > HISTORY_LIMIT:
            del self.undo_stack[0]
        self.redo_stack.clear()
        if label not in LINEUP_LABELS:
            self.dirty = True
        self.refresh_history()

    def restore(self, state: dict) -> None:
        if state["label"] not in LINEUP_LABELS:
            self.dirty = True
        view = self.view
        self.stop(drop_audio=True)
        self.pedal.reset()
        for key in self.SNAPSHOT_KEYS:
            view[key] = state[key]
        view["divisions"] = {key: list(span) for key, span in state["divisions"].items()}
        view["generation"] = view.get("generation", 1) + 1
        self.derive()
        self.sync_entries()
        selection = state.get("selection")
        if selection and selection[1] <= len(view["hits"]):
            self.chart._selection = selection
            self.apply_selection(*selection)
        else:
            self.selection = None
            self.chart.show_selection(None, None)
        self.redraw_chart()
        self.refresh_transport()
        self.refresh_tab_panel()
        self.update_pedal_display()

    def on_undo(self) -> None:
        if not self.view or not self.undo_stack:
            return
        state = self.undo_stack.pop()
        self.redo_stack.append(self.snapshot(state["label"]))
        self.restore(state)
        self.refresh_history()
        more = f" {len(self.undo_stack)} more to undo." if self.undo_stack else " Nothing left to undo."
        self.set_status(f"Undid: {state['label']}.{more}")

    def on_redo(self) -> None:
        if not self.view or not self.redo_stack:
            return
        state = self.redo_stack.pop()
        self.undo_stack.append(self.snapshot(state["label"]))
        self.restore(state)
        self.refresh_history()
        self.set_status(f"Redid: {state['label']}.")

    def refresh_history(self) -> None:
        self.undo_button.configure(state="normal" if self.undo_stack else "disabled")
        self.redo_button.configure(state="normal" if self.redo_stack else "disabled")

    def edited(self, status: str) -> None:
        """Redraw after a bar edit. The wav is rebuilt on the next play."""
        view = self.view
        view["generation"] = view.get("generation", 1) + 1
        self.derive()
        self.sync_entries()
        self.redraw_chart()
        self.refresh_transport()
        self.update_pedal_display()
        self.set_status(status)

    def load_cover(self, artist: str, title: str) -> None:
        self.cover_token += 1
        token = self.cover_token
        ready = album_art.cached(artist, title, ARTWORK)
        if ready is not None:
            self.show_cover(token, ready)
            return
        self.cover_photo = None
        self.cover_label.configure(image="", text="")
        self.album_caption["text"] = "Finding the album cover…"

        def work() -> None:
            try:
                found = album_art.cover_for(artist, title, ARTWORK)
            except Exception:
                found = None
            self.cover_queue.put((token, found))

        threading.Thread(target=work, daemon=True).start()

    def poll_covers(self) -> None:
        try:
            while True:
                token, found = self.cover_queue.get_nowait()
                self.show_cover(token, found)
        except queue.Empty:
            pass
        try:
            while True:
                token, tab, error = self.songsterr_queue.get_nowait()
                self.show_songsterr(token, tab, error)
        except queue.Empty:
            pass
        try:
            while True:
                self.worker_calls.get_nowait()()
        except queue.Empty:
            pass
        self.root.after(50, self.poll_covers)

    def from_worker(self, call: Callable[[], None]) -> None:
        """Run call on the Tk thread. Tk must not be touched from a worker thread."""
        self.worker_calls.put(call)

    def show_cover(self, token: int, found: tuple[Path, str] | None) -> None:
        if token != self.cover_token:
            return
        if found is None:
            self.cover_photo = None
            self.cover_label.configure(image="", text="No cover")
            self.album_caption["text"] = ""
            return
        path, album = found
        try:
            photo = tk.PhotoImage(file=str(path))
        except tk.TclError:
            self.cover_photo = None
            self.cover_label.configure(image="", text="No cover")
            self.album_caption["text"] = ""
            return
        biggest = max(photo.width(), photo.height())
        if biggest > COVER_SIZE:
            zoom, shrink = max(
                ((z, s) for z in (1, 2, 3) for s in range(1, 40) if biggest * z / s <= COVER_SIZE),
                key=lambda pair: pair[0] / pair[1],
                default=(1, -(-biggest // COVER_SIZE)),
            )
            photo = photo.zoom(zoom).subsample(shrink) if zoom > 1 else photo.subsample(shrink)
        self.cover_photo = photo
        self.cover_label.configure(image=self.cover_photo, text="")
        self.album_caption["text"] = album

    def on_ranges(self, _event: object = None) -> None:
        if self._loading or not self.view or not self.view["editable"]:
            return
        try:
            divisions = self.current_divisions()
        except ValueError as exc:
            self.set_status(str(exc))
            return
        if divisions == self.view["divisions"]:
            return
        self.remember("range change")
        self.view["divisions"] = divisions
        self.stop()
        self.pedal.reset()
        self.redraw_chart()
        self.update_pedal_display()
        self.set_status("Updated the suggestion. Type these into Rhythm Converter.")

    def on_assign(self, key: str) -> None:
        if not self.view or not self.selection:
            self.set_status("Drag across some bars first, then choose what they are.")
            return
        start, end = self.selection
        label = dict(chart.DIVISION_ORDER)[key]
        if key.endswith("Fill"):
            start = end
        if self.view["divisions"][key] == [start, end]:
            self.set_status(f"{label} is already {start} × {end - start + 1}.")
            return
        self.remember(f"set {label}")
        divisions = {name: list(span) for name, span in self.view["divisions"].items()}
        divisions[key] = [start, end]
        self.view["divisions"] = divisions
        self.stop()
        self.pedal.reset()
        self.sync_entries()
        self.redraw_chart()
        self.refresh_transport()
        self.update_pedal_display()
        note = ""
        if divisions["pattern1"] == divisions["pattern2"]:
            note = " Pattern 1 and Pattern 2 now cover the same bars."
        self.set_status(f"{label} is now {start} × {end - start + 1}.{note}")

    def current_divisions(self) -> dict[str, list[int]]:
        raw = {key: (self.starts[key].get(), self.lengths[key].get()) for key, _label, _fill in DIVISIONS}
        return divisions_from(raw)

    def on_search(self) -> None:
        song = self.title.get().strip()
        artist = self.artist.get().strip()
        sites = self.chosen_sites()
        if not song:
            messagebox.showinfo("Drum MIDI finder", "Enter a song title.")
            return
        if not sites:
            messagebox.showinfo("Drum MIDI finder", "Choose at least one of MIDIfind, MIDIWORLD, and Songsterr.")
            return
        query = " ".join(part for part in (artist, song) if part)
        site_text = ", ".join(sites[:-1]) + " and " + sites[-1] if len(sites) > 1 else sites[0]
        if not messagebox.askyesno(
            "Search",
            f'Search {site_text} for "{query}"?\n\nThis sends that title to the sites you checked.',
        ):
            self.set_status("Search cancelled.")
            return
        self.request_songsterr(artist, song)
        self.set_busy(True)
        self.set_status(f"Searching {site_text}…")
        threading.Thread(target=self.search_worker, args=(artist, song, sites), daemon=True).start()

    def search_worker(self, artist: str, song: str, sites: list[str]) -> None:
        try:
            hits, note = search(artist, song, sites)
            error = ""
        except Exception as exc:
            hits, note, error = [], "", str(exc)
        self.from_worker(lambda: self.show_hits(hits, note, error))

    def show_hits(self, hits: list[Hit], note: str, error: str) -> None:
        self.set_busy(False)
        self.hits = hits
        self.results.delete(0, "end")
        for hit in hits:
            self.results.insert("end", f"  {hit.site}   {hit.title}")
        if error:
            self.set_status(error)
            return
        if not hits:
            self.set_status("No files found. " + note)
            return
        self.set_status(f"{len(hits)} files. Pick one, then download it. {note}".strip())

    def on_download(self) -> None:
        selection = self.results.curselection()
        if not selection:
            return
        hit = self.hits[selection[0]]
        what = (
            f'Download the drum tab for "{hit.title}" from Songsterr, turn it into a MIDI, and suggest loop sections?'
            if hit.tab is not None
            else f'Download "{hit.title}" from {hit.site} and suggest loop sections?'
        )
        if not messagebox.askyesno(
            "Download",
            f"{what}\n\nThe file is saved in incoming. Nothing is added to the song list until you click Add to song list.",
        ):
            self.set_status("Download cancelled.")
            return
        self.set_busy(True)
        self.set_status("Downloading…")
        threading.Thread(target=self.download_worker, args=(hit,), daemon=True).start()

    def download_worker(self, hit: Hit) -> None:
        try:
            path = save_midi(Web(), hit)
            error = ""
        except Exception as exc:
            path, error = None, str(exc)
        self.from_worker(lambda: self.show_download(path, error, hit.tab))

    def show_download(self, path: Path | None, error: str, tab: songsterr.Tab | None = None) -> None:
        self.set_busy(False)
        if error or path is None:
            self.set_status(error or "The download failed.")
            return
        try:
            midi = chart.parse_midi(path)
            _hits, _meters, boundaries = chart.build_measures(midi)
            idea = suggest_sections.suggest(midi["events"], boundaries)
        except Exception as exc:
            self.set_status(str(exc))
            return
        self.saved_list.selection_set(())
        self.show_file(
            path,
            (tab.artist if tab else self.artist.get().strip()) or "Unknown",
            (tab.title if tab else self.title.get().strip()) or path.stem,
            idea.divisions,
            editable=True,
            summary=idea.summary,
        )
        self.set_status("Suggestion ready. Add to song list keeps it, or change a number if a section landed in the wrong place.")

    def play(self, key: str) -> None:
        if not self.view:
            self.set_status("Pick a saved song or download a file first.")
            return
        if key == "all":
            if not self.loop_on.get() and self.chrome_live():
                self.bridge.send("play")
                self.set_status("Asked Songsterr in Chrome to play. The drums follow it.")
                return
            if self.loop_on.get() and self.selection:
                start, end = self.selection
                self.start_playback(start, self.span_label(start, end), (start, end))
            else:
                self.start_playback(self.selection[0] if self.selection else 1, "the drum track", None)
            return
        try:
            if self.view["editable"]:
                self.view["divisions"] = self.current_divisions()
            start, end, loop, label = self.section_span(key)
        except ValueError as exc:
            self.set_status(str(exc))
            return
        hits = self.view["hits"]
        if start < 1 or end > len(hits) or end < start:
            self.set_status("That range does not fit this file.")
            return
        if self.view["bar_ms"][end] <= self.view["bar_ms"][start - 1]:
            self.set_status("That section has no time in the file.")
            return
        self.apply_selection(start, end)
        self._selecting = True
        self.loop_on.set(loop)
        self._selecting = False
        self.start_playback(start, label, (start, end) if loop else None)

    def span_label(self, start: int, end: int) -> str:
        if start == end:
            return f"bar {start}"
        return f"bars {start}–{end}"

    def loop_bounds(self, start: int, end: int) -> tuple[int, int] | None:
        bar_ms = self.view["bar_ms"]
        if start < 1 or end >= len(bar_ms) or end < start or bar_ms[end] <= bar_ms[start - 1]:
            return None
        return bar_ms[start - 1], bar_ms[end]

    def apply_selection(self, start: int, end: int) -> None:
        if not self.view or not self.view["hits"]:
            return
        count = len(self.view["hits"])
        start, end = sorted((int(start), int(end)))
        start = max(1, min(count, start))
        end = max(start, min(count, end))
        self.selection = (start, end)
        self._selecting = True
        self.loop_start.set(str(start))
        self.loop_bars.set(str(end - start + 1))
        self._selecting = False
        self.chart.show_selection(start, end)
        self.refresh_transport()

    def current_bar(self) -> int:
        if not self.view or not self.view["hits"]:
            return 1
        if self.playing:
            index = bar_index(self.view["bar_ms"], self.playhead_ms())
            return min(len(self.view["hits"]), index + 1)
        return 1

    def nudge_selection(self, direction: int, resize: bool) -> None:
        if not self.view or not self.view["hits"]:
            return
        count = len(self.view["hits"])
        if self.selection:
            start, end = self.selection
        else:
            start = self.current_bar()
            end = start
        if resize:
            if direction < 0:
                if end <= start:
                    return
                end -= 1
            elif end >= count:
                return
            else:
                end += 1
        elif direction < 0:
            if start <= 1:
                return
            start -= 1
            end -= 1
        elif end >= count:
            return
        else:
            start += 1
            end += 1
        self.apply_selection(start, end)
        self.chart.reveal(end if direction > 0 else start)
        self._selecting = True
        self.loop_on.set(True)
        self._selecting = False
        self.start_playback(start, self.span_label(start, end), (start, end))

    def on_arrow(self, event: tk.Event) -> str | None:
        widget = self.root.focus_get()
        if widget is not None and widget.winfo_class() in {"Entry", "TEntry", "Text", "Listbox", "Treeview"}:
            return None
        if event.keysym not in {"Left", "Right"}:
            return None
        self.nudge_selection(-1 if event.keysym == "Left" else 1, bool(event.state & 0x0001))
        return "break"

    def on_bars_chosen(self, start: int, end: int) -> None:
        self.apply_selection(start, end)

    def clear_selection(self) -> None:
        if not self.view or self.selection is None:
            return
        self.selection = None
        self._selecting = True
        self.loop_start.set("")
        self.loop_bars.set("")
        self._selecting = False
        self.chart.show_selection(None, None)
        self.refresh_transport()

    def on_bar_click(self, bar: int) -> None:
        """Select the bar; Play or Space starts from it. With Loop on, the loop moves there and keeps its length."""
        if not self.view:
            return
        if self.loop_on.get() and self.selection:
            if not self.selection[0] <= bar <= self.selection[1]:
                length = self.selection[1] - self.selection[0] + 1
                self.apply_selection(bar, min(len(self.view["hits"]), bar + length - 1))
            return
        self.apply_selection(bar, bar)

    def on_bar_double(self, bar: int) -> None:
        """Play from the double-clicked bar, or loop the selection there when Loop is on."""
        if not self.view:
            return
        self.on_bar_click(bar)
        if self.loop_on.get() and self.selection:
            start, end = self.selection
            self.start_playback(start, self.span_label(start, end), (start, end))
        else:
            self.start_playback(bar, "the drum track", None)

    def on_loop_toggle(self) -> None:
        if self._selecting or self._loading or not self.view:
            return
        if not self.loop_on.get():
            if self.playing:
                self.playing["loop"] = None
                self.playing["label"] = "the drum track"
            return
        if not self.selection:
            self._selecting = True
            self.loop_on.set(False)
            self._selecting = False
            self.set_status("Drag across the bars you want to loop.")
            return
        start, end = self.selection
        self.start_playback(start, self.span_label(start, end), (start, end))

    def on_loop_fields(self, _event: object = None) -> None:
        if self._selecting or not self.view:
            return
        try:
            start = int(self.loop_start.get().strip())
            bars = int(self.loop_bars.get().strip())
        except ValueError:
            return
        count = len(self.view["hits"])
        if start < 1 or bars < 1 or start > count:
            return
        end = min(count, start + bars - 1)
        self.selection = (start, end)
        self._selecting = True
        self.loop_start.set(str(start))
        self.loop_bars.set(str(end - start + 1))
        self._selecting = False
        self.chart.show_selection(start, end)
        self.refresh_transport()
        if not (self.loop_on.get() and self.playing):
            return
        self.playing["loop"] = self.loop_bounds(start, end)
        self.playing["label"] = self.span_label(start, end)
        if self.playing.get("paused"):
            return
        position = self.playhead_ms()
        bounds = self.playing["loop"]
        if bounds and not bounds[0] <= position < bounds[1]:
            self.start_playback(start, self.playing["label"], (start, end))

    def on_delete_key(self, _event: tk.Event) -> str | None:
        if self.typing_focus():
            return None
        self.on_delete_bars()
        return "break"

    def on_delete_bars(self) -> None:
        if not self.view or not self.selection:
            self.set_status("Drag across the bars you want, then delete them.")
            return
        start, end = self.selection
        total = len(self.view["hits"])
        if end - start + 1 >= total:
            self.set_status("Leave at least one bar.")
            return
        span = f"bar {start}" if start == end else f"bars {start}–{end}"
        if not messagebox.askyesno(
            "Delete bars",
            f"Remove {span} from this preview?\n\n"
            "Later bars move up to close the gap. The MIDI file is not changed.",
        ):
            self.set_status("Delete cancelled.")
            return
        self.remember(f"delete {span}")
        self.stop(drop_audio=True)
        self.pedal.reset()
        view = self.view
        cut_a = view["bounds"][start - 1]
        cut_b = view["bounds"][end]
        hits, bounds, ticks, divisions, parts = remove_bars(
            view["hits"], view["bounds"], view["ticks"], view["divisions"], view["parts"], start, end
        )
        view["bounds"] = bounds
        view["ticks"] = ticks
        view["divisions"] = divisions
        view["parts"] = parts
        view["tab_parts"] = shift_parts_for_cut(view["tab_parts"], start, end, len(hits))
        view["tempos"] = cut_tempos(view["tempos"], cut_a, cut_b)
        view["meters"] = view["meters"][: start - 1] + view["meters"][end:]
        view["bars_changed"] = True
        self._loading = True
        self.loop_on.set(False)
        self.loop_start.set("")
        self.loop_bars.set("")
        self._loading = False
        self.selection = None
        self.chart.show_selection(None, None)
        note = ""
        if divisions["pattern1"] == divisions["pattern2"]:
            note = " Pattern 1 and Pattern 2 now cover the same bars."
        self.edited(f"Removed {span}. Later bars moved up. Undo brings them back. The MIDI file is unchanged.{note}")

    def on_blank_bars(self) -> None:
        if not self.view or not self.selection:
            self.set_status("Drag across the bars you want, then blank them.")
            return
        start, end = self.selection
        span = f"bar {start}" if start == end else f"bars {start}–{end}"
        if not messagebox.askyesno(
            "Blank bars",
            f"Replace {span} with silence?\n\n"
            "The bars stay where they are, with the same length and time, so the song does not get shorter. "
            "The MIDI file is not changed.",
        ):
            self.set_status("Blank cancelled.")
            return
        self.remember(f"blank {span}")
        self.stop(drop_audio=True)
        self.pedal.reset()
        view = self.view
        _hits, ticks = silence_bars(view["hits"], view["bounds"], view["ticks"], start, end)
        view["ticks"] = ticks
        view["bars_changed"] = True
        self.edited(f"Blanked {span}. They stay in place, so the song is the same length. Undo brings the hits back.")

    def on_bars_inserted(self, at: int, count: int) -> None:
        if not self.view or count < 1:
            return
        span = "1 silent bar" if count == 1 else f"{count} silent bars"
        self.remember(f"add {span}")
        self.stop(drop_audio=True)
        self.pedal.reset()
        view = self.view
        bounds = view["bounds"]
        total = len(view["hits"])
        place_at = max(1, min(at, total + 1))
        if place_at <= total:
            anchor = bounds[place_at - 1]
            duration = bounds[place_at] - bounds[place_at - 1]
            meter = view["meters"][place_at - 1]
        else:
            anchor = bounds[-1]
            duration = bounds[-1] - bounds[-2]
            meter = view["meters"][-1]
        hits, new_bounds, ticks, divisions, parts = insert_bars(
            view["hits"], bounds, view["ticks"], view["divisions"], view["parts"], at, count
        )
        view["bounds"] = new_bounds
        view["ticks"] = ticks
        view["divisions"] = divisions
        view["parts"] = parts
        view["tab_parts"] = shift_parts_for_insert(view["tab_parts"], place_at, count, len(hits))
        view["tempos"] = insert_tempos(view["tempos"], anchor, max(int(duration), 1) * count)
        view["meters"] = view["meters"][: place_at - 1] + [meter] * count + view["meters"][place_at - 1 :]
        view["bars_changed"] = True
        self.chart._selection = (place_at, place_at + count - 1)
        self.edited(f"Added {span} at bar {place_at}. The song is longer and stays on the beat. The MIDI file is unchanged.")
        self.apply_selection(place_at, place_at + count - 1)

    def start_playback(self, bar: int, label: str, loop_bars: tuple[int, int] | None) -> None:
        if not self.view:
            return
        bar_ms = self.view["bar_ms"]
        if bar < 1 or bar >= len(bar_ms):
            return
        loop = self.loop_bounds(*loop_bars) if loop_bars else None
        self.pending_start = (bar_ms[bar - 1], label, loop)
        self.cancel_tick()
        self.ensure_rendered()

    def ensure_rendered(self) -> None:
        view = self.view
        if view is None:
            return
        generation = view["generation"]
        path = preview_path(view["key"], generation)
        if self.rendered_generation == generation and path.is_file():
            self.finish_start()
            return
        if self.preparing:
            return
        self.preparing = True
        self.play_token += 1
        token = self.play_token
        self.set_status("Preparing the drum track…")
        events = view["events"]
        threading.Thread(
            target=self.render_worker,
            args=(token, generation, events, view["bar_ms"][0], view["bar_ms"][-1]),
            daemon=True,
        ).start()

    def section_span(self, key: str) -> tuple[int, int, bool, str]:
        if key == "all":
            return 1, len(self.view["hits"]), False, "the drum track"
        start, end = self.view["divisions"][key]
        return start, end, key in chart.LOOP_KEYS, dict(chart.DIVISION_ORDER)[key]

    def render_worker(
        self,
        token: int,
        generation: int,
        events: list,
        start_ms: int,
        end_ms: int,
    ) -> None:
        try:
            wav = render_section(events, start_ms, end_ms)
            error = ""
        except Exception as exc:
            wav, error = b"", str(exc)
        self.from_worker(lambda: self.begin_rendered(token, generation, wav, error))

    def begin_rendered(self, token: int, generation: int, wav: bytes, error: str) -> None:
        if token != self.play_token:
            return
        self.preparing = False
        if error or not wav:
            self.start_failed()
            self.set_status(error or "The preview could not be built.")
            self.refresh_transport()
            return
        if not self.view or self.view["generation"] != generation:
            return
        try:
            PREVIEWS.mkdir(parents=True, exist_ok=True)
            path = preview_path(self.view["key"], generation)
            path.write_bytes(wav)
        except Exception as exc:
            self.start_failed()
            self.set_status(f"Playback failed: {exc}")
            return
        self.rendered_generation = generation
        self.prune_previews()
        self.finish_start()

    def start_failed(self) -> None:
        self.pending_start = None
        self.pending_follow = False
        self.follow_retry_at = time.time() + 3

    def finish_start(self) -> None:
        pending = self.pending_start
        view = self.view
        if pending is None or view is None or self.rendered_generation != view["generation"]:
            return
        start_ms, label, loop = pending
        file_start = view["bar_ms"][0]
        try:
            if not self.device_open:
                open_preview(preview_path(view["key"], view["generation"]))
                self.device_open = True
            play_at(start_ms - file_start)
        except Exception as exc:
            self.device_open = False
            close_preview()
            self.start_failed()
            self.set_status(f"Playback failed: {exc}")
            self.refresh_transport()
            return
        self.cancel_tick()
        self.pending_start = None
        self.playing = {
            "anchor_ms": float(start_ms),
            "origin": time.perf_counter(),
            "paused": False,
            "loop": loop,
            "label": label,
            "file_start": file_start,
        }
        if self.pending_follow:
            self.pending_follow = False
            self.playing["follow"] = True
            self.playing["synced_mark"] = self.chrome_mark
        if self.pending_pedal:
            self.pending_pedal = False
            start, end = self.pedal.span(self.pedal.state)
            self.playing["pedal"] = True
            self.playing["segment"] = (view["bar_ms"][start - 1], view["bar_ms"][end])
            self.playing["bar"] = start - 1
            self.playing["bars_done"] = 0
        else:
            self.pedal.reset()
            self.pedal.set_clock(None)
        self.tick_playhead()
        self.refresh_transport()
        self.update_pedal_display()

    def playhead_ms(self) -> float:
        state = self.playing
        if not state:
            return 0.0
        if state.get("paused"):
            return float(state["anchor_ms"])
        return float(state["anchor_ms"]) + (time.perf_counter() - state["origin"]) * 1000

    def tick_playhead(self) -> None:
        state = self.playing
        if not state or not self.view or state.get("paused"):
            return
        if state.get("pedal"):
            self.tick_pedal(state)
            return
        position = self.playhead_ms()
        loop = state.get("loop")
        song_end = self.view["bar_ms"][-1]
        if loop and position >= loop[1]:
            try:
                play_at(loop[0] - state["file_start"])
            except Exception as exc:
                self.stop()
                self.set_status(f"Playback failed: {exc}")
                return
            state["anchor_ms"] = float(loop[0])
            state["origin"] = time.perf_counter()
            position = float(loop[0])
        elif position >= song_end:
            self.stop()
            self.set_status("Stopped.")
            return
        self.show_at(position, "Looping" if loop else "Playing", state["label"])
        self.after_id = self.root.after(40, self.tick_playhead)

    def tick_pedal(self, state: dict) -> None:
        """Follow the pedal path. Ranges change on a bar line, like the RC-10R."""
        bar_ms = self.view["bar_ms"]
        position = self.playhead_ms()
        _seg_start, seg_end = state["segment"]
        index = bar_index(bar_ms, position)
        upcoming = None
        crossed = seg_end
        if position >= seg_end:
            last = bar_index(bar_ms, seg_end - 1)
            state["bars_done"] += last - state["bar"] + 1
            self.pedal.set_clock(float(state["bars_done"]))
            upcoming = self.pedal.range_done()
        elif index != state["bar"]:
            state["bars_done"] += index - state["bar"]
            if self.pedal.pending:
                crossed = bar_ms[index]
                self.pedal.set_clock(float(state["bars_done"]))
                upcoming = self.pedal.bar_done()
        state["bar"] = index
        if upcoming is not None:
            late = max(0.0, position - crossed)
            if upcoming == "idle":
                self.stop()
                self.set_status("The ending finished. Press Start to go again.")
                return
            start, end = self.pedal.span(upcoming)
            target = bar_ms[start - 1] + late
            try:
                play_at(target - state["file_start"])
            except Exception as exc:
                self.stop()
                self.set_status(f"Playback failed: {exc}")
                return
            state["anchor_ms"] = float(target)
            state["origin"] = time.perf_counter()
            state["segment"] = (bar_ms[start - 1], bar_ms[end])
            state["bar"] = start - 1
            state["label"] = dict(chart.DIVISION_ORDER)[upcoming]
            position = target
        self.pedal_clock(position)
        self.show_at(position, "Pedal:", state["label"])
        self.update_pedal_display(position)
        self.after_id = self.root.after(10, self.tick_playhead)

    def pedal_clock(self, position: float) -> None:
        """Tell the looper how many bars the pedal has played, fractions included."""
        state = self.playing
        if not (state and state.get("pedal")) or not self.view:
            self.pedal.set_clock(None)
            return
        bar_ms = self.view["bar_ms"]
        index = state["bar"]
        start, end = bar_ms[index], bar_ms[index + 1]
        length = max(1.0, end - start)
        part = min(0.999, max(0.0, (position - start) / length))
        self.pedal.set_clock(state["bars_done"] + part, length)

    def open_pedal(self) -> None:
        if not self.view:
            self.set_status("Pick a saved song or download a file first.")
            return
        if self.pedal_window is not None:
            self.pedal_window.lift()
            self.update_pedal_display()
            return
        controls = {
            name: (lambda name=name: self.pedal_press(name))
            for name in ("rhythm_tap", "rhythm_hold", "rhythm_double", "loop_tap", "loop_hold", "loop_double")
        }
        controls["sync"] = self.pedal_sync
        controls["clear"] = self.pedal_clear
        controls["stop"] = self.pedal_stop
        self.pedal_window = PedalWindow(self.root, controls, self.pedal_title(), self.pedal_closed)
        if ICON.is_file():
            try:
                self.pedal_window.window.iconbitmap(str(ICON))
            except tk.TclError:
                pass
        dark_titlebar(self.pedal_window.window)
        self.update_pedal_display()

    def pedal_title(self) -> str:
        if not self.view:
            return "RC-10R rehearsal"
        return f"{self.view['artist']} — {self.view['title']}"

    def pedal_closed(self) -> None:
        if self.playing and self.playing.get("pedal"):
            self.stop()
            self.set_status("Stopped.")
        self.pedal.stop_all()
        self.pedal_window = None

    def pedal_start(self) -> None:
        if not self.view:
            return
        try:
            if self.view["editable"]:
                self.view["divisions"] = self.current_divisions()
        except ValueError as exc:
            self.set_status(str(exc))
            return
        count = len(self.view["hits"])
        for key, label, _fill in DIVISIONS:
            start, end = self.view["divisions"][key]
            if start < 1 or end > count or end < start:
                self.set_status(f"{label} does not fit this file.")
                return
        self.stop()
        self.pedal.divisions = self.view["divisions"]
        bar_ms = self.view["bar_ms"]
        opening = self.pedal.span("intro" if self.pedal.has_intro() else "pattern1")[0]
        self.pedal.set_clock(0.0, bar_ms[opening] - bar_ms[opening - 1])
        first = self.pedal.start()
        start, _end = self.pedal.span(first)
        self.pending_pedal = True
        self.pending_start = (self.view["bar_ms"][start - 1], dict(chart.DIVISION_ORDER)[first], None)
        self.ensure_rendered()
        self.update_pedal_display()

    def pedal_press(self, name: str) -> None:
        if self.pending_pedal:
            return
        if self.playing and self.playing.get("pedal") and not self.playing.get("paused"):
            self.pedal_clock(self.playhead_ms())
        result = getattr(self.pedal, name)()
        if result == "start":
            self.pedal_start()
            return
        if result == "sync":
            self.set_status("Synced mode is on." if self.pedal.sync else "Synced mode is off.")
        self.update_pedal_display(self.playhead_ms() if self.playing else None)

    def pedal_sync(self, value: bool) -> None:
        if not self.pedal.set_sync(value):
            self.set_status("Stop the drums and the loop before changing synced mode.")
        self.update_pedal_display(self.playhead_ms() if self.playing else None)

    def pedal_clear(self) -> None:
        self.pedal.clear_tracks()
        self.pedal.note = "PHRASE CLEARED"
        self.update_pedal_display(self.playhead_ms() if self.playing else None)

    def pedal_stop(self) -> None:
        self.pedal.stop_all()
        self.stop()
        self.set_status("Stopped.")

    def update_pedal_display(self, position: float | None = None) -> None:
        window = self.pedal_window
        if window is None:
            return
        window.set_title(self.pedal_title())
        pedal = self.pedal
        state = pedal.state
        info = {
            "state": state,
            "pending": pedal.pending,
            "loop": pedal.loop,
            "track": pedal.track,
            "recorded": pedal.tracks[pedal.track],
            "note": pedal.note,
            "sync": pedal.sync,
            "range": self.pedal_ranges(),
            "waiting": pedal.waiting_text(),
            "guide": pedal.loop_guide(),
            "loop_progress": pedal.loop_progress(),
            "cue": pedal.press_now(),
            "beats": 4,
            "beat": 0,
        }
        bar_ms = self.view["bar_ms"] if self.view else []
        if state == "idle" or not self.view or position is None:
            if self.pending_pedal and self.preparing:
                info["waiting"] = "Preparing the drums…"
            bpm = None
            meter = "4/4"
            if self.view and len(bar_ms) > 1:
                first = self.view["divisions"]["intro"][0] if self.view.get("divisions") else 1
                bpm = self.bar_bpm(max(0, first - 1))
                meter = "{}/{}".format(*self.bar_meter(max(0, first - 1)))
            info["lines"] = pedal.lcd_lines(None, None, bpm, meter)
            window.show(info)
            return
        start, end = pedal.span(state)
        index = bar_index(bar_ms, position)
        beats, den = self.bar_meter(index)
        bar_start, bar_end = bar_ms[index], bar_ms[index + 1]
        beat = int((position - bar_start) / max(1.0, bar_end - bar_start) * beats) + 1
        info.update(
            beats=beats,
            beat=min(beats, max(1, beat)),
            lines=pedal.lcd_lines(index + 1 - start + 1, min(beats, max(1, beat)), self.bar_bpm(index), f"{beats}/{den}"),
            range=f"Now: {dict(chart.DIVISION_ORDER)[state]}, bars {start}–{end}.   {self.pedal_ranges()}",
        )
        window.show(info)

    def bar_meter(self, index: int) -> tuple[int, int]:
        meters = self.view.get("meters") or []
        if 0 <= index < len(meters):
            num, den = meters[index]
            return int(num), int(den)
        return 4, 4

    def bar_bpm(self, index: int) -> int | None:
        bar_ms = self.view["bar_ms"]
        if not 0 <= index < len(bar_ms) - 1:
            return None
        num, den = self.bar_meter(index)
        length = bar_ms[index + 1] - bar_ms[index]
        if length <= 0:
            return None
        return round(num * (4 / den) * 60000 / length)

    def pedal_ranges(self) -> str:
        if not self.view:
            return ""
        divisions = self.view["divisions"]
        return "  ".join(
            f"{short} {divisions[key][0]}×{divisions[key][1] - divisions[key][0] + 1}"
            for key, short in (("intro", "I"), ("pattern1", "P1"), ("pattern2", "P2"), ("ending", "E"))
        )

    def show_at(self, position: float, word: str, label: str) -> None:
        index = bar_index(self.view["bar_ms"], position)
        start = self.view["bar_ms"][index]
        end = self.view["bar_ms"][index + 1]
        fraction = (position - start) / (end - start) if end > start else 0
        self.chart.show_playhead(index, fraction)
        self.set_status(f"{word} {label} · bar {index + 1}")

    def typing_focus(self) -> bool:
        widget = self.root.focus_get()
        return widget is not None and widget.winfo_class() in {"Entry", "TEntry", "Text"}

    def on_space(self, _event: tk.Event) -> str | None:
        if self.typing_focus():
            return None
        if self.chrome_live() and not self.loop_on.get():
            state, _age = self.bridge.latest()
            self.bridge.send("pause" if state and state.get("playing") else "play")
            return "break"
        if self.preparing:
            return "break"
        if self.playing and self.playing.get("paused"):
            self.resume_playback()
        elif self.playing:
            self.pause_playback()
        else:
            self.play("all")
        return "break"

    def on_pause_button(self) -> None:
        if self.chrome_live() and self.playing and self.playing.get("follow"):
            state, _age = self.bridge.latest()
            self.bridge.send("pause" if state and state.get("playing") else "play")
            return
        if self.playing and self.playing.get("paused"):
            self.resume_playback()
        elif self.playing:
            self.pause_playback()

    def pause_playback(self) -> None:
        state = self.playing
        if not state or state.get("paused"):
            return
        position = self.playhead_ms()
        try:
            pause_preview()
        except Exception as exc:
            self.set_status(f"Playback failed: {exc}")
            return
        state["anchor_ms"] = position
        state["paused"] = True
        self.cancel_tick()
        self.show_at(position, "Paused", state["label"])
        self.refresh_transport()

    def resume_playback(self) -> None:
        state = self.playing
        if not state or not state.get("paused"):
            return
        try:
            resume_preview()
        except Exception as exc:
            self.set_status(f"Playback failed: {exc}")
            return
        state["paused"] = False
        state["origin"] = time.perf_counter()
        self.tick_playhead()
        self.refresh_transport()

    def on_stop(self) -> None:
        if self.playing and self.playing.get("follow") and self.chrome_live():
            self.bridge.send("pause")
        self.stop()
        self.set_status("Stopped.")

    # Songsterr in Chrome

    def chrome_state(self) -> tuple[dict | None, str]:
        """The page state when it is fresh and shows this song, else None and why."""
        if self.bridge.error:
            return None, self.bridge.error
        state, age = self.bridge.latest()
        if state is None:
            return None, "Chrome: not connected (Songsterr › Set up Chrome…)"
        if age > 1.5:
            return None, "Chrome: stopped answering"
        if self.tab is None:
            return None, "Chrome: connected, waiting for the tab"
        if state.get("songId") != self.tab.song_id:
            title = str(state.get("title") or "another song").split(" Tab")[0].split(" Drum")[0]
            return None, f"Chrome: showing {title}"
        if state.get("measure") is None:
            return None, "Chrome: cursor not visible yet"
        return state, ""

    def chrome_live(self) -> bool:
        return bool(self.follow_chrome.get() and self.view and self.chrome_state()[0] is not None)

    def on_follow_toggle(self) -> None:
        if not self.follow_chrome.get() and self.playing and self.playing.get("follow"):
            self.playing["follow"] = False
        self.chrome_played = None
        self.chrome_mark = None

    def chrome_target(self, state: dict) -> tuple[float, int, int] | None:
        """Where the MIDI should be now: (ms, tab bar played, MIDI bar)."""
        tab, view = self.tab, self.view
        source = int(state["measure"]) + 1
        played = [index for index, bar in enumerate(tab.bars, start=1) if bar.get("source") == source]
        if not played:
            return None
        mark = (state.get("measure"), state.get("markAt"), state.get("playing"))
        if mark != self.chrome_mark or self.chrome_played not in played:
            last = self.chrome_played
            ahead = [index for index in played if last is not None and index >= last]
            self.chrome_played = ahead[0] if ahead else played[0]
            self.chrome_mark = mark
        bar = self.chrome_played
        half = view["tab_half"]
        if half:
            midi_bar = (bar - 1) // 2 + 1 + view["tab_shift"]
            inner = ((bar - 1) % 2) / 2
            share = 0.5
        else:
            midi_bar = bar + view["tab_shift"]
            inner = 0.0
            share = 1.0
        bar_ms = view["bar_ms"]
        if not 1 <= midi_bar < len(bar_ms):
            return None
        length = bar_ms[midi_bar] - bar_ms[midi_bar - 1]
        elapsed = time.time() * 1000 - float(state.get("markAt") or 0)
        if not state.get("playing"):
            elapsed = 0.0
        elapsed = max(0.0, min(elapsed, length * share * 1.5))
        start = float(state.get("markFraction") or 0.0)
        target = bar_ms[midi_bar - 1] + (inner + start * share) * length + elapsed
        return min(target, bar_ms[-1] - 1), bar, midi_bar

    def poll_chrome(self) -> None:
        self.root.after(30, self.poll_chrome)
        state, why = self.chrome_state()
        following = bool(self.playing and self.playing.get("follow"))
        if not self.follow_chrome.get() or not self.view:
            self.show_chrome_text("" if not self.follow_chrome.get() else why)
            return
        if state is None:
            self.show_chrome_text(why)
            return
        if self.pending_pedal or (self.playing and self.playing.get("pedal")):
            self.show_chrome_text("Chrome: paused for the pedal rehearsal")
            return
        found = self.chrome_target(state)
        speed = int(state.get("speed") or 100)
        slow = f" · tab at {speed}% speed" if speed != 100 else ""
        if found is None:
            self.show_chrome_text(f"Chrome: tab bar {int(state['measure']) + 1} is outside this MIDI" + slow)
            if following and not self.playing.get("paused"):
                self.pause_playback()
            return
        target, tab_bar, midi_bar = found
        word = "Playing" if state.get("playing") else "Paused"
        self.show_chrome_text(f"Chrome: {word.lower()} · tab bar {tab_bar} = MIDI bar {midi_bar}" + slow)
        if not state.get("playing"):
            if following and not self.playing.get("paused"):
                self.pause_playback()
            return
        if self.preparing or self.pending_start is not None or time.time() < self.follow_retry_at:
            return
        if not following:
            if self.playing:
                self.stop()
            self.pending_follow = True
            self.pending_start = (int(target), "with Songsterr", None)
            self.ensure_rendered()
            return
        drift = self.playhead_ms() - target
        new_bar = self.playing.get("synced_mark") != self.chrome_mark
        self.playing["synced_mark"] = self.chrome_mark
        if self.playing.get("paused") or abs(drift) > 300 or (new_bar and abs(drift) > 35):
            self.follow_seek(target)

    def follow_seek(self, target: float) -> None:
        state = self.playing
        if not state or not self.view:
            return
        try:
            play_at(target - state["file_start"])
        except Exception as exc:
            self.stop()
            self.set_status(f"Playback failed: {exc}")
            return
        was_paused = state.get("paused")
        state["anchor_ms"] = float(target)
        state["origin"] = time.perf_counter()
        state["paused"] = False
        if was_paused:
            self.cancel_tick()
            self.tick_playhead()
            self.refresh_transport()

    def show_chrome_text(self, text: str) -> None:
        if text != self.chrome_text:
            self.chrome_text = text
            self.chrome_label["text"] = text

    def chrome_setup(self) -> None:
        folder = ROOT / "chrome-extension"
        steps = (
            "Load the small helper into Chrome once:\n\n"
            "1. In Chrome, open chrome://extensions\n"
            "2. Turn on Developer mode (top right).\n"
            "3. Click Load unpacked and choose this folder:\n"
            f"    {folder}\n\n"
            "Then open the song's drum, guitar or bass tab from the Songsterr menu and press play there, "
            "or press Play here. The drums follow Songsterr's cursor bar by bar, whichever part is open. "
            "The line-up (Songsterr menu) decides which MIDI bar matches each tab bar.\n\n"
            "The helper only talks to this computer (127.0.0.1). Keep the Chrome window at least partly visible, "
            "because Chrome slows hidden tabs.\n\n"
            "Open the folder now?"
        )
        if messagebox.askyesno("Play along with Songsterr", steps, parent=self.root):
            subprocess.Popen(["explorer", str(folder)])

    def request_songsterr(self, artist: str, title: str) -> None:
        if not title.strip() and not artist.strip():
            return
        self.songsterr_token += 1
        token = self.songsterr_token
        self.link_tab = None
        self.songsterr_search = songsterr.search_url(artist, title)
        if self.view is not None:
            self.tab = None
            self.set_tab_info("Songsterr: reading the tab…")
            self.refresh_tab_panel(keep_info=True)
        threading.Thread(target=self.songsterr_worker, args=(token, artist, title), daemon=True).start()

    def songsterr_worker(self, token: int, artist: str, title: str) -> None:
        tab, error = lookup_songsterr(artist, title)
        self.songsterr_queue.put((token, tab, error))

    def show_songsterr(self, token: int, tab: songsterr.Tab | None, error: str) -> None:
        if token != self.songsterr_token:
            return
        self.link_tab = tab
        if self.view is None:
            return
        same_song = tab is not None and songsterr.same_title(tab.title, self.view["title"])
        if tab is None or not tab.bars:
            self.tab = None
            self.view["tab_parts"] = []
            reason = error or (tab.error if tab else "") or "Songsterr has no tab data for this song."
            self.set_tab_info(f"Songsterr: {reason}", f"Songsterr: {reason} The chart keeps the drum-based section names.")
            self.refresh_tab_panel(keep_info=True)
            self.redraw_chart()
            return
        if not same_song:
            self.tab = None
            self.view["tab_parts"] = []
            self.set_tab_info(
                f"Songsterr: closest tab is {tab.artist} — {tab.title}, not this song.",
                f"Songsterr: the closest tab is {tab.artist} — {tab.title}, which is not this song. "
                "Songsterr › Search Songsterr for this song finds it by hand; Edit › Song name… tries another name.",
            )
            self.refresh_tab_panel(keep_info=True)
            self.redraw_chart()
            return
        self.tab = tab
        shift, score, sure, half = songsterr.fit(tab.bar_hits(), self.view["hits"])
        self.view["tab_score"] = score
        self.view["tab_sure"] = sure
        self.place_tab(shift, half)
        self.refresh_tab_panel()
        self.redraw_chart()

    def place_tab(self, shift: int, half: bool) -> None:
        view = self.view
        view["tab_shift"] = shift
        view["tab_half"] = half
        view["tab_parts"] = songsterr.place(self.tab.sections, shift, len(view["hits"]), half) if self.tab else []

    def refresh_tab_panel(self, keep_info: bool = False) -> None:
        view = self.view
        tab = self.tab
        ready = view is not None and tab is not None
        has_sections = ready and bool(tab.sections)
        for widget in (self.shift_down, self.shift_up, self.shift_auto):
            widget.configure(state="normal" if ready else "disabled")
        self.tab_parts_check.configure(state="normal" if has_sections else "disabled")
        self.use_tab_button.configure(state="normal" if has_sections else "disabled")
        self.match_tempo_button.configure(state="normal" if ready and tab.bars else "disabled")
        has_drums = ready and not self.busy and tab.best_part("drums") is not None
        self.tab_drums_button.configure(state="normal" if has_drums else "disabled")
        if not ready:
            self.shift_label["text"] = ""
            if not keep_info:
                self.set_tab_info("Songsterr: pick a song.")
            return
        shift = view["tab_shift"]
        half = view["tab_half"]
        if shift >= 0:
            line = f"Songsterr bar 1 = MIDI bar {1 + shift}"
        else:
            line = f"MIDI bar 1 = Songsterr bar {1 - shift * (2 if half else 1)}"
        self.shift_label["text"] = line
        midi_ms = view["bar_ms"][-1] - view["bar_ms"][0]
        tab_ms = tab.seconds() * 1000
        bpm = tab.first_bpm()
        midi_bpm = round(60_000_000 / view["tempos"][0][1]) if view["tempos"] else None
        facts = [f"{tab.count} bars", brief_meters(tab.meter_text())]
        if bpm:
            facts.insert(1, f"{bpm:g} BPM")
        facts.append(song_length(tab_ms))
        lines = [f"Songsterr: {' · '.join(facts)}.  This MIDI: {len(view['hits'])} bars · {view['tempo']} · {song_length(midi_ms)}."]
        drift = (midi_ms - tab_ms) / 1000
        if bpm and midi_bpm and abs(bpm - midi_bpm) >= 1:
            way = "faster" if midi_bpm > bpm else "slower"
            lines.append(f"The MIDI starts {abs(midi_bpm - bpm):g} BPM {way} than the tab.")
        if abs(drift) >= 2:
            lines.append(f"It runs {abs(drift):.0f} s {'longer' if drift > 0 else 'shorter'} overall, so it drifts against the recording.")
        if half:
            lines.append("The MIDI is written in half time: two tab bars make one MIDI bar.")
        if view["tab_sure"]:
            lines.append("The drums line up well at this shift.")
        else:
            lines.append("The drum match is weak. Check the line-up by ear and use − or + to move it.")
        if not tab.sections:
            lines.append("This tab has no section names, so the chart keeps the drum-based names.")
        quality = "lines up well" if view["tab_sure"] else "weak match, check the line-up"
        short = [f"Songsterr: {tab.count} bars", f"{bpm:g} BPM" if bpm else "", song_length(tab_ms), line, quality]
        if half:
            short.append("half time")
        self.set_tab_info("  ·  ".join(part for part in short if part), " ".join(lines))

    def on_shift(self, step: int) -> None:
        if not self.view or not self.tab:
            return
        self.remember("move the Songsterr line-up")
        self.view["tab_sure"] = False
        self.place_tab(self.view["tab_shift"] + step, self.view["tab_half"])
        self.refresh_tab_panel()
        self.redraw_chart()
        self.set_status(f"{self.shift_label['text']}. Sections moved with it.")

    def on_shift_auto(self) -> None:
        if not self.view or not self.tab:
            return
        shift, score, sure, half = songsterr.fit(self.tab.bar_hits(), self.view["hits"])
        if (shift, half) == (self.view["tab_shift"], self.view["tab_half"]):
            self.view["tab_sure"] = sure
            self.refresh_tab_panel()
            self.set_status("The line-up already matches the drums.")
            return
        self.remember("line up Songsterr")
        self.view["tab_score"] = score
        self.view["tab_sure"] = sure
        self.place_tab(shift, half)
        self.refresh_tab_panel()
        self.redraw_chart()
        self.set_status(f"{self.shift_label['text']}.")

    def on_match_tempo(self) -> None:
        view, tab = self.view, self.tab
        if not view or not tab or not tab.bars:
            return
        lengths = midi_export.tab_bar_lengths(tab.bars, tab.tempos)
        tempos, matched = midi_export.retime_to_tab(
            view["ppq"], view["bounds"], view["tempos"], lengths, view["tab_shift"], view["tab_half"]
        )
        if not matched:
            self.set_status("No MIDI bar lines up with a Songsterr bar at this line-up. Move it with − or + first.")
            return
        changes = len(view["tempos"])
        warning = ""
        if not view["tab_sure"]:
            warning = "\n\nThe drum match is weak, so check the line-up (− / +) first or the timing will follow the wrong bars."
        if changes > 1:
            warning += f"\n\nThis MIDI has {changes} tempo changes. They are replaced by Songsterr's timing wherever the tab covers the song."
        if not messagebox.askyesno(
            "Match Songsterr tempo",
            f"Give each MIDI bar the length of its Songsterr bar? {matched} of {len(view['hits'])} bars line up with the tab. "
            "The notes stay the same; only the timing changes."
            f"{warning}\n\nUndo brings the old timing back. Save MIDI writes the result.",
            parent=self.root,
        ):
            return
        before = view["bar_ms"][-1] - view["bar_ms"][0]
        self.remember("match Songsterr tempo")
        self.stop(drop_audio=True)
        view["tempos"] = tempos
        self.edited("")
        self.refresh_tab_panel()
        after = view["bar_ms"][-1] - view["bar_ms"][0]
        self.set_status(
            f"Matched Songsterr's timing on {matched} bars. The song went from {song_length(before)} to {song_length(after)}. "
            "Use Save MIDI to keep it."
        )

    def on_use_tab_drums(self) -> None:
        view, tab = self.view, self.tab
        if not view or not tab or self.busy:
            return
        part = tab.best_part("drums")
        if part is None:
            self.set_status("Songsterr has no drum part for this song.")
            return
        if not messagebox.askyesno(
            "Use Songsterr's drums",
            f"Replace every note in this MIDI with Songsterr's {part.name} part?\n\n"
            f"The song becomes the tab's {tab.count} bars (it has {len(view['hits'])} now), with the tab's meters and tempo, "
            "and the ranges are set from its sections.\n\n"
            "Undo brings this MIDI's drums back. Save MIDI writes the result.",
            parent=self.root,
        ):
            return
        key, ppq = view["key"], view["ppq"]
        self.set_busy(True)
        self.refresh_tab_panel(keep_info=True)
        self.set_status("Reading Songsterr's drum part…")

        def work() -> None:
            try:
                _meta, track = songsterr.drum_part(tab)
                drums, error = songsterr.drums_to_midi(track, ppq), ""
            except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, urllib.error.URLError) as exc:
                drums, error = None, f"Songsterr's drum part could not be read ({exc})."
            self.from_worker(lambda: self.finish_tab_drums(key, tab, drums, error))

        threading.Thread(target=work, daemon=True).start()

    def finish_tab_drums(self, key: str, tab: songsterr.Tab, drums: songsterr.TabDrums | None, error: str) -> None:
        self.set_busy(False)
        view = self.view
        if view is not None and self.tab is not None:
            self.refresh_tab_panel(keep_info=True)
        if drums is None:
            self.set_status(error)
            return
        if view is None or view["key"] != key:
            self.set_status("Songsterr's drums arrived after you changed songs. Open the song again and retry.")
            return
        count = len(drums.bounds) - 1
        if not drums.ticks:
            self.set_status("Songsterr's drum part has no notes.")
            return
        sections = tab.sections if tab.count == count and tab.sections else drums.sections
        self.remember("use Songsterr's drums")
        self.stop(drop_audio=True)
        self.pedal.reset()
        view["ticks"] = drums.ticks
        view["bounds"] = drums.bounds
        view["tempos"] = midi_export.clean_tempos(drums.tempos)
        view["meters"] = drums.meters
        view["parts"] = suggest_sections.song_parts([tuple(item) for item in drums.ticks], drums.bounds)
        view["tab_parts"] = list(sections)
        view["tab_shift"] = 0
        view["tab_half"] = False
        view["tab_sure"] = True
        view["tab_score"] = 1.0
        view["bars_changed"] = True
        notes: list[str] = []
        try:
            view["divisions"], notes = songsterr.divisions_from_sections(sections, count)
            self.show_tab_parts.set(True)
        except ValueError:
            for name, (start, end) in view["divisions"].items():
                start = min(max(1, start), count)
                view["divisions"][name] = [start, min(max(start, end), count)]
            notes.append("The tab has no sections, so the ranges were only trimmed to fit. Check them.")
        self._loading = True
        self.loop_on.set(False)
        self.loop_start.set("")
        self.loop_bars.set("")
        self._loading = False
        self.selection = None
        self.chart.show_selection(None, None)
        changed = []
        if drums.moved:
            changed.append(f"{sum(drums.moved.values())} Guitar Pro drum sounds were moved to the nearest standard drum")
        if drums.dropped:
            changed.append(f"{sum(drums.dropped.values())} notes with no drum sound were left out")
        extra = (" " + ". ".join(changed) + ".") if changed else ""
        self.edited(
            f"Switched to Songsterr's {drums.part_name}: {count} bars, {len(drums.ticks)} hits.{extra} "
            + " ".join(notes)
            + " Undo brings the old drums back. Use Save MIDI to keep it."
        )
        self.refresh_tab_panel()

    def build_menus(self) -> None:
        def make() -> tk.Menu:
            return tk.Menu(
                self.root,
                tearoff=False,
                bg=FIELD,
                fg=TEXT,
                activebackground=ACCENT,
                activeforeground=ACCENT_INK,
                disabledforeground=DISABLED_TEXT,
                selectcolor=TEXT,
                relief="flat",
                bd=1,
                font=UI_FONT,
            )

        self.entries: dict[str, MenuEntry] = {}

        def add(menu: tk.Menu, key: str, label: str, command, accelerator: str = "", prefix: str = "") -> MenuEntry:
            menu.add_command(label=label, command=command, accelerator=accelerator)
            entry = MenuEntry([(menu, menu.index("end"))], prefix)
            self.entries[key] = entry
            return entry

        file = self.file_menu = make()
        add(file, "open", "Open MIDI…", self.open_midi_file, "Ctrl+O")
        add(file, "save", "Save MIDI for RC-10R…", self.open_export, "Ctrl+S")
        add(file, "copy", "Copy ranges", self.copy_ranges)
        add(file, "add", "Add to song list…", self.on_add)
        file.add_separator()
        add(file, "source", "Source MIDI folder…", self.choose_source_folder)
        add(file, "show_source", "Show source folder", lambda: self.show_folder(self.source_folder()))
        add(file, "final", "Final MIDI folder…", self.choose_output_folder)
        add(file, "show_final", "Show final folder", lambda: self.show_folder(self.output_folder()))
        add(file, "refresh", "Refresh song list", self.refresh_sources, "F5")
        file.add_separator()
        add(file, "quit", "Quit", self.on_quit, "Ctrl+Q")

        self.bars_menu = make()
        bars_cascade = make()
        places: dict[str, list[tuple[tk.Menu, int]]] = {}
        for menu in (self.bars_menu, bars_cascade):
            for key, label, _fill in DIVISIONS:
                menu.add_command(label=f"Set as {label}", command=lambda key=key: self.on_assign(key))
                places.setdefault(key, []).append((menu, menu.index("end")))
            menu.add_separator()
            menu.add_command(label="Delete bars", accelerator="Del", command=self.on_delete_bars)
            places.setdefault("delete", []).append((menu, menu.index("end")))
            menu.add_command(label="Blank bars (keep their time)", command=self.on_blank_bars)
            places.setdefault("blank", []).append((menu, menu.index("end")))
        self.assign_buttons = [MenuEntry(places[key]) for key, _label, _fill in DIVISIONS]
        self.delete_button = MenuEntry(places["delete"])
        self.blank_button = MenuEntry(places["blank"])

        edit = self.edit_menu = make()
        self.undo_button = add(edit, "undo", "Undo", self.on_undo, "Ctrl+Z")
        self.redo_button = add(edit, "redo", "Redo", self.on_redo, "Ctrl+Y")
        edit.add_separator()
        edit.add_cascade(label="Selected bars", menu=bars_cascade)
        edit.add_separator()
        add(edit, "rename", "Song name…", self.rename_song)

        view = self.view_menu = make()
        add(view, "zoom_in", "Zoom in", lambda: self.on_zoom(1), "Ctrl++")
        add(view, "zoom_out", "Zoom out", lambda: self.on_zoom(-1), "Ctrl+−")
        view.add_separator()
        view.add_checkbutton(label="Show Songsterr sections", variable=self.show_tab_parts, command=self.redraw_chart)
        self.tab_parts_check = MenuEntry([(view, view.index("end"))])
        view.add_separator()
        add(view, "bigger", "Bigger text", lambda: self.step_text_scale(10))
        add(view, "smaller", "Smaller text", lambda: self.step_text_scale(-10))
        add(view, "normal", "Normal text size", lambda: self.step_text_scale(0))

        tab = self.songsterr_menu = make()
        for kind in songsterr.PART_KINDS:
            add(tab, f"part_{kind}", f"Open the {PART_NOUNS[kind]} tab", lambda kind=kind: self.open_songsterr(kind))
        self.parts_menu = make()
        tab.add_cascade(label="Open another part", menu=self.parts_menu)
        self.parts_cascade = MenuEntry([(tab, tab.index("end"))])
        add(tab, "search", "Search Songsterr for this song", self.search_songsterr)
        tab.add_separator()
        self.shift_label = add(tab, "shift_label", "Line-up: —", lambda: None, prefix="Line-up: ")
        self.shift_label.configure(state="disabled")
        self.shift_down = add(tab, "shift_down", "Move the tab one bar earlier", lambda: self.on_shift(-1))
        self.shift_up = add(tab, "shift_up", "Move the tab one bar later", lambda: self.on_shift(1))
        self.shift_auto = add(tab, "shift_auto", "Line up automatically", self.on_shift_auto)
        tab.add_separator()
        self.use_tab_button = add(tab, "use_tab", "Use Songsterr sections", self.on_use_tab)
        self.match_tempo_button = add(tab, "match", "Match Songsterr tempo…", self.on_match_tempo)
        self.tab_drums_button = add(tab, "tab_drums", "Use Songsterr's drums for the whole song…", self.on_use_tab_drums)
        tab.add_separator()
        tab.add_checkbutton(label="Play along with Songsterr in Chrome", variable=self.follow_chrome, command=self.on_follow_toggle)
        add(tab, "chrome_setup", "Set up Chrome…", self.chrome_setup)

        self.help_menu = make()
        add(self.help_menu, "tips", "Chart and keyboard tips", self.show_tips)

        for menu in (file, edit, view, tab, self.bars_menu, bars_cascade):
            menu.configure(postcommand=self.refresh_menu)
        keys = {
            "<Control-o>": self.open_midi_file,
            "<Control-s>": self.open_export,
            "<Control-q>": self.on_quit,
            "<F5>": self.refresh_sources,
            "<Control-plus>": lambda: self.on_zoom(1),
            "<Control-equal>": lambda: self.on_zoom(1),
            "<Control-minus>": lambda: self.on_zoom(-1),
        }
        for sequence, command in keys.items():
            self.root.bind_all(sequence, lambda _event, command=command: (command(), "break")[1])
        for sequence, command in (("<Control-z>", self.on_undo), ("<Control-y>", self.on_redo)):
            self.root.bind_all(sequence, lambda _event, command=command: None if self.typing_focus() else (command(), "break")[1])

    def refresh_menu(self) -> None:
        def enable(key: str, on: bool) -> None:
            self.entries[key].configure(state="normal" if on else "disabled")

        view = self.view
        enable("save", bool(view))
        enable("copy", bool(view))
        enable("add", bool(view and view["editable"] and not self.busy))
        enable("rename", bool(view))
        enable("zoom_in", bool(view) and self.chart.can_zoom(1))
        enable("zoom_out", bool(view) and self.chart.can_zoom(-1))
        enable("search", bool(self.songsterr_search))
        tab = self.link_tab
        for kind in songsterr.PART_KINDS:
            noun = PART_NOUNS[kind]
            part = tab.best_part(kind) if tab else None
            if part is not None:
                self.entries[f"part_{kind}"].configure(state="normal", text=f"Open the {noun} tab · {part.name}")
            elif tab is not None:
                self.entries[f"part_{kind}"].configure(state="disabled", text=f"No {noun} tab on Songsterr")
            else:
                self.entries[f"part_{kind}"].configure(state="disabled", text=f"Open the {noun} tab")
        self.parts_menu.delete(0, "end")
        for part in tab.parts if tab else []:
            self.parts_menu.add_command(label=part.name, command=lambda index=part.index: self.open_songsterr_part(index))
        self.parts_cascade.configure(state="normal" if tab and tab.parts else "disabled")
        self.entries["source"].configure(text=f"Source MIDI folder…   ({short_path(self.source_folder())})")
        self.entries["final"].configure(text=f"Final MIDI folder…   ({short_path(self.output_folder())})")

    def on_text_scale(self) -> None:
        value = int(round(self.text_scale.get() / 5) * 5)
        if value != self.text_scale.get():
            self.text_scale.set(value)
        if value == self._applied_scale:
            return
        self._applied_scale = value
        scale = value / 100
        scale_fonts(self.root, scale)
        self.scale_label["text"] = f"{value}%"
        self.settings["textScale"] = scale
        if self._scale_job is not None:
            self.root.after_cancel(self._scale_job)
        self._scale_job = self.root.after(150, lambda: self.chart.set_scale(scale))

    def step_text_scale(self, step: int) -> None:
        low, high = TEXT_SCALES
        value = 100 if step == 0 else max(low, min(high, self.text_scale.get() + step))
        self.text_scale.set(value)
        self.on_text_scale()

    def on_wheel(self, event: tk.Event) -> None:
        if event.state & 0x0004:
            return
        widget = self.root.winfo_containing(event.x_root, event.y_root)
        if self.right_area.contains(widget):
            self.right_area.scroll(-1 if event.delta > 0 else 1)

    def show_tips(self) -> None:
        messagebox.showinfo(
            "Chart and keyboard tips",
            "Chart\n"
            "• On the bars or the section names above them: click to pick a bar, drag to select bars, right-drag to add silent bars.\n"
            "• Play or Space starts from the selected bar. Double-click a bar to play from it.\n"
            "• Below the bars: click to clear the selection, drag to pan.\n"
            "• Mouse wheel over the chart pans left and right. Shift + wheel zooms at the pointer.\n\n"
            "Keys\n"
            "• Space plays and pauses.\n"
            "• Left and Right move the loop; Shift resizes it.\n"
            "• Delete removes the selected bars.\n"
            "• Ctrl+Z and Ctrl+Y undo and redo.\n\n"
            "Ranges\n"
            "• Type start × length into Rhythm Converter.\n"
            "• The intro and ending play once. Patterns and fills loop.",
            parent=self.root,
        )

    def ask_fields(self, title: str, fields: list[tuple[str, str]], note: str = "") -> list[str] | None:
        """A small dark dialog with one entry per field. None if cancelled."""
        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.configure(bg=INK)
        dialog.transient(self.root)
        dialog.resizable(False, False)
        dark_titlebar(dialog)
        frame = ttk.Frame(dialog, padding=16)
        frame.pack(fill="both", expand=True)
        if note:
            ttk.Label(frame, text=note, style="Muted.TLabel", wraplength=400, justify="left").pack(anchor="w", pady=(0, 10))
        entries = []
        for label, value in fields:
            ttk.Label(frame, text=label, style="Section.TLabel").pack(anchor="w")
            entry = ttk.Entry(frame, width=44)
            entry.insert(0, value)
            entry.pack(fill="x", pady=(2, 8))
            entries.append(entry)
        result: list[str] = []

        def done(_event: object = None) -> None:
            result.extend(entry.get().strip() for entry in entries)
            dialog.destroy()

        row = ttk.Frame(frame)
        row.pack(anchor="e", pady=(4, 0))
        ttk.Button(row, text="Cancel", command=dialog.destroy).pack(side="right")
        ttk.Button(row, text="OK", style="Accent.TButton", command=done).pack(side="right", padx=(0, 8))
        dialog.bind("<Return>", done)
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        entries[0].focus_set()
        entries[0].select_range(0, "end")
        dialog.grab_set()
        self.root.wait_window(dialog)
        return result or None

    def rename_song(self) -> None:
        view = self.view
        if not view:
            return
        answer = self.ask_fields(
            "Song name",
            [("Artist", view["artist"]), ("Song title", view["title"])],
            "Used to find the Songsterr tab and the album cover.",
        )
        if not answer or not answer[1]:
            return
        artist, title = answer[0] or "Unknown", answer[1]
        if (artist, title) == (view["artist"], view["title"]):
            return
        view["artist"], view["title"] = artist, title
        self.heading["text"] = f"{artist} — {title}"
        for entry, value in ((self.artist, artist), (self.title, title)):
            entry.delete(0, "end")
            entry.insert(0, value)
        self.request_songsterr(artist, title)
        self.load_cover(artist, title)
        self.set_status(f"Renamed to {artist} — {title}; looking up Songsterr again.")

    def set_tab_info(self, text: str, full: str = "") -> None:
        self.tab_info["text"] = text
        self.tab_tip["text"] = full if full and full != text else ""

    def source_folder(self) -> Path:
        chosen = self.settings.get("sourceFolder")
        if chosen:
            return Path(chosen)
        return chart.drum_root(self.catalog or load_catalog(chart.ensure_song_list()))

    def output_folder(self) -> Path:
        chosen = self.settings.get("finalFolder")
        return Path(chosen) if chosen else EXPORTS

    def choose_folder(self, key: str, title: str, current: Path) -> Path | None:
        chosen = filedialog.askdirectory(
            parent=self.root, title=title, initialdir=str(current if current.is_dir() else ROOT), mustexist=True
        )
        if not chosen:
            return None
        self.settings[key] = str(Path(chosen))
        try:
            save_settings(self.settings)
        except OSError as exc:
            self.set_status(f"The folder is set for now, but it could not be remembered: {exc}")
        return Path(chosen)

    def choose_source_folder(self) -> None:
        folder = self.choose_folder("sourceFolder", "Folder with your source drum MIDIs", self.source_folder())
        if folder:
            self.refresh_sources()
            count = len(self.file_by_iid)
            self.set_status(f"Source folder: {folder}. {count} file{'s' if count != 1 else ''} not in the song list yet. Click one to parse it.")

    def choose_output_folder(self) -> None:
        folder = self.choose_folder("finalFolder", "Folder for finished RC-10R MIDIs", self.output_folder())
        if folder:
            self.set_status(f"Save MIDI now saves into {folder}.")

    def show_folder(self, folder: Path) -> None:
        try:
            folder.mkdir(parents=True, exist_ok=True)
            os.startfile(folder)
        except OSError as exc:
            self.set_status(f"Could not open {folder}: {exc}")

    def refresh_sources(self) -> None:
        folder = self.source_folder()
        self.source_files = scan_midi(folder) if folder.is_dir() else []
        self.reload_songs()
        if not folder.is_dir():
            self.set_status(f"The source folder is missing: {folder}. Set it under File.")

    def poll_sources(self) -> None:
        try:
            while True:
                folder, files = self.scan_queue.get_nowait()
                if folder == str(self.source_folder()) and files != self.source_files:
                    added = len(set(files) - set(self.source_files))
                    self.source_files = files
                    self.reload_songs()
                    if added:
                        self.set_status(f"Found {added} new MIDI file{'s' if added != 1 else ''} in the source folder.")
        except queue.Empty:
            pass
        if not self.scanning:
            self.scanning = True
            folder = self.source_folder()

            def work() -> None:
                try:
                    files = scan_midi(folder) if folder.is_dir() else []
                except OSError:
                    files = self.source_files
                self.scan_queue.put((str(folder), files))
                self.scanning = False

            threading.Thread(target=work, daemon=True).start()
        self.root.after(SCAN_MS, self.poll_sources)

    def on_quit(self) -> None:
        unsaved = self.unsaved_songs()
        if unsaved and not messagebox.askyesno(
            "Quit",
            f"{unsaved} song{'s have' if unsaved != 1 else ' has'} edits that are not saved as a MIDI file. Quit anyway?",
            parent=self.root,
        ):
            return
        try:
            self.stop()
        except Exception:
            pass
        if self.pedal_window:
            try:
                self.pedal_window.close()
            except Exception:
                pass
        if self.root.state() == "normal":
            self.settings["geometry"] = self.root.geometry()
        try:
            save_settings(self.settings)
        except OSError:
            pass
        self.bridge.close()
        clear_previews()
        self.root.destroy()

    def open_midi_file(self) -> None:
        start = self.source_folder()
        chosen = filedialog.askopenfilename(
            parent=self.root,
            title="Open a MIDI file",
            initialdir=str(start if start.is_dir() else ROOT),
            filetypes=[("MIDI files", "*.mid *.midi *.kar"), ("All files", "*.*")],
        )
        if chosen:
            self.saved_list.selection_set(())
            self.open_source(Path(chosen))

    def open_source(self, path: Path) -> None:
        artist, title = guess_artist_title(path)
        artist = source_artist(path, self.source_folder(), {artist_key(song["artist"]): song["artist"] for song in self.songs})
        if artist == "Other files":
            artist = ""
        self.results.selection_clear(0, "end")
        session = self.sessions.get(session_key(path, True))
        if session is not None and session["stamp"] == file_stamp(path):
            self.show_file(path, artist, title, {}, editable=True, summary="")
        else:
            self.set_status(f"Parsing {path.name}…")
            self.root.update_idletasks()
            try:
                midi = chart.parse_midi(path)
                _hits, _meters, boundaries = chart.build_measures(midi)
                idea = suggest_sections.suggest(midi["events"], boundaries)
            except Exception as exc:
                messagebox.showerror("Open MIDI", f"{path.name} could not be read as a drum MIDI.\n\n{exc}", parent=self.root)
                self.set_status(f"Could not parse {path.name}.")
                return
            self.show_file(path, artist or "Unknown", title, idea.divisions, editable=True, summary=idea.summary, midi=midi)
            self.set_status(f"Parsed {path.name}. If Songsterr finds the wrong tab, fix the name under Edit › Song name….")
        self.artist.delete(0, "end")
        self.artist.insert(0, self.view["artist"] if self.view["artist"] != "Unknown" else "")
        self.title.delete(0, "end")
        self.title.insert(0, self.view["title"])

    def on_use_tab(self) -> None:
        view = self.view
        if not view or not self.tab or not view.get("tab_parts"):
            self.set_status("Songsterr has no sections to use for this song.")
            return
        try:
            divisions, notes = songsterr.divisions_from_sections(view["tab_parts"], len(view["hits"]))
        except ValueError as exc:
            self.set_status(str(exc))
            return
        if divisions == view["divisions"]:
            self.set_status("The ranges already match Songsterr's sections. " + " ".join(notes))
            return
        self.remember("use Songsterr sections")
        view["divisions"] = divisions
        self.show_tab_parts.set(True)
        self.stop()
        self.pedal.reset()
        self.sync_entries()
        self.redraw_chart()
        self.refresh_transport()
        self.update_pedal_display()
        self.set_status("Set from Songsterr. " + " ".join(notes) + " Undo puts the old ranges back.")

    def open_songsterr(self, kind: str = "drums") -> None:
        part = self.link_tab.best_part(kind) if self.link_tab else None
        if part is not None:
            self.open_songsterr_part(part.index)
        else:
            self.search_songsterr()

    def open_songsterr_part(self, index: int) -> None:
        if self.link_tab is not None:
            os.startfile(self.link_tab.part_url(index))

    def search_songsterr(self) -> None:
        if self.songsterr_search:
            os.startfile(self.songsterr_search)

    def on_zoom(self, direction: int) -> None:
        if not self.view or not self.chart.can_zoom(direction):
            return
        self.chart.zoom(direction)
        self.refresh_zoom()

    def refresh_zoom(self) -> None:
        self.refresh_menu()

    def refresh_transport(self) -> None:
        if self.playing and self.playing.get("paused"):
            self.pause_button.configure(text="Resume", state="normal")
        elif self.playing:
            self.pause_button.configure(text="Pause", state="normal")
        else:
            self.pause_button.configure(text="Pause", state="disabled")
        chosen = "normal" if self.selection and self.view else "disabled"
        if self.selection and self.view:
            start, end = self.selection
            self.bars_button["text"] = (f"Bar {start}" if start == end else f"Bars {start}–{end}") + "  ▾"
        else:
            self.bars_button["text"] = "Selected bars  ▾"
        self.delete_button.configure(state=chosen)
        self.blank_button.configure(state=chosen)
        for button in self.assign_buttons:
            button.configure(state=chosen)
        self.export_button.configure(state="normal" if self.view else "disabled")
        self.refresh_history()

    def cancel_tick(self) -> None:
        if self.after_id:
            self.root.after_cancel(self.after_id)
            self.after_id = None

    def stop(self, drop_audio: bool = False) -> None:
        self.play_token += 1
        self.preparing = False
        self.playing = None
        self.pending_start = None
        self.pending_follow = False
        self.cancel_tick()
        close_preview()
        self.device_open = False
        winsound.PlaySound(None, winsound.SND_PURGE)
        self.chart.hide_playhead()
        self.pedal.reset()
        self.pedal.set_clock(None)
        self.update_pedal_display()
        if drop_audio:
            self.rendered_generation = None
        self.refresh_transport()

    def on_add(self) -> None:
        if not self.view or not self.view["editable"]:
            return
        try:
            divisions = self.current_divisions()
        except ValueError as exc:
            self.set_status(str(exc))
            return
        self.view["divisions"] = divisions
        artist = self.view["artist"]
        title = self.view["title"]
        typed = "\n".join(typing_lines(divisions))
        if self.view.get("bars_changed") and not messagebox.askyesno(
            "Bar edits are not in the file",
            "You deleted, blanked, or added bars. Add to song list keeps the original MIDI, so these ranges "
            "would point at the original bars.\n\n"
            "To keep the edits, use Save MIDI for RC-10R instead. Add the original file anyway?",
        ):
            self.set_status("Not added.")
            return
        if not messagebox.askyesno(
            "Add to song list", f"Add {artist} — {title} with these ranges?\n\n{typed}", parent=self.root
        ):
            self.set_status("Not added.")
            return
        self.set_busy(True)
        self.set_status("Adding to the song list…")
        note = ""
        path = self.view["path"]
        threading.Thread(
            target=self.add_worker,
            args=(path, artist, title, divisions, note),
            daemon=True,
        ).start()

    def add_worker(self, path: Path, artist: str, title: str, divisions: dict, note: str) -> None:
        try:
            report = add_to_list(path, artist, title, divisions, note)
            error = ""
        except Exception as exc:
            report, error = "", str(exc)
        song = song_id(title)
        self.from_worker(lambda: self.finish_add(report, error, song))

    def finish_add(self, report: str, error: str, song: str) -> None:
        self.set_busy(False)
        if error:
            self.set_status(error)
            return
        self.reload_songs()
        saved = next((item for item in self.songs if item["id"] == song), None)
        if saved is not None:
            self.forget_session(chart.drum_root(self.catalog) / saved["file"])
        iid = self.song_iids.get(song)
        if iid:
            self.saved_list.selection_set(iid)
            self.saved_list.focus(iid)
            self.saved_list.see(iid)
            self.on_saved()
        self.set_status(report.splitlines()[0] + "  Type the ranges into Rhythm Converter.")

    def copy_ranges(self) -> None:
        if not self.view:
            return
        try:
            divisions = self.current_divisions() if self.view["editable"] else self.view["divisions"]
        except ValueError as exc:
            self.set_status(str(exc))
            return
        lines = typing_lines(divisions)
        kit = self.view.get("kit")
        if kit:
            lines.insert(0, f"RC-10R kit: {kit}")
        text = "\n".join(lines)
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.set_status("Ranges copied.")

    def export_data(self, mode: str) -> tuple[bytes, dict[str, list[int]], int]:
        view = self.view
        name = f"{view['artist']} - {view['title']} drums"
        if mode == "parts":
            events, tempos, bounds, meters, divisions = midi_export.pedal_parts(
                view["ticks"], view["tempos"], view["bounds"], view["meters"], view["divisions"]
            )
        else:
            events, tempos, bounds, meters = view["ticks"], view["tempos"], view["bounds"], view["meters"]
            divisions = {key: list(span) for key, span in view["divisions"].items()}
        data = midi_export.build(view["ppq"], events, tempos, bounds, meters, name)
        return data, divisions, len(bounds) - 1

    def open_export(self) -> None:
        view = self.view
        if not view:
            self.set_status("Pick a saved song or download a file first.")
            return
        try:
            if view["editable"]:
                view["divisions"] = self.current_divisions()
        except ValueError as exc:
            self.set_status(str(exc))
            return
        count = len(view["hits"])
        for key, label, _fill in DIVISIONS:
            start, end = view["divisions"][key]
            if start < 1 or end > count or end < start:
                self.set_status(f"{label} does not fit this file. Fix it before saving.")
                return
        dialog = tk.Toplevel(self.root)
        dialog.title("Save MIDI for the RC-10R")
        dialog.configure(bg=INK)
        dialog.transient(self.root)
        dialog.resizable(False, False)
        dark_titlebar(dialog)
        frame = ttk.Frame(dialog, padding=18)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Save MIDI for the RC-10R", style="Song.TLabel").pack(anchor="w")
        ttk.Label(
            frame,
            text="This writes a type 0 drum MIDI with your edits (bars and timing). "
            "Open it in Rhythm Converter and type the ranges below.",
            style="Muted.TLabel",
            wraplength=520,
            justify="left",
        ).pack(anchor="w", pady=(4, 12))
        mode = tk.StringVar(value="song")
        ttk.Radiobutton(frame, text="The whole song, with your edits", variable=mode, value="song").pack(anchor="w")
        ttk.Radiobutton(
            frame, text="Only the pedal parts, back to back (a shorter file)", variable=mode, value="parts"
        ).pack(anchor="w", pady=(4, 0))
        preview = ttk.Label(frame, text="", font=REPORT_FONT, justify="left")
        preview.pack(anchor="w", pady=(12, 0))

        source = Path(view["path"])
        locked = chart.is_locked(view.get("song_id") or "", self.catalog or {}) and view.get("bars_changed")
        keep = tk.StringVar(value="keep")
        ttk.Label(frame, text="The original file", style="Section.TLabel").pack(anchor="w", pady=(16, 0))
        ttk.Label(frame, text=str(source), style="Muted.TLabel", wraplength=520, justify="left").pack(anchor="w")
        keep_button = ttk.Radiobutton(frame, text="Keep it, and save the edited copy somewhere else", variable=keep, value="keep")
        keep_button.pack(anchor="w", pady=(6, 0))
        replace_button = ttk.Radiobutton(
            frame,
            text="Replace it with the edited file (the old one goes to the Recycle Bin)",
            variable=keep,
            value="replace",
        )
        replace_button.pack(anchor="w", pady=(4, 0))
        replace_note = ttk.Label(frame, text="", style="Muted.TLabel", wraplength=520, justify="left")
        replace_note.pack(anchor="w", pady=(4, 0))

        def refresh(*_args: object) -> None:
            _data, divisions, bars = self.export_data(mode.get())
            preview["text"] = f"{bars} bars\n" + "\n".join(typing_lines(divisions))
            reason = ""
            if mode.get() == "parts":
                reason = "Only the whole song can replace the original."
            elif not source.is_file():
                reason = "The original file is missing."
            elif locked:
                reason = "This song's ranges are locked, and your bar edits would move them. Keep the original instead."
            if reason:
                keep.set("keep")
                replace_button.state(["disabled"])
            else:
                replace_button.state(["!disabled"])
                if view.get("song_id") and view.get("bars_changed"):
                    reason = "Your bar edits move bar numbers, so the saved ranges for this song may need fixing afterwards."
            replace_note["text"] = reason

        mode.trace_add("write", refresh)
        refresh()
        row = ttk.Frame(frame)
        row.pack(anchor="e", pady=(16, 0))

        def save() -> None:
            choice = mode.get()
            data, divisions, _bars = self.export_data(choice)
            final = self.output_folder()
            try:
                final.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                messagebox.showerror("Save MIDI", f"The final MIDI folder could not be made.\n\n{exc}", parent=dialog)
                return
            suffix = "RC-10R pedal parts" if choice == "parts" else "RC-10R"
            filename = safe_name(f"{view['artist']} - {view['title']} ({suffix}).mid", "drums.mid")
            if keep.get() == "replace":
                if not messagebox.askyesno(
                    "Replace the original",
                    f"Replace {source.name} with the edited drums?\n\nThe old file goes to the Recycle Bin, so you can still restore it.",
                    parent=dialog,
                ):
                    return
                path = source
                notes_path = final / Path(filename).with_suffix(".txt").name
                try:
                    to_recycle_bin(source)
                except OSError as exc:
                    messagebox.showerror("Replace the original", str(exc), parent=dialog)
                    return
            else:
                chosen = filedialog.asksaveasfilename(
                    parent=dialog,
                    initialdir=str(final),
                    initialfile=filename,
                    defaultextension=".mid",
                    filetypes=[("MIDI file", "*.mid")],
                )
                if not chosen:
                    return
                path = Path(chosen)
                notes_path = path.with_suffix(".txt")
            try:
                midi_export.write(path, data)
                lines = typing_lines(divisions)
                notes = [f"{view['artist']} — {view['title']}", f"RC-10R kit: {view.get('kit') or '?'}", f"Tempo: {view['tempo']}", ""]
                notes_path.write_text("\n".join(notes + lines) + "\n", encoding="utf-8")
            except OSError as exc:
                messagebox.showerror("Save MIDI", f"The file could not be saved.\n\n{exc}", parent=dialog)
                return
            dialog.destroy()
            self.dirty = False
            typed = "\n".join(typing_lines(divisions))
            if path == source:
                self.set_status(f"Replaced {path.name} with the edited drums. The old file is in the Recycle Bin. Ranges are in {notes_path}.")
            else:
                self.set_status(f"Saved {path}. The ranges are in {notes_path.name} next to it.")
            if not RHYTHM_CONVERTER.is_file():
                messagebox.showinfo("Saved", f"Saved {path.name}.\n\nIn Rhythm Converter, open it and type:\n{typed}")
                subprocess.Popen(["explorer", "/select,", str(path)])
                return
            if messagebox.askyesno(
                "Saved",
                f"Saved {path.name}.\n\nIn Rhythm Converter, open this file and type:\n{typed}\n\n"
                "Open Rhythm Converter and show the file now?",
            ):
                os.startfile(str(RHYTHM_CONVERTER))
                subprocess.Popen(["explorer", "/select,", str(path)])

        ttk.Button(row, text="Cancel", command=dialog.destroy).pack(side="right")
        ttk.Button(row, text="Save…", style="Accent.TButton", command=save).pack(side="right", padx=(0, 8))
        dialog.grab_set()

    def run(self) -> None:
        self.root.mainloop()


def shift_span(start: int, end: int, cut_start: int, cut_end: int) -> tuple[int, int] | None:
    """Move a bar span after bars cut_start through cut_end are removed."""
    if end < cut_start:
        return start, end
    count = cut_end - cut_start + 1
    if start > cut_end:
        return start - count, end - count
    if start >= cut_start and end <= cut_end:
        return None
    if start < cut_start and end > cut_end:
        return start, end - count
    if start < cut_start:
        return start, cut_start - 1
    return cut_start, end - count


def place_span(start: int, end: int, cut_start: int, cut_end: int, new_length: int) -> tuple[int, int]:
    shifted = shift_span(start, end, cut_start, cut_end)
    if shifted is None or new_length < 1:
        bar = min(max(new_length, 1), max(1, cut_start))
        return bar, bar
    left, right = shifted
    left = min(max(1, left), new_length)
    right = min(max(left, right), new_length)
    return left, right


def remove_bars(
    hits: list[int],
    bar_ms: list[int],
    events: list,
    divisions: dict,
    parts: list,
    cut_start: int,
    cut_end: int,
) -> tuple[list[int], list[int], list, dict, list]:
    """Drop bars cut_start through cut_end and close the gap. Bars are numbered from 1."""
    if cut_end < cut_start:
        cut_start, cut_end = cut_end, cut_start
    gap = bar_ms[cut_end] - bar_ms[cut_start - 1]
    cut_a = bar_ms[cut_start - 1]
    cut_b = bar_ms[cut_end]
    new_hits = hits[: cut_start - 1] + hits[cut_end:]
    new_bar_ms = list(bar_ms[:cut_start]) + [tick - gap for tick in bar_ms[cut_end + 1 :]]
    new_events = []
    for ms, note, velocity in events:
        if cut_a <= ms < cut_b:
            continue
        if ms >= cut_b:
            ms -= gap
        new_events.append([ms, note, velocity])
    new_length = len(new_hits)
    new_divisions = {
        key: list(place_span(span[0], span[1], cut_start, cut_end, new_length))
        for key, span in divisions.items()
    }
    new_parts = []
    for start, end, name in parts:
        shifted = shift_span(start, end, cut_start, cut_end)
        if shifted is None:
            continue
        left, right = shifted
        if right < 1 or left > new_length:
            continue
        left = min(max(1, left), new_length)
        right = min(max(left, right), new_length)
        new_parts.append((left, right, name))
    return new_hits, new_bar_ms, new_events, new_divisions, new_parts


def silence_bars(
    hits: list[int],
    bar_ms: list[int],
    events: list,
    cut_start: int,
    cut_end: int,
) -> tuple[list[int], list]:
    """Clear the hits in bars cut_start through cut_end. Bar times stay put."""
    if cut_end < cut_start:
        cut_start, cut_end = cut_end, cut_start
    cut_a = bar_ms[cut_start - 1]
    cut_b = bar_ms[cut_end]
    new_hits = list(hits)
    for index in range(cut_start - 1, cut_end):
        new_hits[index] = 0
    new_events = [event for event in events if not (cut_a <= event[0] < cut_b)]
    return new_hits, new_events


def insert_bars(
    hits: list[int],
    bar_ms: list[int],
    events: list,
    divisions: dict,
    parts: list,
    at: int,
    count: int,
) -> tuple[list[int], list[int], list, dict, list]:
    """Insert silent bars at bar `at`. Later notes move later by the same amount of time."""
    if count < 1:
        return hits, bar_ms, events, divisions, parts
    at = max(1, min(at, len(hits) + 1))
    if at <= len(hits):
        duration = bar_ms[at] - bar_ms[at - 1]
    else:
        duration = bar_ms[-1] - bar_ms[-2]
    duration = max(int(duration), 1)
    gap = duration * count
    anchor = bar_ms[at - 1] if at <= len(hits) else bar_ms[-1]
    if at <= len(hits):
        new_hits = hits[: at - 1] + [0] * count + hits[at - 1 :]
        new_bar_ms = (
            list(bar_ms[:at])
            + [anchor + duration * step for step in range(1, count)]
            + [ms + gap for ms in bar_ms[at - 1 :]]
        )
    else:
        new_hits = list(hits) + [0] * count
        new_bar_ms = list(bar_ms) + [anchor + duration * step for step in range(1, count + 1)]
    new_events = []
    for ms, note, velocity in events:
        if ms >= anchor:
            ms += gap
        new_events.append([ms, note, velocity])
    new_length = len(new_hits)

    def move_span(start: int, end: int) -> tuple[int, int]:
        if end < at:
            left, right = start, end
        elif start >= at:
            left, right = start + count, end + count
        else:
            left, right = start, end + count
        left = min(max(1, left), new_length)
        right = min(max(left, right), new_length)
        return left, right

    new_divisions = {key: list(move_span(span[0], span[1])) for key, span in divisions.items()}
    new_parts = []
    for start, end, name in parts:
        left, right = move_span(start, end)
        new_parts.append((left, right, name))
    return new_hits, new_bar_ms, new_events, new_divisions, new_parts


def bar_index(bar_ms: list[int], position: float) -> int:
    index = 0
    for cursor in range(len(bar_ms) - 1):
        if bar_ms[cursor + 1] <= position:
            index = cursor + 1
        else:
            break
    if index >= len(bar_ms) - 1:
        index = max(0, len(bar_ms) - 2)
    return index


if __name__ == "__main__":
    Finder().run()
