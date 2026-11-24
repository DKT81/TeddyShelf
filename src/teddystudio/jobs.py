"""Durable isolated workers with per-title checkpoints and immutable input snapshots."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import contextlib
import hashlib
import json

from . import covers, media, taf
from .client import TeddyCloud
from .domain import Book, NamingProfile, source_fingerprint
from .storage import Settings, atomic_json, data_directory


@contextlib.contextmanager
def exclusive_lock(path):
    """OS-level lock also excludes a second application process from the same journal."""
    stream = Path(path).open("a+b")
    try:
        stream.seek(0)
        if stream.read(1) == b"":
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        import os

        if os.name == "nt":
            import msvcrt

            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        stream.close()


from .queue import Worker


@contextlib.contextmanager
def server_access(url, log):
    """Only one transaction per destination; local preparation remains parallel."""
    import time

    lock_path = data_directory() / (
        "server-"
        + hashlib.sha256(url.rstrip("/").casefold().encode()).hexdigest()
        + ".lock"
    )
    log(
        "Wartet auf TeddyCloud",
        "Übertragung und Modelländerungen werden je Zielserver serialisiert",
    )
    while True:
        lock = exclusive_lock(lock_path)
        try:
            lock.__enter__()
            break
        except OSError:
            time.sleep(0.1)
    try:
        yield
    finally:
        lock.__exit__(None, None, None)


def execute(kind, payload, work, log):
    """Decoded audio is disposable even when validation or conversion fails."""
    from .workspace import clean_pcm

    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    with exclusive_lock(work / "audio-work.lock"):
        try:
            return _execute(kind, payload, work, log)
        finally:
            count, size, errors = clean_pcm(work)
            if count or errors:
                log(
                    "Arbeitsdateien bereinigen",
                    f"{count} PCM-Dateien · {size / 1048576:.1f} MiB freigegeben"
                    + (" · " + "; ".join(errors) if errors else ""),
                )


def _execute(kind, payload, work, log):
    if kind == "Web-Download":
        from .web_downloads import execute_web

        return execute_web(payload, work, log)
    if kind == "Aus TeddyCloud holen":
        from .downloads import execute_download

        return execute_download(payload, work, log)
    if kind == "Tonie / Tag zuordnen":
        from .tags import assign_tag

        work = Path(work)
        work.mkdir(parents=True, exist_ok=True)
        return assign_tag(payload, work, log)
    settings = Settings(**payload["settings"])
    book = Book.from_dict(payload["book"])
    profile = NamingProfile(**payload["profile"])
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    log("Prüfen", book.title)
    from .library import resolve_legacy_order

    if resolve_legacy_order(book):
        log(
            "Prüfen",
            "Alte Playlist-Warnung automatisch aufgelöst; alle Quellen eindeutig zugeordnet.",
        )
    if book.order_pending:
        raise ValueError(
            "Die Spurenreihenfolge wurde geändert. Bitte nur diese Änderung in der Sammlung bestätigen und einen neuen Auftrag anlegen."
        )
    if not book.tracks or any(not Path(t.path).is_file() for t in book.tracks):
        raise ValueError("Quelldatei nicht gefunden")
    if any(t.error for t in book.tracks):
        raise ValueError(
            "Audio oder Reihenfolge ungeklärt: "
            + "; ".join(dict.fromkeys(t.error for t in book.tracks if t.error))
            + " In der Sammlung unter Spuren & Reihenfolge prüfen und bestätigen; anschließend einen neuen Auftrag anlegen."
        )
    naming = profile.render(book)
    fingerprint = source_fingerprint(book)
    state_path = work / "state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    if state and state.get("fingerprint") != fingerprint:
        raise ValueError(
            "Quelle seit Auftragsbeginn verändert. Bitte neuen Auftrag mit aktueller Quelle erstellen."
        )
    state["fingerprint"] = fingerprint
    atomic_json(state_path, state)
    expected = sum(
        media.probe(t.path, settings.ffprobe)["duration"] for t in book.tracks
    )
    pcm = work / "source.pcm"
    if (
        not pcm.exists()
        or state.get("pcm_hash") != _hash(pcm)
        or abs(pcm.stat().st_size / 192000 - expected) > max(0.15, expected * 0.001)
    ):
        log("Konvertieren", "Quellen vollständig dekodieren")
        temporary = pcm.with_suffix(".partial.pcm")
        media.concat_pcm(
            [t.path for t in book.tracks], temporary, settings.ffmpeg, settings.timeout
        )
        temporary.replace(pcm)
        state["pcm_hash"] = _hash(pcm)
        atomic_json(state_path, state)
    duration = pcm.stat().st_size / 192000
    if abs(duration - expected) > max(0.15, expected * 0.001):
        raise ValueError("Dekodierte Gesamtdauer stimmt nicht mit den Quellen überein")
    cover_ext = ".png" if settings.cover_format == "PNG" else ".jpg"
    try:
        cover = covers.prepare(book.cover, work / ("cover" + cover_ext), settings)
    except (OSError, ValueError) as exc:
        log(
            "Cover fehlt",
            f"Bild nicht verwendbar; Audio wird trotzdem verarbeitet: {exc}",
        )
        cover = covers.prepare("", work / ("cover" + cover_ext), settings)

    def make_taf(target, chapters, source=pcm):
        if target.exists():
            try:
                if state.get("artifacts", {}).get(target.name) != _hash(target):
                    raise ValueError(
                        "TAF-Zwischenergebnis ohne passende Herkunfts-Prüfsumme"
                    )
                return taf.validate(
                    target,
                    settings.ffmpeg,
                    source.stat().st_size / 192000,
                    len(chapters),
                    settings.timeout,
                )
            except Exception:
                target.rename(
                    target.with_suffix(
                        ".invalid-" + str(__import__("time").time_ns()) + ".taf"
                    )
                )
        log("Konvertieren", target.name)
        taf.create(
            source,
            target,
            chapters,
            settings.ffmpeg,
            settings.taf_bitrate,
            settings.timeout,
        )
        info = taf.validate(
            target,
            settings.ffmpeg,
            source.stat().st_size / 192000,
            len(chapters),
            settings.timeout,
        )
        state.setdefault("artifacts", {})[target.name] = _hash(target)
        atomic_json(state_path, state)
        return info

    if kind in {"Import", "Prüfung"}:
        target = work / (naming["filename"] + ".taf")
        starts, current = [], 0
        for track in book.tracks:
            starts.append(current)
            current += media.probe(track.path, settings.ffprobe)["duration"]
        if kind == "Prüfung":
            # A verification job does not encode or mutate the server.
            prepared = Path(payload.get("taf", ""))
            if not prepared.is_file():
                raise ValueError(
                    "Für die reine Prüfung ist eine vorhandene TAF erforderlich."
                )
            target = prepared
            if (
                payload.get("expected_taf_hash")
                and _hash(target) != payload["expected_taf_hash"]
            ):
                raise ValueError(
                    "Lokale TAF wurde seit dem bestätigten Import verändert"
                )
            info = taf.validate(
                target, settings.ffmpeg, duration, len(starts), settings.timeout
            )
        else:
            info = make_taf(target, starts)
        with server_access(settings.url, log), TeddyCloud(settings, log) as client:
            entry = client.import_book(
                book, naming, target, info, cover, kind == "Prüfung", settings.existing
            )
        atomic_json(
            work / "result.json",
            {"entry": entry, "validation": info, "fingerprint": fingerprint},
        )
        return {
            "status": "Bestätigt",
            "message": "Audio, Header, Cover und Custom-Tonie verifiziert"
            + (" · ohne Cover" if not cover else ""),
            "model": naming["model"],
            "fingerprint": fingerprint,
        }

    options = payload["audio"]
    plan = media.audio_plan(
        [media.probe(t.path, settings.ffprobe)["duration"] for t in book.tracks],
        options["seconds"],
        options["mode"],
    )
    destination = Path(options["destination"])
    source_root = Path(settings.source).resolve()
    if (
        destination.resolve() == source_root
        or source_root in destination.resolve().parents
    ):
        raise ValueError("Exportziel muss außerhalb der eingescannten Sammlung liegen.")
    if any(
        destination.resolve() == Path(t.path).parent.resolve()
        or Path(t.path).parent.resolve() in destination.resolve().parents
        for t in book.tracks
    ):
        raise ValueError("Exportziel muss außerhalb der Quellordner liegen.")
    destination.mkdir(parents=True, exist_ok=True)
    manifest_path = destination / (naming["filename"] + ".teddystudio.json")
    old_manifest = (
        json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    )
    if old_manifest and old_manifest.get("fingerprint") != fingerprint:
        raise ValueError("Exportziel enthält Ergebnisse einer anderen Quelle.")
    outputs = []

    def publish_output(source):
        final = destination / source.name
        _publish(source, final, old_manifest)
        outputs.append(str(final))
        old_manifest.update({"fingerprint": fingerprint, "options": options})
        old_manifest.setdefault("outputs", {})[str(final)] = _hash(final)
        atomic_json(manifest_path, old_manifest)

    if options["mode"] in media.AUDIO_MODES[1:]:
        target = work / (naming["filename"] + ".taf")
        info = make_taf(target, [start for start, _ in plan])
        if options["target"] != "Nur lokal":
            with server_access(settings.url, log), TeddyCloud(settings, log) as client:
                client.import_book(
                    book, naming, target, info, cover, existing=settings.existing
                )
        if options["target"] != "Nur TeddyCloud":
            publish_output(target)
    else:
        names = [
            profile.render(book, index + 1)["split_filename"]
            for index in range(len(plan))
        ]
        if len(set(name.casefold() for name in names)) != len(names):
            raise ValueError(
                "Teil-Dateinamen kollidieren. Die Vorlage muss {teil:03} enthalten."
            )
        total = 0
        for index, (start, length) in enumerate(plan, 1):
            log("Aufteilen", f"Teil {index} von {len(plan)}")
            target = work / (names[index - 1] + "." + options["format"])
            if target.exists() and state.get("artifacts", {}).get(target.name) == _hash(
                target
            ):
                total += media.decoded_duration(
                    target, settings.ffmpeg, timeout=settings.timeout
                )
            else:
                total += media.export_segment(
                    pcm,
                    target,
                    start,
                    length,
                    options["format"],
                    settings.audio_bitrate,
                    settings.ffmpeg,
                    settings.timeout,
                )
            if settings.embed_cover and cover:
                covers.embed_image(target, cover)
            state.setdefault("artifacts", {})[target.name] = _hash(target)
            atomic_json(state_path, state)
            if options["target"] != "Nur TeddyCloud":
                publish_output(target)
            if options["target"] != "Nur lokal":
                part_book = Book.from_dict(book.to_dict())
                part_book.title += f" – Teil {index:03}"
                part_book.episode = None
                part_naming = profile.render(part_book, index)
                # The suffix preserves uniqueness even for templates ignoring the title.
                part_naming["model"] = naming["model"] + f"-teil-{index:03}"
                part_naming["filename"] = names[index - 1]
                for key in ("title", "episodes"):
                    suffix = f" – Teil {index:03}"
                    if not part_naming[key].endswith(suffix):
                        part_naming[key] += suffix
                segment_pcm = work / f"part-{index}.pcm"
                with pcm.open("rb") as source, segment_pcm.open("wb") as result:
                    source.seek(round(start * 48000) * 4)
                    remaining = round(length * 48000) * 4
                    while remaining:
                        chunk = source.read(min(1024 * 1024, remaining))
                        if not chunk:
                            break
                        result.write(chunk)
                        remaining -= len(chunk)
                part_taf = work / (names[index - 1] + ".taf")
                info = make_taf(part_taf, [0], segment_pcm)
                with (
                    server_access(settings.url, log),
                    TeddyCloud(settings, log) as client,
                ):
                    client.import_book(
                        part_book,
                        part_naming,
                        part_taf,
                        info,
                        cover,
                        existing=settings.existing,
                    )
        if abs(total - duration) > max(0.2 * len(plan), duration * 0.001):
            raise ValueError(
                "Gesamtdauer der Teile stimmt nicht mit der Quelle überein"
            )
    hashes = {p: _hash(Path(p)) for p in outputs}
    atomic_json(
        manifest_path,
        {
            "fingerprint": fingerprint,
            "outputs": hashes,
            "parts": len(plan),
            "source_duration": duration,
            "options": options,
        },
    )
    return {
        "status": (
            "Lokal gespeichert" if options["target"] == "Nur lokal" else "Bestätigt"
        ),
        "message": f"{len(plan)} Teile · Gesamtdauer geprüft",
    }


def _hash(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _publish(source, target, manifest):
    import shutil

    if target.exists():
        if _hash(target) == _hash(source):
            return
        if manifest.get("outputs", {}).get(str(target)) != _hash(target):
            raise ValueError(
                "Vorhandene fremde oder geänderte Datei wird nicht überschrieben: "
                + str(target)
            )
    temporary = target.with_name(target.name + ".partial")
    shutil.copyfile(source, temporary)
    if _hash(source) != _hash(temporary):
        raise ValueError("Export-Prüfsumme stimmt nicht")
    temporary.replace(target)
