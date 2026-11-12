"""Find a free MIDI on MIDIfind or MIDIWORLD, then read its drum kit.

The saved song list opens in this window with the section chart. A new
download gets a suggested intro, two loops, two fills, and an ending.
You still type those numbers into Rhythm Converter.

Run:
  python scripts/find_drum_midi.py
"""

from __future__ import annotations

import ctypes
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
from tkinter import messagebox, ttk

sys.path.insert(0, str(Path(__file__).resolve().parent))
import album_art
import build_section_chart as chart
import suggest_sections
from preview_ui import ROLE, SectionChart, render_section

ROOT = Path(__file__).resolve().parents[1]
ARTWORK = ROOT / "artwork"
ICON = ROOT / "assets" / "drum-finder.ico"
INCOMING = ROOT / "incoming"
SITES = ("MIDIfind", "MIDIWORLD")
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
MUTED = "#9b978e"
ACCENT = "#e4a24a"
ACCENT_INK = "#1c1408"
HOVER = "#2c313c"
ACCENT_HOVER = "#f0b35c"
DISABLED = "#2a2d34"
DISABLED_TEXT = "#6e6a62"
UI_FONT = ("Segoe UI", 10)
TITLE_FONT = ("Segoe UI", 18)
REPORT_FONT = ("Cascadia Mono", 10)

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


@dataclass
class KitInfo:
    measures: int
    kit_notes: int
    tempo: str
    meter: str
    kit_names: list[str]
    tracks: list[str]
    preview: str
    notes: list[str]


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
    hits.sort(key=lambda hit: rank(hit.title, artist, song), reverse=True)
    return hits, " ".join(notes)


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


def restart_preview() -> None:
    audio_command("seek drum to start")
    audio_command("play drum")


def close_preview() -> None:
    audio_command("stop drum", optional=True)
    audio_command("close drum", optional=True)


_MEDIA_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$WarningPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
})[0]
function Await([object]$WinRtTask, [type]$ResultType) {
    $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
    $netTask = $asTask.Invoke($null, @($WinRtTask))
    $netTask.Wait(-1) | Out-Null
    $netTask.Result
}
function Clean([string]$Text) {
    if (-not $Text) { return '' }
    return ($Text -replace '[|\r\n]', ' ').Trim()
}
function Reply([string]$Line) {
    [Console]::Out.WriteLine($Line)
    [Console]::Out.Flush()
}
$null = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager, Windows.Media.Control, ContentType = WindowsRuntime]
$mgr = Await ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]::RequestAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager])
while ($true) {
    $line = [Console]::In.ReadLine()
    if ($null -eq $line -or $line -eq 'quit') { break }
    $bits = $line.Split(' ', 2)
    $action = $bits[0]
    $ms = [int64]0
    if ($bits.Length -gt 1) { [void][int64]::TryParse($bits[1], [ref]$ms) }
    try {
        $session = $mgr.GetCurrentSession()
        if (-not $session) { Reply 'none'; continue }
        $info = $session.GetPlaybackInfo()
        $status = [string]$info.PlaybackStatus
        $canSeek = [bool]$info.Controls.IsPlaybackPositionEnabled
        $seekNote = $(if ($canSeek) { 'seek' } else { 'noseek' })
        if ($action -eq 'pause') {
            if ($status -eq 'Playing') {
                $null = Await ($session.TryPauseAsync()) ([bool])
                $status = 'Paused'
            }
        } elseif ($action -eq 'play') {
            if ($status -ne 'Playing') {
                $null = Await ($session.TryPlayAsync()) ([bool])
                $status = 'Playing'
            }
            if ($canSeek) {
                $timeline = $session.GetTimelineProperties()
                $target = [int64]$timeline.StartTime.Ticks + ($ms * 10000)
                $end = [int64]$timeline.EndTime.Ticks
                if ($end -gt $timeline.StartTime.Ticks -and $target -ge $end) { $target = $end - 1000000 }
                if ($target -lt $timeline.StartTime.Ticks) { $target = [int64]$timeline.StartTime.Ticks }
                $moved = Await ($session.TryChangePlaybackPositionAsync($target)) ([bool])
                if (-not $moved) { $seekNote = 'noseek' }
            }
        }
        $props = Await ($session.TryGetMediaPropertiesAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties])
        $app = Clean ([string]$session.SourceAppUserModelId)
        $artist = Clean ([string]$props.Artist)
        $title = Clean ([string]$props.Title)
        Reply "$status|$app|$artist|$title|$seekNote"
    } catch {
        Reply ("error|" + (Clean $_.Exception.Message))
    }
}
"""


class MediaBridge:
    """One PowerShell session that can play, pause, and seek the open player."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._lines: queue.Queue[str] = queue.Queue()
        self._proc: subprocess.Popen[bytes] | None = None

    def command(self, action: str, ms: int = 0) -> str:
        with self._lock:
            self._ensure()
            assert self._proc is not None and self._proc.stdin is not None
            self._proc.stdin.write(f"{action} {max(0, int(ms))}\n".encode("ascii"))
            self._proc.stdin.flush()
            try:
                return self._lines.get(timeout=12)
            except queue.Empty:
                self._close()
                raise RuntimeError("The player did not respond.") from None

    def _ensure(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            return
        script = Path(os.environ.get("TEMP", ".")) / "miditool-media-session.ps1"
        script.write_text(_MEDIA_SCRIPT, encoding="utf-8")
        self._lines = queue.Queue()
        self._proc = subprocess.Popen(
            ["powershell", "-NoProfile", "-STA", "-File", str(script)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        for raw in proc.stdout:
            self._lines.put(raw.decode("utf-8", "replace").strip())

    def _close(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        if proc.stdin is not None:
            try:
                proc.stdin.close()
            except OSError:
                pass
        proc.kill()


_MEDIA = MediaBridge()


def control_media(action: str, ms: int = 0) -> str:
    """Play, pause, or seek the current Spotify, YouTube, or other player session."""
    return _MEDIA.command(action, ms)


def media_caption(line: str) -> str:
    if not line or line == "none":
        return "Open the song in Spotify, YouTube, or another player, then press play."
    status, app, artist, title, seek = (line.split("|", 4) + ["", "", "", "", ""])[:5]
    app_name = Path(app).stem or "Player"
    lowered = app_name.lower()
    if "spotify" in lowered:
        app_name = "Spotify"
    elif "chrome" in lowered or "msedge" in lowered or "firefox" in lowered or "youtube" in lowered:
        app_name = "YouTube" if "youtube" in f"{lowered} {title.lower()}" else app_name
    name = " — ".join(part for part in (artist.strip(), title.strip()) if part) or "the current song"
    lead = "Playing along with" if status == "Playing" else "Ready with"
    if seek == "seek":
        tail = "It jumps to the same moment as the drums."
    elif seek == "noseek":
        tail = "This player can play and pause with the drums, but it cannot jump to a bar."
    else:
        tail = ""
    return f"{lead} {app_name}: {name}. {tail}".strip()


def songsterr_slug(text: str) -> str:
    cleaned = "".join(char.lower() if char.isalnum() else "-" for char in text)
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-") or "song"


def lookup_songsterr(artist: str, title: str) -> tuple[str, str]:
    """Return a label and the Songsterr tab URL for this song."""
    query = " ".join(part for part in (artist.strip(), title.strip()) if part)
    search_url = "https://www.songsterr.com/?pattern=" + urllib.parse.quote(query)
    request = urllib.request.Request(
        "https://www.songsterr.com/api/songs?pattern=" + urllib.parse.quote(query),
        headers={"User-Agent": BROWSER},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            songs = json.loads(response.read().decode("utf-8"))
    except (OSError, json.JSONDecodeError, urllib.error.URLError):
        return f"Songsterr search · {query}", search_url
    if not isinstance(songs, list) or not songs:
        return f"Songsterr search · {query}", search_url
    song = songs[0]
    song_id = song.get("songId")
    if not song_id:
        return f"Songsterr search · {query}", search_url
    found_artist = str(song.get("artist") or artist).strip()
    found_title = str(song.get("title") or title).strip()
    tracks = song.get("tracks") or []
    drum_index = next((index for index, track in enumerate(tracks) if str(track.get("instrument", "")).lower() == "drums"), None)
    slug = f"{songsterr_slug(found_artist)}-{songsterr_slug(found_title)}-tab-s{song_id}"
    if drum_index is None:
        url = f"https://www.songsterr.com/a/wsa/{slug}"
        label = f"Songsterr tab · {found_artist} — {found_title}"
    else:
        url = f"https://www.songsterr.com/a/wsa/{slug}t{drum_index}"
        label = f"Songsterr drum tab · {found_artist} — {found_title}"
    return label, url


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    number = 2
    while True:
        candidate = path.with_name(f"{path.stem} ({number}){path.suffix}")
        if not candidate.exists():
            return candidate
        number += 1


def read_kit(path: Path) -> KitInfo:
    midi = chart.parse_midi(path)
    bar_hits, meters, _boundaries = chart.build_measures(midi)
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
    tracks: list[str] = []
    for track in midi["tracks"]:
        if not track["notes"]:
            continue
        name = track["name"] or "(unnamed)"
        channels = ", ".join(str(channel + 1) for channel in track["channels"]) or "none"
        mark = "kit" if chart.is_kit_track(track["name"]) else "not the kit"
        tracks.append(f"  {name}: {track['notes']} notes, channels {channels}, {mark}")
    preview = " ".join(str(count) for count in bar_hits[:24])
    if len(bar_hits) > 24:
        preview += f" … {len(bar_hits)} bars"
    return KitInfo(
        measures=len(bar_hits),
        kit_notes=len(midi["events"]),
        tempo=chart.tempo_text(midi["tempos"]),
        meter=chart.meter_text(meters),
        kit_names=[track["name"] or "(unnamed)" for track in kit_named],
        tracks=tracks,
        preview=preview or "(none)",
        notes=notes,
    )


def describe(path: Path, hit: Hit) -> str:
    info = read_kit(path)
    lines = [
        f"{hit.site}: {hit.title}",
        f"Saved: {path}",
        f"{info.tempo} · {info.meter} · {info.measures} measures",
        f"Drum-kit notes: {info.kit_notes}",
    ]
    if info.kit_names:
        lines.append("Kit track: " + ", ".join(info.kit_names))
    lines.extend(info.notes)
    lines.extend(["", "Tracks", *info.tracks, "", "Kit hits by bar", info.preview])
    lines.extend(
        [
            "",
            "Click Add to chart when you know the intro, pattern, and ending bars.",
            "That copies this file into the drum folder, adds it to the song list, and rebuilds the chart.",
        ]
    )
    return "\n".join(lines)


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


def rebuild_chart(catalog_path: Path, chart_path: Path) -> None:
    catalog = load_catalog(catalog_path)
    root = Path(catalog["drumRoot"])
    blocks = []
    for song in catalog["songs"]:
        path = root / song["file"]
        if not path.is_file():
            raise ValueError(f"Missing MIDI file: {path}")
        blocks.append(chart.render_song(song, path))
    chart_path.parent.mkdir(parents=True, exist_ok=True)
    chart_path.write_text(chart.render_page(catalog, "\n".join(blocks)), encoding="utf-8")


def add_to_list(
    src: Path,
    artist: str,
    title: str,
    divisions: dict[str, list[int]],
    extra_note: str,
    *,
    catalog_path: Path = chart.MAP_PATH,
    chart_path: Path = chart.OUT_PATH,
    drum_root: Path | None = None,
) -> str:
    artist = artist.strip()
    title = title.strip()
    if not artist or not title:
        raise ValueError("Enter the artist and the song title.")
    slug = song_id(title)
    if slug in chart.EXPECTED:
        raise ValueError(
            f"{title} is already on the chart with fixed ranges. This finder will not replace it."
        )
    previous = catalog_path.read_bytes()
    catalog = json.loads(previous.decode("utf-8"))
    root = drum_root or Path(catalog["drumRoot"])
    info = read_kit(src)
    relative, dest, created = place_midi(src, artist, root)
    notes = list(info.notes)
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
        rebuild_chart(catalog_path, chart_path)
    except Exception:
        catalog_path.write_bytes(previous)
        if created and dest.exists():
            dest.unlink()
        raise
    verb = "Replaced" if replaced else "Added"
    return (
        f"{verb} {artist} — {title} on the chart.\n"
        f"File: {dest}\n"
        + "\n".join(typing_lines(divisions))
    )


def apply_theme(root: tk.Tk) -> None:
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", background=INK, foreground=TEXT, font=UI_FONT)
    style.configure("TFrame", background=INK)
    style.configure("TLabel", background=INK, foreground=TEXT)
    style.configure("Title.TLabel", font=TITLE_FONT, background=INK, foreground=TEXT)
    style.configure("Muted.TLabel", background=INK, foreground=MUTED)
    style.configure("Section.TLabel", background=INK, foreground=MUTED, font=("Segoe UI", 9))
    style.configure("Song.TLabel", background=INK, foreground=TEXT, font=("Segoe UI", 16))
    style.configure(
        "TEntry",
        fieldbackground=FIELD,
        foreground=TEXT,
        bordercolor=LINE,
        lightcolor=LINE,
        darkcolor=LINE,
        insertcolor=TEXT,
        padding=8,
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
        padding=(14, 8),
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
        padding=(16, 8),
    )
    style.map(
        "Accent.TButton",
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
        rowheight=26,
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


def styled_text(parent: tk.Misc, height: int) -> tk.Text:
    return tk.Text(
        parent,
        height=height,
        wrap="word",
        font=REPORT_FONT,
        bg=FIELD,
        fg=TEXT,
        insertbackground=TEXT,
        selectbackground="#3a4254",
        selectforeground=TEXT,
        relief="flat",
        borderwidth=0,
        highlightthickness=1,
        highlightbackground=LINE,
        highlightcolor=ACCENT,
        padx=12,
        pady=10,
    )


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
        self.root.geometry("1180x840")
        self.root.minsize(980, 680)
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
        self.with_song = tk.BooleanVar(value=False)
        self.media_token = 0
        self.songsterr_token = 0
        self.songsterr_url = ""
        self.songsterr_queue: queue.Queue[tuple[int, str, str]] = queue.Queue()
        self.loop_start = tk.StringVar()
        self.loop_bars = tk.StringVar()
        self._selecting = False
        self.rendered_generation: int | None = None
        self.device_open = False
        self.pending_start: tuple[int, str, tuple[int, int] | None] | None = None
        self.cover_token = 0
        self.cover_photo: tk.PhotoImage | None = None
        self.cover_queue: queue.Queue[tuple[int, tuple[Path, str] | None]] = queue.Queue()
        self.after_id: str | None = None
        self._loading = False
        self.starts: dict[str, tk.StringVar] = {}
        self.lengths: dict[str, tk.StringVar] = {}

        frame = ttk.Frame(self.root, padding=(20, 16))
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(3, weight=1)

        ttk.Label(frame, text="Drum MIDI finder", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            frame,
            text="Saved songs are on the left, in a folder for each artist. Drag the divider to read the full names. A new download suggests a loop for the RC-10R. You still type the numbers into Rhythm Converter.",
            style="Muted.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(2, 12))

        search = ttk.Frame(frame)
        search.grid(row=2, column=0, sticky="ew", pady=(0, 12))
        search.columnconfigure(1, weight=1)
        search.columnconfigure(3, weight=1)
        ttk.Label(search, text="Artist", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        self.artist = ttk.Entry(search)
        self.artist.grid(row=0, column=1, sticky="ew", padx=(8, 16))
        ttk.Label(search, text="Song title", style="Section.TLabel").grid(row=0, column=2, sticky="w")
        self.title = ttk.Entry(search)
        self.title.grid(row=0, column=3, sticky="ew", padx=(8, 16))
        self.search_button = ttk.Button(search, text="Search", style="Accent.TButton", command=self.on_search)
        self.search_button.grid(row=0, column=4, sticky="e")
        self.use_midifind = tk.BooleanVar(value=True)
        self.use_midiworld = tk.BooleanVar(value=True)
        sites = ttk.Frame(search)
        sites.grid(row=1, column=1, columnspan=3, sticky="w", pady=(8, 0))
        ttk.Checkbutton(sites, text="MIDIfind.com", variable=self.use_midifind).pack(side="left")
        ttk.Checkbutton(sites, text="MIDIWORLD.com", variable=self.use_midiworld).pack(side="left", padx=(16, 0))
        self.songsterr_link = tk.Label(
            search,
            text="",
            bg=INK,
            fg=ACCENT,
            cursor="hand2",
            font=("Segoe UI", 10, "underline"),
            anchor="w",
        )
        self.songsterr_link.grid(row=2, column=1, columnspan=4, sticky="w", pady=(8, 0))
        self.songsterr_link.bind("<Button-1>", self.open_songsterr)

        body = tk.PanedWindow(
            frame,
            orient=tk.HORIZONTAL,
            bg=LINE,
            sashwidth=8,
            sashrelief=tk.FLAT,
            bd=0,
            opaqueresize=True,
        )
        body.grid(row=3, column=0, sticky="nsew")

        left = ttk.Frame(body, padding=(0, 0, 12, 0))
        left.rowconfigure(1, weight=3)
        left.rowconfigure(3, weight=2)
        left.columnconfigure(0, weight=1)
        self.saved_label = ttk.Label(left, text="Saved songs", style="Section.TLabel")
        self.saved_label.grid(row=0, column=0, sticky="w", pady=(0, 6))
        saved_holder, self.saved_list = scroll_around(left, lambda parent: styled_tree(parent))
        saved_holder.grid(row=1, column=0, sticky="nsew")
        self.saved_list.bind("<<TreeviewSelect>>", self.on_saved)
        ttk.Label(left, text="Search results", style="Section.TLabel").grid(row=2, column=0, sticky="w", pady=(14, 6))
        result_holder, self.results = scroll_around(left, lambda parent: styled_list(parent))
        self.results.configure(width=28)
        result_holder.grid(row=3, column=0, sticky="nsew")
        self.results.bind("<<ListboxSelect>>", lambda _event: self.refresh_buttons())
        self.download_button = ttk.Button(left, text="Download and parse", command=self.on_download)
        self.download_button.grid(row=4, column=0, sticky="ew", pady=(10, 0))

        right = ttk.Frame(body, padding=(12, 0, 0, 0))
        body.add(left, minsize=220, width=320, stretch="never")
        body.add(right, minsize=680, stretch="always")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(3, weight=1)
        self.heading = ttk.Label(right, text="Pick a saved song", style="Song.TLabel")
        self.heading.grid(row=0, column=0, sticky="w")
        self.meta = ttk.Label(right, text="", style="Muted.TLabel")
        self.meta.grid(row=1, column=0, sticky="w", pady=(2, 0))
        transport = ttk.Frame(right)
        transport.grid(row=2, column=0, sticky="ew", pady=(10, 8))
        buttons = ttk.Frame(transport)
        buttons.pack(anchor="w")
        self.play_all = ttk.Button(buttons, text="Play drums", command=lambda: self.play("all"))
        self.play_all.pack(side="left")
        self.pause_button = ttk.Button(buttons, text="Pause", command=self.on_pause_button)
        self.pause_button.pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="Stop", command=self.on_stop).pack(side="left", padx=(8, 0))
        self.zoom_out_button = ttk.Button(buttons, text="Zoom out", command=lambda: self.on_zoom(-1))
        self.zoom_out_button.pack(side="left", padx=(16, 0))
        self.zoom_in_button = ttk.Button(buttons, text="Zoom in", command=lambda: self.on_zoom(1))
        self.zoom_in_button.pack(side="left", padx=(8, 0))
        ttk.Checkbutton(
            buttons,
            text="Play the song too",
            variable=self.with_song,
            command=self.on_song_toggle,
        ).pack(side="left", padx=(16, 0))
        self.song_player = ttk.Label(
            transport,
            text="Off. Turn this on to play and pause Spotify, YouTube, or another open player in time with the drums.",
            style="Muted.TLabel",
            wraplength=720,
        )
        self.song_player.pack(anchor="w", pady=(6, 0))
        loop_row = ttk.Frame(transport)
        loop_row.pack(anchor="w", pady=(8, 0))
        ttk.Checkbutton(loop_row, text="Loop", variable=self.loop_on, command=self.on_loop_toggle).pack(side="left")
        ttk.Label(loop_row, text="from bar", style="Muted.TLabel").pack(side="left", padx=(10, 4))
        self.loop_start_entry = ttk.Entry(loop_row, textvariable=self.loop_start, width=5)
        self.loop_start_entry.pack(side="left")
        ttk.Label(loop_row, text="for", style="Muted.TLabel").pack(side="left", padx=(6, 4))
        self.loop_bars_entry = ttk.Entry(loop_row, textvariable=self.loop_bars, width=4)
        self.loop_bars_entry.pack(side="left")
        ttk.Label(loop_row, text="bars", style="Muted.TLabel").pack(side="left", padx=(4, 10))
        self.delete_button = ttk.Button(loop_row, text="Delete bars", command=self.on_delete_bars)
        self.delete_button.pack(side="left")
        self.blank_button = ttk.Button(loop_row, text="Blank bars", command=self.on_blank_bars)
        self.blank_button.pack(side="left", padx=(8, 0))
        ttk.Label(loop_row, text="Right-drag on the chart to add silent bars.", style="Muted.TLabel").pack(
            side="left", padx=(12, 0)
        )
        for entry in (self.loop_start_entry, self.loop_bars_entry):
            entry.bind("<FocusOut>", self.on_loop_fields)
            entry.bind("<Return>", self.on_loop_fields)
        self.chart = SectionChart(right)
        self.chart.on_zoom_changed = self.refresh_zoom
        self.chart.on_bar_click = self.on_bar_click
        self.chart.on_bars_chosen = self.on_bars_chosen
        self.chart.on_bars_inserted = self.on_bars_inserted
        self.chart.grid(row=3, column=0, sticky="nsew", pady=(0, 8))

        lower = ttk.Frame(right)
        lower.grid(row=4, column=0, sticky="ew")
        lower.columnconfigure(0, weight=1)
        ranges = ttk.Frame(lower)
        ranges.grid(row=0, column=0, sticky="nw")
        banner = ttk.Frame(lower)
        banner.grid(row=0, column=1, sticky="ne", padx=(24, 0))
        self.cover_holder = tk.Frame(banner, width=200, height=200, bg=FIELD, highlightthickness=1, highlightbackground=LINE)
        self.cover_holder.pack_propagate(False)
        self.cover_holder.pack(anchor="e")
        self.cover_label = tk.Label(self.cover_holder, bg=FIELD, fg=MUTED, font=("Segoe UI", 9), text="")
        self.cover_label.pack(fill="both", expand=True)
        self.album_caption = ttk.Label(banner, text="", style="Muted.TLabel", wraplength=200, justify="right", anchor="e")
        self.album_caption.pack(anchor="e", pady=(6, 0))
        self.range_entries: list[ttk.Entry] = []
        ttk.Label(ranges, text="RC-10R kit", style="Section.TLabel").grid(row=0, column=1, sticky="w", pady=(0, 2))
        self.kit_value = ttk.Label(ranges, text="", style="Song.TLabel")
        self.kit_value.grid(row=0, column=2, columnspan=4, sticky="w")
        self.kit_reason = ttk.Label(ranges, text="", style="Muted.TLabel", wraplength=420, justify="left")
        self.kit_reason.grid(row=1, column=1, columnspan=5, sticky="w", pady=(0, 8))
        for row, (key, label, _fill) in enumerate(DIVISIONS):
            family = {"pattern1Fill": "pattern1", "pattern2Fill": "pattern2"}.get(key, key)
            swatch_color = "#f3f1eb" if key.endswith("Fill") else ROLE[family]
            tk.Label(ranges, text="    ", bg=swatch_color, width=2).grid(row=row + 2, column=0, sticky="w", pady=2, padx=(0, 8))
            ttk.Label(ranges, text=label, width=16).grid(row=row + 2, column=1, sticky="w")
            start = tk.StringVar()
            length = tk.StringVar()
            self.starts[key] = start
            self.lengths[key] = length
            start_entry = ttk.Entry(ranges, textvariable=start, width=6)
            length_entry = ttk.Entry(ranges, textvariable=length, width=6)
            start_entry.grid(row=row + 2, column=2, sticky="w", padx=(8, 4))
            ttk.Label(ranges, text="×", style="Muted.TLabel").grid(row=row + 2, column=3, sticky="w")
            length_entry.grid(row=row + 2, column=4, sticky="w", padx=(4, 8))
            for entry in (start_entry, length_entry):
                entry.bind("<FocusOut>", self.on_ranges)
                entry.bind("<Return>", self.on_ranges)
                self.range_entries.append(entry)
            ttk.Button(ranges, text="Play", command=lambda key=key: self.play(key)).grid(row=row + 2, column=5, sticky="w")

        footer = ttk.Frame(right)
        footer.grid(row=5, column=0, sticky="ew", pady=(10, 0))
        ttk.Label(footer, text="Note", style="Section.TLabel").pack(side="left", padx=(0, 8))
        self.note = ttk.Entry(footer)
        self.note.pack(side="left", fill="x", expand=True)
        self.add_button = ttk.Button(footer, text="Add to chart", style="Accent.TButton", command=self.on_add)
        self.add_button.pack(side="left", padx=(8, 0))
        self.copy_button = ttk.Button(footer, text="Copy ranges", command=self.copy_ranges)
        self.copy_button.pack(side="left", padx=(8, 0))
        ttk.Button(footer, text="Open chart page", command=self.open_chart).pack(side="left", padx=(8, 0))

        self.summary = ttk.Label(frame, text="The saved list is the chart. Search when you want a new file.", style="Muted.TLabel", wraplength=1080)
        self.summary.grid(row=4, column=0, sticky="w", pady=(10, 0))
        self.status = ttk.Label(frame, text="", style="Muted.TLabel", wraplength=1080)
        self.status.grid(row=5, column=0, sticky="w", pady=(4, 0))
        self.root.after(100, self.poll_covers)
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
        self.note.configure(state="normal" if editable else "disabled")
        for entry in self.range_entries:
            entry.configure(state="normal" if editable else "disabled")

    def set_status(self, text: str) -> None:
        self.status["text"] = text

    def reload_songs(self) -> None:
        self.catalog = load_catalog(chart.MAP_PATH)
        self.songs = list(self.catalog["songs"])
        children = self.saved_list.get_children("")
        closed = set()
        for folder in children:
            if not self.saved_list.item(folder, "open"):
                closed.add(self.saved_list.item(folder, "text"))
        if children:
            self.saved_list.delete(*children)
        self.song_by_iid = {}
        self.song_iids = {}
        groups: dict[str, list[dict]] = {}
        for song in self.songs:
            groups.setdefault(song["artist"], []).append(song)
        for artist in sorted(groups, key=str.casefold):
            folder = self.saved_list.insert("", "end", text=artist, open=artist not in closed)
            for song in sorted(groups[artist], key=lambda item: item["title"].casefold()):
                iid = self.saved_list.insert(folder, "end", text=song["title"])
                self.song_by_iid[iid] = song
                self.song_iids[song["id"]] = iid
        self.saved_label["text"] = f"Saved songs  ·  {len(self.songs)}"

    def on_saved(self, _event: object = None) -> None:
        selected = self.saved_list.selection()
        if not selected:
            return
        song = self.song_by_iid.get(selected[0])
        if song is None:
            return
        path = Path(self.catalog["drumRoot"]) / song["file"]
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
            summary="Saved on the chart. Type these into Rhythm Converter. Intro and the ending play once. Patterns and fills loop.",
        )

    def show_file(
        self,
        path: Path,
        artist: str,
        title: str,
        divisions: dict[str, list[int]],
        editable: bool,
        summary: str,
    ) -> None:
        self.stop(drop_audio=True)
        midi = chart.parse_midi(path)
        hits, meters, boundaries = chart.build_measures(midi)
        convert = chart.tick_to_ms(midi["tempos"], midi["ppq"])
        parts = suggest_sections.song_parts(midi["events"], boundaries)
        kit, kit_reason = suggest_sections.recommend_kit(midi, artist, title, meters)
        tempo = chart.tempo_text(midi["tempos"])
        meter = chart.meter_text(meters)
        self.view = {
            "path": path,
            "artist": artist,
            "title": title,
            "hits": hits,
            "bar_ms": [convert(tick) for tick in boundaries],
            "events": [[convert(tick), note, velocity] for tick, note, velocity in midi["events"]],
            "divisions": divisions,
            "parts": parts,
            "kit": kit,
            "editable": editable,
            "summary": summary,
            "tempo": tempo,
            "meter": meter,
            "generation": 1,
        }
        self.heading["text"] = f"{artist} — {title}"
        self.meta["text"] = f"{tempo}  ·  {meter}  ·  {len(hits)} measures"
        self.kit_value["text"] = kit
        self.kit_reason["text"] = kit_reason
        self.summary["text"] = summary
        self._loading = True
        for key, _label, _fill in DIVISIONS:
            start, end = divisions[key]
            self.starts[key].set(str(start))
            self.lengths[key].set(str(end - start + 1))
        self.note.configure(state="normal")
        self.note.delete(0, "end")
        self.loop_on.set(False)
        self.loop_start.set("")
        self.loop_bars.set("")
        self.selection = None
        self._loading = False
        self.chart.show_selection(None, None)
        self.chart.draw(hits, divisions, parts)
        self.refresh_zoom()
        self.refresh_buttons()
        self.refresh_transport()
        self.set_status("Click a bar to play from there. Right-drag adds silent bars. Arrows move the loop. Delete changes this preview only.")
        self.load_cover(artist, title)
        self.request_songsterr(artist, title)

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
                token, label, url = self.songsterr_queue.get_nowait()
                self.show_songsterr_link(token, label, url)
        except queue.Empty:
            pass
        self.root.after(100, self.poll_covers)

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
        self.view["divisions"] = divisions
        self.stop()
        self.chart.draw(self.view["hits"], divisions, self.view.get("parts"))
        self.set_status("Updated the suggestion. Type these into Rhythm Converter.")

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
            messagebox.showinfo("Drum MIDI finder", "Choose MIDIfind, MIDIWORLD, or both.")
            return
        query = " ".join(part for part in (artist, song) if part)
        site_text = " and ".join(sites)
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
        self.root.after(0, lambda hits=hits, note=note, error=error: self.show_hits(hits, note, error))

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
        if not messagebox.askyesno(
            "Download",
            f'Download "{hit.title}" from {hit.site} and suggest loop sections?\n\n'
            "The file is saved in incoming. Nothing is added to the song list until you click Add to chart.",
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
        self.root.after(0, lambda path=path, error=error: self.show_download(path, error))

    def show_download(self, path: Path | None, error: str) -> None:
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
            self.artist.get().strip() or "Unknown",
            self.title.get().strip() or path.stem,
            idea.divisions,
            editable=True,
            summary=idea.summary,
        )
        self.set_status("Suggestion ready. Add to chart keeps it, or change a number if a section landed in the wrong place.")

    def play(self, key: str) -> None:
        if not self.view:
            self.set_status("Pick a saved song or download a file first.")
            return
        if key == "all":
            if self.loop_on.get() and self.selection:
                start, end = self.selection
                self.start_playback(start, self.span_label(start, end), (start, end))
            else:
                self.start_playback(1, "the drum track", None)
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
        if self.loop_on.get():
            self.start_playback(start, self.span_label(start, end), (start, end))

    def on_bar_click(self, bar: int) -> None:
        if not self.view:
            return
        loop_bars = None
        if self.loop_on.get():
            if self.selection and not self.selection[0] <= bar <= self.selection[1]:
                length = self.selection[1] - self.selection[0] + 1
                end = min(len(self.view["hits"]), bar + length - 1)
                self.apply_selection(bar, end)
            elif not self.selection:
                self.apply_selection(bar, bar)
            loop_bars = self.selection
        label = self.span_label(*loop_bars) if loop_bars else "the drum track"
        self.start_playback(bar, label, loop_bars)

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
        self.stop(drop_audio=True)
        view = self.view
        hits, bar_ms, events, divisions, parts = remove_bars(
            view["hits"], view["bar_ms"], view["events"], view["divisions"], view["parts"], start, end
        )
        view["hits"] = hits
        view["bar_ms"] = bar_ms
        view["events"] = events
        view["divisions"] = divisions
        view["parts"] = parts
        view["generation"] = view.get("generation", 1) + 1
        self._loading = True
        for key, _label, _fill in DIVISIONS:
            div_start, div_end = divisions[key]
            self.starts[key].set(str(div_start))
            self.lengths[key].set(str(div_end - div_start + 1))
        self.loop_on.set(False)
        self.loop_start.set("")
        self.loop_bars.set("")
        self._loading = False
        self.selection = None
        self.chart.show_selection(None, None)
        self.chart.draw(hits, divisions, parts)
        self.meta["text"] = f"{view['tempo']}  ·  {view['meter']}  ·  {len(hits)} measures"
        self.refresh_zoom()
        self.refresh_transport()
        note = ""
        if divisions["pattern1"] == divisions["pattern2"]:
            note = " Pattern 1 and Pattern 2 now cover the same bars."
        self.set_status(f"Removed {span}. Later bars moved up. The MIDI file is unchanged.{note}")

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
        self.stop(drop_audio=True)
        view = self.view
        hits, events = silence_bars(view["hits"], view["bar_ms"], view["events"], start, end)
        view["hits"] = hits
        view["events"] = events
        view["generation"] = view.get("generation", 1) + 1
        self.chart.draw(hits, view["divisions"], view["parts"])
        self.refresh_transport()
        self.set_status(f"Blanked {span}. They stay in place, so the song is the same length. The MIDI file is unchanged.")

    def on_bars_inserted(self, at: int, count: int) -> None:
        if not self.view or count < 1:
            return
        self.stop(drop_audio=True)
        view = self.view
        hits, bar_ms, events, divisions, parts = insert_bars(
            view["hits"], view["bar_ms"], view["events"], view["divisions"], view["parts"], at, count
        )
        view["hits"] = hits
        view["bar_ms"] = bar_ms
        view["events"] = events
        view["divisions"] = divisions
        view["parts"] = parts
        view["generation"] = view.get("generation", 1) + 1
        self._loading = True
        for key, _label, _fill in DIVISIONS:
            div_start, div_end = divisions[key]
            self.starts[key].set(str(div_start))
            self.lengths[key].set(str(div_end - div_start + 1))
        self._loading = False
        self.chart._selection = (at, at + count - 1)
        self.chart.draw(hits, divisions, parts)
        self.apply_selection(at, at + count - 1)
        self.meta["text"] = f"{view['tempo']}  ·  {view['meter']}  ·  {len(hits)} measures"
        self.refresh_zoom()
        span = "1 silent bar" if count == 1 else f"{count} silent bars"
        self.set_status(f"Added {span} at bar {at}. The song is longer and stays on the beat. The MIDI file is unchanged.")

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
        path = INCOMING / f"preview-{generation}.wav"
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
        self.root.after(0, lambda wav=wav, error=error: self.begin_rendered(token, generation, wav, error))

    def begin_rendered(self, token: int, generation: int, wav: bytes, error: str) -> None:
        if token != self.play_token:
            return
        self.preparing = False
        if error or not wav:
            self.set_status(error or "The preview could not be built.")
            self.refresh_transport()
            return
        if not self.view or self.view["generation"] != generation:
            return
        try:
            INCOMING.mkdir(parents=True, exist_ok=True)
            path = INCOMING / f"preview-{generation}.wav"
            path.write_bytes(wav)
        except Exception as exc:
            self.set_status(f"Playback failed: {exc}")
            return
        self.rendered_generation = generation
        self.finish_start()

    def finish_start(self) -> None:
        pending = self.pending_start
        view = self.view
        if pending is None or view is None or self.rendered_generation != view["generation"]:
            return
        start_ms, label, loop = pending
        file_start = view["bar_ms"][0]
        try:
            if not self.device_open:
                open_preview(INCOMING / f"preview-{view['generation']}.wav")
                self.device_open = True
            play_at(start_ms - file_start)
        except Exception as exc:
            self.device_open = False
            close_preview()
            self.set_status(f"Playback failed: {exc}")
            self.refresh_transport()
            return
        self.cancel_tick()
        self.playing = {
            "anchor_ms": float(start_ms),
            "origin": time.perf_counter(),
            "paused": False,
            "loop": loop,
            "label": label,
            "file_start": file_start,
        }
        self.tick_playhead()
        self.refresh_transport()
        self.follow_song("play")

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
            self.follow_song("play")
        elif position >= song_end:
            self.follow_song("pause")
            self.stop()
            self.set_status("Stopped.")
            return
        self.show_at(position, "Looping" if loop else "Playing", state["label"])
        self.after_id = self.root.after(40, self.tick_playhead)

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
        self.follow_song("pause")

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
        self.follow_song("play")

    def on_stop(self) -> None:
        self.follow_song("pause")
        self.stop()
        self.set_status("Stopped.")

    def on_song_toggle(self) -> None:
        if not self.with_song.get():
            self.song_player["text"] = "Off. Turn this on to play and pause Spotify, YouTube, or another open player in time with the drums."
            return
        if self.playing and not self.playing.get("paused"):
            self.follow_song("play")
        else:
            self.follow_song("status")

    def follow_song(self, action: str) -> None:
        if not self.with_song.get():
            return
        position = 0
        if action == "play" and self.playing:
            position = int(self.playing.get("anchor_ms", 0))
        self.media_token += 1
        token = self.media_token
        threading.Thread(target=self.media_worker, args=(token, action, position), daemon=True).start()

    def media_worker(self, token: int, action: str, position: int) -> None:
        try:
            result = control_media(action, position)
        except Exception as exc:
            result = f"error|{exc}"
        self.root.after(0, lambda result=result: self.show_media(token, result))

    def request_songsterr(self, artist: str, title: str) -> None:
        if not title.strip() and not artist.strip():
            return
        self.songsterr_token += 1
        token = self.songsterr_token
        self.songsterr_url = ""
        self.songsterr_link.configure(text="Finding the Songsterr tab…", fg=MUTED)
        threading.Thread(target=self.songsterr_worker, args=(token, artist, title), daemon=True).start()

    def songsterr_worker(self, token: int, artist: str, title: str) -> None:
        label, url = lookup_songsterr(artist, title)
        self.songsterr_queue.put((token, label, url))

    def show_songsterr_link(self, token: int, label: str, url: str) -> None:
        if token != self.songsterr_token:
            return
        self.songsterr_url = url
        self.songsterr_link.configure(text=label, fg=ACCENT)

    def open_songsterr(self, _event: object = None) -> None:
        if self.songsterr_url:
            os.startfile(self.songsterr_url)

    def show_media(self, token: int, result: str) -> None:
        if token != self.media_token or not self.with_song.get():
            return
        if result.startswith("error|"):
            self.song_player["text"] = result.split("|", 1)[1]
            return
        self.song_player["text"] = media_caption(result)

    def on_zoom(self, direction: int) -> None:
        self.chart.zoom(direction)
        self.refresh_zoom()

    def refresh_zoom(self) -> None:
        self.zoom_out_button["state"] = "normal" if self.chart.can_zoom(-1) else "disabled"
        self.zoom_in_button["state"] = "normal" if self.chart.can_zoom(1) else "disabled"

    def refresh_transport(self) -> None:
        if self.playing and self.playing.get("paused"):
            self.pause_button.configure(text="Resume", state="normal")
        elif self.playing:
            self.pause_button.configure(text="Pause", state="normal")
        else:
            self.pause_button.configure(text="Pause", state="disabled")
        self.delete_button.configure(state="normal" if self.selection and self.view else "disabled")

    def cancel_tick(self) -> None:
        if self.after_id:
            self.root.after_cancel(self.after_id)
            self.after_id = None

    def stop(self, drop_audio: bool = False) -> None:
        self.play_token += 1
        self.preparing = False
        self.playing = None
        self.pending_start = None
        self.cancel_tick()
        close_preview()
        self.device_open = False
        winsound.PlaySound(None, winsound.SND_PURGE)
        self.chart.hide_playhead()
        if drop_audio:
            self.rendered_generation = None
            if INCOMING.is_dir():
                for path in INCOMING.glob("preview-*.wav"):
                    try:
                        path.unlink()
                    except OSError:
                        pass
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
        if not messagebox.askyesno(
            "Add to chart",
            f"Add {artist} — {title} with these ranges?\n\n{typed}\n\n"
            "The MIDI is copied into the drum folder and the chart page is rebuilt. "
            "You still type the numbers into Rhythm Converter.",
        ):
            self.set_status("Chart update cancelled.")
            return
        self.set_busy(True)
        self.set_status("Copying the file and rebuilding the chart…")
        note = self.note.get()
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
        self.root.after(0, lambda report=report, error=error: self.finish_add(report, error, song))

    def finish_add(self, report: str, error: str, song: str) -> None:
        self.set_busy(False)
        if error:
            self.set_status(error)
            return
        self.reload_songs()
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

    def open_folder(self) -> None:
        INCOMING.mkdir(parents=True, exist_ok=True)
        os.startfile(INCOMING)

    def open_chart(self) -> None:
        if chart.OUT_PATH.is_file():
            os.startfile(chart.OUT_PATH)
        else:
            self.set_status("The chart page has not been built yet.")

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
