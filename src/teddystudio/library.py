"""Recursive collection recognition and explicit, backed-up audio tag writes."""

from __future__ import annotations

from pathlib import Path
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
import re
import shutil
import time
import unicodedata
from urllib.parse import unquote, urlparse
import mutagen
from mutagen.id3 import APIC, ID3, ID3NoHeaderError

from .domain import AUDIO_EXTENSIONS, Book, Track, natural_key
from .media import probe

PLAYLIST_WARNING = (
    "Playlist und Audiodateien passen nicht eindeutig zusammen. Reihenfolge prüfen."
)


def resolve_legacy_order(book):
    """Clear old false warnings only after a complete unambiguous file match."""
    if book.order_pending or not any(
        t.error.startswith("Playlist und Audiodateien") for t in book.tracks
    ):
        return False
    folder = Path(book.folder)
    playlists = [p for p in folder.iterdir() if p.suffix.lower() in {".m3u", ".m3u8"}]
    if len(playlists) != 1:
        return False
    paths = [
        p
        for p in folder.iterdir()
        if p.suffix.lower() in AUDIO_EXTENSIONS and p.is_file()
    ]
    ordered = playlist_order(playlists[0], paths)
    if ordered is None or {str(p) for p in ordered} != {t.path for t in book.tracks}:
        return False
    if not book.confirmed_order and "bestätigte Spurenreihenfolge" not in book.origin:
        positions = {str(p): n for n, p in enumerate(ordered)}
        book.tracks.sort(key=lambda track: positions[track.path])
    for track in book.tracks:
        if track.error.startswith("Playlist und Audiodateien"):
            track.error = ""
    return True


def playlist_order(playlist, paths):
    """Match the complete local file set, ignoring only Windows-safe name differences.

    Never infer an order from partial matches or silently omit additional audio files.
    """

    def key(name):
        return unicodedata.normalize("NFC", name).casefold()

    expected = {key(p.name): p for p in paths}
    if len(expected) != len(paths):
        return None
    raw = playlist.read_bytes()
    for encoding in ("utf-8-sig", "cp1252", "cp850"):
        try:
            lines = raw.decode(encoding).splitlines()
        except UnicodeError:
            continue
        names = []
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            line = line.strip('"')
            if line.lower().startswith("file://"):
                line = unquote(urlparse(line).path)
            names.append(key(Path(line.replace("\\", "/")).name))
        if not names:
            return list(sorted(paths, key=natural_key))
        if (
            len(names) == len(paths)
            and len(set(names)) == len(names)
            and set(names) == set(expected)
        ):
            return [expected[name] for name in names]
    return None


def scan(root, settings, cache, on_progress=lambda message: None):
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError("Der Quellordner ist nicht erreichbar.")
    export = Path(settings.export).resolve()
    if root == export or root in export.parents:
        raise ValueError("Der Exportordner darf nicht im Quellordner liegen.")
    folders = {}
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS:
            folders.setdefault(path.parent, []).append(path)
        elif path.is_file() and (
            path.name.lower()
            in {"cover.jpg", "cover.png", "playlist.m3u", "playlist.m3u8"}
        ):
            folders.setdefault(path.parent, [])
    result = []
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    index_file = cache / "probe-index.json"
    try:
        probe_cache = json.loads(index_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        probe_cache = {}

    def inspect(path):
        stat = path.stat()
        signature = [stat.st_size, stat.st_mtime_ns, settings.ffprobe]
        old = probe_cache.get(str(path), {})
        if old.get("signature") == signature:
            return str(path), old
        try:
            return str(path), {
                "signature": signature,
                "info": probe(path, settings.ffprobe),
            }
        except Exception as exc:
            return str(path), {"error": str(exc)}

    all_paths = [p for paths in folders.values() for p in paths]
    on_progress(
        f"Audiodaten lesen: {len(all_paths)} Dateien (unveränderte aus Cache) …"
    )
    with ThreadPoolExecutor(max_workers=4) as executor:
        inspected = dict(executor.map(inspect, all_paths))
    from .storage import atomic_json

    atomic_json(index_file, {k: v for k, v in inspected.items() if "info" in v})
    for folder, paths in sorted(folders.items(), key=lambda row: natural_key(row[0])):
        on_progress(str(folder))
        tracks = []
        for path in sorted(paths, key=natural_key):
            try:
                cached = inspected[str(path)]
                if "error" in cached:
                    raise ValueError(cached["error"])
                info = cached["info"]
                tags = {k.lower(): str(v) for k, v in info["tags"].items()}
                tracks.append(
                    Track(
                        str(path),
                        tags.get("title", path.stem),
                        tags.get("track", ""),
                        info["duration"],
                        tags,
                    )
                )
            except Exception as exc:
                tracks.append(Track(str(path), path.stem, error=str(exc)))
        playlist = next(
            (p for p in folder.iterdir() if p.suffix.lower() in {".m3u", ".m3u8"}), None
        )
        if playlist:
            ordered = playlist_order(playlist, paths)
            if ordered is not None:
                positions = {str(path): index for index, path in enumerate(ordered)}
                tracks.sort(key=lambda track: positions[track.path])
            else:
                for track in tracks:
                    if not track.error:
                        track.error = PLAYLIST_WARNING
        tags = tracks[0].tags if tracks else {}
        album = tags.get("album", folder.name)
        series = tags.get("series", "")
        parent = folder.parent.name if folder.parent != root else ""
        if not series:
            series = parent
        match = re.match(
            r"^(?:(.+?)\s*[-–:]\s*)?(?:Folge\s*)?(\d{1,3})\s*[-–:.]\s*(.+)$",
            folder.name,
            re.I,
        )
        episode, title = None, album
        if match:
            series = match[1] or series
            episode, title = int(match[2]), match[3]
        # Only this established family is grouped from loose top-level folders.
        if re.search(r"k[äa]pt.?n\s+sharky", folder.name, re.I):
            series = "Käptn Sharky"
            title = (
                re.sub(r"^k[äa]pt.?n\s+sharky\s*[-–:]?\s*", "", folder.name, flags=re.I)
                or folder.name
            )
        identifier = hashlib.sha256(str(folder).casefold().encode()).hexdigest()[:24]
        book = Book(
            identifier,
            str(folder),
            title,
            series,
            episode,
            album,
            tags.get("artist", ""),
            tags.get("album_artist", tags.get("albumartist", "")),
            tags.get("genre", "Hörspiel"),
            tags.get("date", ""),
            tags.get("disc", ""),
            tracks=tracks,
        )
        if not tracks or any(t.error for t in tracks):
            book.status = "Prüfen"
        from .covers import discover_cover

        book.cover, book.cover_source = discover_cover(book, settings, cache)
        result.append(book)
    return result


def write_tags(book: Book, include_cover=False):
    """Only called by the explicit GUI action. Episode never overwrites track number."""
    saved = []
    for track in book.tracks:
        path = Path(track.path)
        backup = path.with_name(path.name + f".tags-{time.time_ns()}.bak")
        shutil.copy2(path, backup)
        try:
            audio = mutagen.File(path, easy=True)
            if audio is None:
                raise ValueError(f"Tagformat nicht unterstützt: {path.name}")
            if audio.tags is None:
                audio.add_tags()
            mapping = {
                "title": track.title,
                "album": book.album,
                "artist": book.artist,
                "albumartist": book.albumartist,
                "genre": book.genre,
                "date": book.date,
                "discnumber": book.disc,
                "tracknumber": track.track,
            }
            for key, value in mapping.items():
                if value:
                    audio[key] = [value]
                elif key in audio:
                    del audio[key]
            audio.save()
            if include_cover and book.cover:
                from .covers import embed_image

                embed_image(path, book.cover)
            saved.append(str(path))
        except Exception:
            shutil.copy2(backup, path)
            raise
    return saved
