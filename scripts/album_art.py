"""Find a song's album cover and cache a PNG for the finder window.

The MIDI files have no artwork. This asks Deezer's public search for the
studio album, then saves a small PNG under artwork/. That folder stays
off the repo.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path

BROWSER = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
SEARCH_HOST = "api.deezer.com"
SIZE = 200
SKIP = ("live", "best of", "greatest", "tribute", "lullaby", "karaoke", "session", "remix", "cover")


def cached(artist: str, title: str, cache: Path) -> tuple[Path, str] | None:
    png, note = _paths(artist, title, cache)
    if png.is_file() and note.is_file():
        album = note.read_text(encoding="utf-8").strip()
        if album:
            return png, album
    return None


def cover_for(artist: str, title: str, cache: Path) -> tuple[Path, str] | None:
    ready = cached(artist, title, cache)
    if ready is not None:
        return ready
    hit = _search(artist, title)
    if hit is None:
        return None
    cache.mkdir(parents=True, exist_ok=True)
    png, note = _paths(artist, title, cache)
    jpeg = cache / f"{_slug(f'{artist}-{title}')}.jpg"
    _download(hit["art"], jpeg)
    _to_png(jpeg, png, SIZE)
    jpeg.unlink(missing_ok=True)
    note.write_text(hit["album"], encoding="utf-8")
    return png, hit["album"]


def _paths(artist: str, title: str, cache: Path) -> tuple[Path, Path]:
    slug = _slug(f"{artist}-{title}")
    return cache / f"{slug}.png", cache / f"{slug}.txt"


def _search(artist: str, title: str) -> dict[str, str] | None:
    query = urllib.parse.urlencode({"q": f"{artist} {title}"})
    url = f"https://{SEARCH_HOST}/search?{query}"
    payload = json.loads(_read(url, SEARCH_HOST, 1_500_000).decode("utf-8"))
    for item in payload.get("data", []):
        band = str((item.get("artist") or {}).get("name") or "")
        track = str(item.get("title_short") or item.get("title") or "")
        album = item.get("album") or {}
        album_name = str(album.get("title") or "")
        art = str(album.get("cover_xl") or album.get("cover_big") or "")
        if not art or not album_name:
            continue
        if not _same(band, artist) or not _same(track, title):
            continue
        if _skipped(track) or _skipped(album_name):
            continue
        return {"art": art, "album": album_name}
    return None


def _skipped(name: str) -> bool:
    text = _flat(name)
    return any(phrase in text for phrase in SKIP)


def _download(url: str, dest: Path) -> None:
    host = urllib.parse.urlparse(url).hostname or ""
    if not host.endswith(".dzcdn.net"):
        raise ValueError(f"Refusing artwork host {host or url}")
    dest.write_bytes(_read(url, host, 4_000_000))


def _read(url: str, host: str, limit: int) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": BROWSER, "Accept": "*/*"})
    with urllib.request.urlopen(request, timeout=20) as response:
        final = urllib.parse.urlparse(response.geturl()).hostname or ""
        if final != host and not (host.endswith(".dzcdn.net") and final.endswith(".dzcdn.net")):
            raise ValueError("The artwork lookup left the expected site")
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("The artwork file is too large")
    return data


def _to_png(src: Path, dest: Path, size: int) -> None:
    script = r"""
Add-Type -AssemblyName System.Drawing
$image = [System.Drawing.Image]::FromFile($env:ART_SRC)
$bmp = New-Object System.Drawing.Bitmap ([int]$env:ART_SIZE), ([int]$env:ART_SIZE)
$draw = [System.Drawing.Graphics]::FromImage($bmp)
$draw.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
$draw.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
$draw.DrawImage($image, 0, 0, $bmp.Width, $bmp.Height)
$bmp.Save($env:ART_DEST, [System.Drawing.Imaging.ImageFormat]::Png)
$draw.Dispose()
$bmp.Dispose()
$image.Dispose()
"""
    env = os.environ.copy()
    env["ART_SRC"] = str(src)
    env["ART_DEST"] = str(dest)
    env["ART_SIZE"] = str(size)
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        check=True,
        env=env,
        capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _same(left: str, right: str) -> bool:
    return _flat(left) == _flat(right)


def _flat(text: str) -> str:
    text = text.lower().replace("&", " and ")
    text = re.sub(r"['\u2018\u2019]", "", text)
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", _flat(text)).strip("-") or "song"
