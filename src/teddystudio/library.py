"""Recursive collection recognition and explicit, backed-up audio tag writes."""

from __future__ import annotations

from pathlib import Path
import hashlib
import json
import os
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


def within_folder(folder, root):
    if not folder or not root:
        return False
    folder = Path(os.path.abspath(folder)).as_posix().casefold().rstrip("/")
    root = Path(os.path.abspath(root)).as_posix().casefold().rstrip("/")
    return folder == root or folder.startswith(root + "/")


def refresh_roots(selected, roots, excluded):
    """Scan a selected parent for NEW children, retaining explicit re-inclusions."""
    if not selected:
        return roots
    blocked = any(
        within_folder(selected, item)
        and not any(
            within_folder(selected, root) and within_folder(root, item)
            for root in roots
        )
        for item in excluded
    )
    targets = [] if blocked else [selected]
    targets.extend(
        root
        for root in roots
        if within_folder(root, selected)
        and any(within_folder(root, item) for item in excluded)
        and root not in targets
    )
    return targets


def web_metadata(path):
    """Read receipts from 0.7.0 as well as newer ones; never trust a stale hash."""
    receipt = Path(str(path) + ".teddyshelf-web.json")
    if receipt.stat().st_size > 1_000_000:
        raise ValueError("Web-Downloadnachweis ist zu groß")
    data = json.loads(receipt.read_text("utf-8"))
    if (
        not isinstance(data, dict)
        or data.get("mode") != "audio"
        or not isinstance(data.get("title"), str)
        or not data["title"].strip()
    ):
        raise ValueError("Ungültige Web-Downloadmetadaten")
    with Path(path).open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != data.get("sha256"):
        raise ValueError("Web-Download wurde verändert; Metadatenzuordnung prüfen")
    return data


def web_book(path, metadata, info):
    path = Path(path).resolve()
    identifier = hashlib.sha256(
        ("web-file:" + str(path).casefold()).encode()
    ).hexdigest()[:24]
    title = metadata["title"].strip()
    tags = {key.lower(): str(value) for key, value in info.get("tags", {}).items()}
    tags["title"] = title
    album = title
    track = Track(
        str(path), title, str(metadata.get("index") or ""), info["duration"], tags
    )
    return Book(
        identifier,
        str(path.parent),
        title,
        series=metadata.get("series") or "",
        album=album,
        artist=metadata.get("artist") or tags.get("artist", ""),
        origin="Web-Download",
        tracks=[track],
    )


def organized_duplicate(metadata, source):
    try:
        target = Path(metadata["organized_copy"])
        if not target.is_file() or target.resolve() == source.resolve():
            return False
        copy = web_metadata(target)
        return copy.get("organized_from_hash", copy["sha256"]) == metadata[
            "sha256"
        ] and copy.get("organized_from") == str(source.resolve())
    except (OSError, KeyError, TypeError, ValueError):
        return False


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


def scan(root, settings, cache, on_progress=lambda message: None, excluded=()):
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError("Der Quellordner ist nicht erreichbar.")
    export = Path(settings.export).resolve()
    if root == export or root in export.parents:
        raise ValueError("Der Exportordner darf nicht im Quellordner liegen.")
    folders = {}

    def failed_walk(error):
        # Never treat an unreadable subtree as a successful empty scan.
        raise error

    for directory, names, files in os.walk(
        root, followlinks=False, onerror=failed_walk
    ):
        folder = Path(directory)
        names[:] = [
            name
            for name in names
            if name != ".teddyshelf-web"
            and not any(within_folder(folder / name, item) for item in excluded)
        ]
        if any(within_folder(folder, item) for item in excluded):
            names.clear()
            continue
        for name in files:
            path = folder / name
            if path.suffix.lower() in AUDIO_EXTENSIONS:
                folders.setdefault(folder, []).append(path)
            elif name.lower() in {
                "cover.jpg",
                "cover.png",
                "playlist.m3u",
                "playlist.m3u8",
            }:
                folders.setdefault(folder, [])
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
        receipt = Path(str(path) + ".teddyshelf-web.json")
        if receipt.is_file():
            receipt_stat = receipt.stat()
            signature.extend([receipt_stat.st_size, receipt_stat.st_mtime_ns])
            try:
                copied = Path(json.loads(receipt.read_text("utf-8"))["organized_copy"])
                for dependency in (copied, Path(str(copied) + ".teddyshelf-web.json")):
                    signature.extend(
                        [dependency.stat().st_size, dependency.stat().st_mtime_ns]
                        if dependency.is_file()
                        else [None]
                    )
            except (OSError, ValueError, KeyError, TypeError):
                pass
        old = probe_cache.get(str(path), {})
        if old.get("signature") == signature:
            return str(path), old
        try:
            result = {
                "signature": signature,
                "info": probe(path, settings.ffprobe),
            }
            if receipt.is_file():
                try:
                    result["web"] = web_metadata(path)
                    result["organized_duplicate"] = organized_duplicate(
                        result["web"], path
                    )
                except Exception as exc:
                    result["web_error"] = str(exc)
            return str(path), result
        except Exception as exc:
            return str(path), {"error": str(exc)}

    all_paths = [p for paths in folders.values() for p in paths]
    on_progress(
        f"Audiodaten lesen: {len(all_paths)} Dateien (unveränderte aus Cache) …"
    )
    with ThreadPoolExecutor(max_workers=4) as executor:
        inspected = dict(executor.map(inspect, all_paths))
    from .storage import atomic_json

    atomic_json(
        index_file,
        {**probe_cache, **{k: v for k, v in inspected.items() if "info" in v}},
    )
    for folder, paths in sorted(folders.items(), key=lambda row: natural_key(row[0])):
        on_progress(str(folder))
        ordinary = []
        for path in sorted(paths, key=natural_key):
            cached = inspected[str(path)]
            if cached.get("organized_duplicate"):
                continue
            if "web" in cached:
                result.append(web_book(path, cached["web"], cached["info"]))
            elif Path(str(path) + ".teddyshelf-web.json").is_file():
                # Keep an invalid/unreadable download visible as its own item.
                title = cached.get("info", {}).get("tags", {}).get("title", path.stem)
                book = web_book(
                    path, {"title": title}, cached.get("info", {"duration": 0})
                )
                book.tracks[0].error = cached.get("web_error") or cached.get(
                    "error", "Ungültige Web-Downloadmetadaten"
                )
                book.status = "Prüfen"
                result.append(book)
            else:
                ordinary.append(path)
        if paths and not ordinary:
            continue
        paths = ordinary
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
        parent = folder.parent.name if folder != root and folder.parent != root else ""
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
