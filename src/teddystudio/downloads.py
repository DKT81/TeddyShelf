"""Verified, restartable local copies of existing TeddyCloud audio. Never fetch upstream."""

from pathlib import Path
from urllib.parse import quote, unquote
import hashlib
import json
import re
import shutil
import struct
from .client import TeddyCloud
from .domain import Book, Track, ascii_name, slug
from .storage import Settings, atomic_json
from . import media, taf


def chapter_title(entry, index, count):
    """Use names only when the catalog matches all actual TAF chapters."""
    titles = entry.get("model", {}).get("tracks", [])
    if (
        isinstance(titles, list)
        and len(titles) == count
        and isinstance(titles[index], str)
        and titles[index].strip()
    ):
        return titles[index].strip()
    return f"Spur {index + 1:03}"


def chapter_filename(title, number, extension):
    try:
        title = ascii_name(title[:110])
    except ValueError:
        title = f"Spur-{number:03}"
    return f"{number:03}-{title}.{extension}"


def output_folder(payload):
    entry = payload["download"]["entry"]
    root = Path(payload["download"]["destination"]).resolve()

    def safe(value, fallback):
        try:
            return ascii_name(value)
        except ValueError:
            return fallback

    series = safe(entry.get("series") or "Unzugeordnet", "Unzugeordnet")
    stem = safe(
        entry.get("model", {}).get("model") or Path(entry["title"]).stem, "tonie"
    )[:100]
    folder = (
        root / series / (stem + "-" + str(entry["audio_id"]) + "-" + entry["hash"][:8])
    )
    if not folder.resolve().is_relative_to(root):
        raise ValueError("Downloadziel verlässt den gewählten Ordner")
    return folder


def verify_source(path, entry, settings):
    info = taf.validate(path, settings.ffmpeg, timeout=settings.timeout)
    if (
        info["audioId"] != str(entry["audio_id"])
        or info["sha1Hash"].lower() != entry["hash"].lower()
    ):
        raise ValueError(
            "Audiodatei passt nicht zum eingelesenen Tonie (Audio-ID/Hash)"
        )
    return info


def fetch_taf(client, entry, work, log):
    final = work / "source.taf"
    partial = work / "source.partial.taf"
    state_path = work / "download-state.json"
    identity = {
        "url": client.url,
        "audio_id": entry["audio_id"],
        "hash": entry["hash"],
        "locations": entry["locations"],
    }
    previous = (
        json.loads(state_path.read_text(encoding="utf-8"))
        if state_path.exists()
        else {}
    )
    if previous.get("identity") != identity:
        if partial.exists():
            partial.unlink()
        if final.exists():
            raise ValueError("Arbeitsstand gehört zu einem anderen Download")
    atomic_json(state_path, {"identity": identity})
    if final.exists():
        return final
    last = None
    for location in entry["locations"]:
        path = location["path"]
        if any(
            p in {"..", "."} for p in path.replace("\\", "/").split("/")
        ) or path.startswith("/"):
            raise ValueError("Ungültiger Server-Dateipfad")
        params = {}
        if location.get("special"):
            params["special"] = location["special"]
        if location.get("overlay"):
            params["overlay"] = location["overlay"]
        offset = partial.stat().st_size if partial.exists() else 0
        headers = {"Range": f"bytes={offset}-"} if offset else {}
        log("Herunterladen", f"{path} · ab {offset} Bytes")
        try:
            with client.request(
                "GET",
                "/content/" + quote(path, safe="/"),
                params=params,
                headers=headers,
                stream=True,
                timeout=(15, 180),
            ) as response:
                append = response.status_code == 206 and offset > 0
                if append and not response.headers.get("Content-Range", "").startswith(
                    f"bytes {offset}-"
                ):
                    raise ValueError("Server bestätigt den Wiederaufnahmebereich nicht")
                if response.status_code == 206 and not append:
                    raise ValueError("Unerwartete Teilantwort")
                mode = "ab" if append else "wb"
                done = offset if append else 0
                reported = done
                with partial.open(mode) as stream:
                    for chunk in response.iter_content(8192):
                        if not chunk:
                            continue
                        stream.write(chunk)
                        done += len(chunk)
                        if done - reported >= 4 * 1024 * 1024:
                            log("Herunterladen", f"{path} · {done//(1024*1024)} MiB")
                            reported = done
            partial.replace(final)
            return final
        except Exception as exc:
            last = exc
            # Different locations may serve different representations. Never append across them.
            if len(entry["locations"]) > 1 and partial.exists():
                partial.unlink()
    raise ValueError(
        "Download nicht vollständig; später denselben Auftrag fortsetzen: " + str(last)
    ) from last


def execute_download(payload, work, log):
    settings = Settings(**payload["settings"])
    options = payload["download"]
    entry = options["entry"]
    if not entry.get("available") or not entry.get("locations"):
        raise ValueError("Keine vorhandene Audiodatei ausgewählt")
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    folder = output_folder(payload)
    folder.mkdir(parents=True, exist_ok=True)
    with TeddyCloud(settings, log) as client:
        source = fetch_taf(client, entry, work, log)
        log(
            "Prüfen",
            "TAF vollständig dekodieren und Audio-ID, SHA1, Ogg-Seiten und SHA256 prüfen",
        )
        try:
            info = verify_source(source, entry, settings)
        except Exception:
            source.rename(
                source.with_name(
                    "source.invalid-" + str(__import__("time").time_ns()) + ".taf"
                )
            )
            raise
        manifest_path = folder / "teddystudio-download.json"
        manifest = (
            json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest_path.exists()
            else {}
        )
        if manifest and (manifest.get("audio_id"), manifest.get("hash")) != (
            entry["audio_id"],
            entry["hash"],
        ):
            raise ValueError("Downloadordner enthält eine andere Audiodatei")
        saved = dict(manifest.get("outputs", {}))
        outputs = []

        def publish(path):
            from .jobs import _publish

            target = folder / path.name
            _publish(path, target, {"outputs": saved})
            saved[str(target)] = hashlib.sha256(target.read_bytes()).hexdigest()
            outputs.append(str(target))
            atomic_json(
                manifest_path,
                {
                    "audio_id": entry["audio_id"],
                    "hash": entry["hash"],
                    "outputs": saved,
                    "metadata": entry.get("aliases", []),
                    "server": client.url,
                    "overlay": payload.get("overlay", ""),
                },
            )
            return target

        if options.get("keep_taf", True) or options["format"] == "taf":
            publish(source)
        audio = []
        fmt = options["format"]
        need_audio = fmt != "taf" or options.get("import_collection", True)
        if need_audio:
            ogg = work / "audio.ogg"
            if (
                not ogg.exists()
                or hashlib.sha1(ogg.read_bytes()).hexdigest() != entry["hash"].lower()
            ):
                with (
                    source.open("rb") as src,
                    ogg.with_suffix(".partial.ogg").open("wb") as dst,
                ):
                    src.seek(4096)
                    shutil.copyfileobj(src, dst)
                ogg.with_suffix(".partial.ogg").replace(ogg)
            duration = media.probe(ogg, settings.ffprobe)["duration"]
            if fmt in {"ogg", "taf"}:
                audio = [(publish(ogg), duration)]
            else:
                if fmt not in {"mp3", "flac", "wav"}:
                    raise ValueError("Unbekanntes Downloadformat")
                pcm = work / "export.pcm"
                partial = pcm.with_suffix(".partial.pcm")
                media.concat_pcm([ogg], partial, settings.ffmpeg, settings.timeout)
                partial.replace(pcm)
                plan = [(0, duration)]
                if options.get("split_chapters"):
                    raw = source.read_bytes()[4096:]
                    pages = list(taf.pages(raw))
                    pre_skip = struct.unpack_from("<H", pages[0].packets[0], 10)[0]
                    starts = [0] + [
                        max(0, (pages[n - 1].granule - pre_skip) / 48000)
                        for n in info["chapterPages"][1:]
                    ]
                    if starts != sorted(set(starts)) or starts[-1] >= duration:
                        raise ValueError("Kapitelgrenzen nicht eindeutig")
                    plan = [
                        (s, (starts + [duration])[n + 1] - s)
                        for n, s in enumerate(starts)
                    ]
                for n, (start, length) in enumerate(plan, 1):
                    log("Konvertieren", f"Lokale Audiodatei {n}/{len(plan)}")
                    title = (
                        chapter_title(entry, n - 1, len(plan))
                        if options.get("split_chapters")
                        else entry.get("title") or "Audio"
                    )
                    target = work / chapter_filename(title, n, fmt)
                    actual = media.export_segment(
                        pcm,
                        target,
                        start,
                        length,
                        fmt,
                        settings.audio_bitrate,
                        settings.ffmpeg,
                        settings.timeout,
                    )
                    if abs(actual - length) > max(0.2, length * 0.001):
                        raise ValueError("Exportdauer stimmt nicht")
                    tag_export(target, title, entry, n)
                    audio.append((publish(target), actual))
        picture = unquote(entry.get("model", {}).get("pic", ""))
        cover = ""
        if (
            picture.startswith("/custom_img/")
            and not any(p in {"..", "."} for p in picture.split("/"))
            and "\\" not in picture
        ):
            try:
                from .covers import checked_image

                data = client.request("GET", quote(picture, safe="/"), stream=True)
                temp = work / "cover.jpg"
                with data, temp.open("wb") as stream:
                    size = 0
                    for chunk in data.iter_content(65536):
                        size += len(chunk)
                        if size > 20 * 1024 * 1024:
                            raise ValueError("Cover größer als 20 MiB")
                        stream.write(chunk)
                checked_image(temp)
                cover = str(publish(temp))
            except Exception as exc:
                log("Cover fehlt", "Audio bleibt gespeichert: " + str(exc))
    book = Book.from_dict(payload["book"])
    book.folder = str(folder)
    book.cover = cover
    book.cover_source = "Aus TeddyCloud" if cover else ""
    book.tracks = [
        Track(
            str(path),
            (
                chapter_title(entry, n, len(audio))
                if options.get("split_chapters") and fmt in {"mp3", "flac", "wav"}
                else book.title if len(audio) == 1 else f"Spur {n+1:03}"
            ),
            str(n + 1),
            duration,
        )
        for n, (path, duration) in enumerate(audio)
    ]
    book.status = "Lokal gespeichert"
    result = {
        "status": "Lokal gespeichert",
        "message": "TeddyCloud-Datei vollständig geprüft und lokal gespeichert",
        "outputs": outputs,
        "validation": info,
    }
    if options.get("import_collection", True):
        result["book"] = book.to_dict()
    return result


def tag_export(path, title, entry, number):
    import mutagen

    audio = mutagen.File(path, easy=True)
    if audio is None:
        raise ValueError("Audio-Metadaten konnten nicht geschrieben werden")
    if audio.tags is None:
        audio.add_tags()
    if Path(path).suffix.lower() == ".wav":
        from mutagen.id3 import TIT2, TALB, TRCK

        audio.tags.add(TIT2(encoding=3, text=title))
        audio.tags.add(TALB(encoding=3, text=entry.get("title") or title))
        audio.tags.add(TRCK(encoding=3, text=str(number)))
    else:
        audio["title"] = [title]
        audio["album"] = [entry.get("title") or title]
        audio["tracknumber"] = [str(number)]
    audio.save()
