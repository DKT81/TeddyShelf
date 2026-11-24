"""External yt-dlp jobs; no Python yt-dlp dependency or shell interpolation."""

from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlsplit
import hashlib
import json
import math
import os
import queue
import subprocess
import threading
import time
import uuid

from . import media
from .domain import Book, Track, ascii_name
from .processes import ProcessGuard
from .storage import Settings, atomic_json
from .web_tools import ToolManager, sha256

JOB_KIND = "Web-Download"
PROGRESS = "TS_PROGRESS:"
FINAL = "TS_FILE:"


def web_url(value):
    value = str(value).strip()
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise ValueError(
            "Bitte eine gültige HTTP-/HTTPS-URL ohne Zugangsdaten eingeben"
        )
    return value


def command_base(tools, settings):
    for tool in tools.values():
        ToolManager.verify(tool)
    return [
        tools["yt-dlp"]["path"],
        "--ignore-config",
        "--no-plugin-dirs",
        "--no-js-runtimes",
        "--js-runtimes",
        "deno:" + tools["deno"]["path"],
        "--encoding",
        "utf-8",
        "--no-colors",
        "--socket-timeout",
        "30",
        "--retries",
        "3",
        "--fragment-retries",
        "3",
        "--ffmpeg-location",
        str(Path(media.binary("ffmpeg", settings.ffmpeg)).parent),
    ]


def stream_process(
    command, consume, timeout, cancelled=lambda: False, max_output=64_000_000
):
    """Drain output on a reader thread; timeout/cancel also kills FFmpeg children."""
    process = subprocess.Popen(
        [str(value) for value in command],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        start_new_session=os.name != "nt",
    )
    guard = None
    lines = queue.Queue(maxsize=256)
    stop_reader = threading.Event()

    def read():
        try:
            while not stop_reader.is_set():
                line = process.stdout.readline(64_000_001)
                if not line:
                    break
                while not stop_reader.is_set():
                    try:
                        lines.put(line, timeout=0.1)
                        break
                    except queue.Full:
                        pass
        finally:
            process.stdout.close()

    reader = threading.Thread(target=read, daemon=True)
    try:
        guard = ProcessGuard(process)
        reader.start()
        deadline, total, tail = time.monotonic() + timeout, 0, []
        while reader.is_alive() or not lines.empty():
            if cancelled():
                raise InterruptedError("Web-Vorschau abgebrochen")
            if time.monotonic() > deadline:
                raise TimeoutError("Web-Werkzeug überschreitet das Zeitlimit")
            try:
                raw = lines.get(timeout=0.1)
            except queue.Empty:
                continue
            total += len(raw)
            if total > max_output:
                raise ValueError("Web-Werkzeug liefert zu viele Ausgabedaten")
            line = raw.decode("utf-8", "replace").rstrip()
            consume(line)
            tail = (tail + [line])[-20:]
        remaining = max(0.1, deadline - time.monotonic())
        if process.wait(timeout=remaining):
            raise RuntimeError("yt-dlp: " + "\n".join(tail)[-5000:])
    finally:
        from .queue import terminate_tree

        stop_reader.set()
        terminate_tree(process, guard)
        if reader.ident:
            reader.join(timeout=3)


def preview(url, tools, settings, cancelled=lambda: False):
    output = []
    command = command_base(tools, settings) + [
        "--flat-playlist",
        "--dump-single-json",
        "--skip-download",
        "--ignore-errors",
        "--",
        web_url(url),
    ]
    stream_process(command, output.append, min(settings.timeout, 300), cancelled)
    data = next((json.loads(line) for line in output if line.startswith("{")), None)
    if not isinstance(data, dict):
        raise ValueError("Keine verwertbare Web-Vorschau erhalten")
    originals = (
        data.get("entries")
        if data.get("_type") in {"playlist", "multi_video"}
        else [data]
    )
    if not isinstance(originals, list) or len(originals) > 5000:
        raise ValueError(
            "Die Playlist ist ungültig oder enthält mehr als 5000 Einträge"
        )
    entries = []
    for position, entry in enumerate(originals, 1):
        entry = entry or {}
        target = (
            entry.get("webpage_url")
            or entry.get("url")
            or (url if len(originals) == 1 else "")
        )
        # Generic HTML playlists can repeat the containing page as webpage_url.
        # Use that entry's actual media URL rather than downloading the entire
        # containing playlist once for every chapter.
        if (
            len(originals) > 1
            and entry.get("url")
            and target in {url, data.get("webpage_url"), data.get("original_url")}
        ):
            target = entry["url"]
        if (
            target
            and not urlsplit(target).scheme
            and "youtube" in str(data.get("extractor", "")).lower()
        ):
            target = "https://www.youtube.com/watch?v=" + str(entry.get("id") or target)
        error = ""
        try:
            target = web_url(target)
        except ValueError:
            error = "Eintrag nicht verfügbar"
        if entry.get("availability") in {
            "private",
            "premium_only",
            "subscriber_only",
            "needs_auth",
        }:
            error = "Eintrag nicht frei verfügbar"
        entries.append(
            {
                "index": position,
                "id": str(entry.get("id") or position),
                "title": str(entry.get("title") or "Unbekannter Titel"),
                "url": target,
                "duration": entry.get("duration"),
                "error": error,
            }
        )
    return {
        "id": uuid.uuid4().hex,
        "url": web_url(url),
        "title": str(
            data.get("title") or entries[0]["title"] if entries else "Web-Download"
        ),
        "entries": entries,
    }


def output_path(options):
    root = Path(options["destination"]).resolve()
    entry = options["entry"]
    title = entry["title"][:65]
    try:
        title = ascii_name(title)
    except ValueError:
        title = "web-audio" if options["mode"] == "audio" else "web-video"
    token = hashlib.sha256(web_url(entry["url"]).encode()).hexdigest()[:10]
    extension = ".mp3" if options["mode"] == "audio" else ".mp4"
    return root / f"{int(entry['index']):04}-{title}-{token}{extension}"


def make_payloads(snapshot, entries, destination, mode, tools, settings):
    if mode not in {"audio", "video"}:
        raise ValueError("Unbekannter Web-Downloadmodus")
    batch = snapshot["id"]
    result = []
    for entry in sorted(entries, key=lambda item: item["index"]):
        if entry.get("error"):
            continue
        web_url(entry["url"])
        result.append(
            {
                "book": Book(
                    "web-" + batch + "-" + str(entry["index"]),
                    "",
                    f"{entry['index']:04} · {entry['title']}",
                ).to_dict(),
                "settings": asdict(settings),
                "profile": asdict(settings.profile()),
                "web": {
                    "batch": batch,
                    "playlist": snapshot["title"],
                    "entry": entry,
                    "destination": str(Path(destination).resolve()),
                    "mode": mode,
                    "tools": tools,
                },
            }
        )
    return result


def saved_result(options):
    target = output_path(options)
    receipt = target.with_suffix(target.suffix + ".teddyshelf-web.json")
    if not target.exists() and not receipt.exists():
        return None
    if receipt.is_file() and not target.exists():
        previous = json.loads(receipt.read_text("utf-8"))
        if (
            previous.get("url") == options["entry"]["url"]
            and previous.get("mode") == options["mode"]
        ):
            return None
    if not target.is_file() or not receipt.is_file():
        raise ValueError(
            "Zieldatei ohne gültigen Web-Downloadnachweis; anderen Zielordner wählen"
        )
    state = json.loads(receipt.read_text("utf-8"))
    if (
        state.get("url") != options["entry"]["url"]
        or state.get("mode") != options["mode"]
        or state.get("sha256") != sha256(target)
    ):
        raise ValueError(
            "Vorhandener Web-Download wurde verändert; Datei wird nicht überschrieben"
        )
    return {"path": str(target), **state}


def execute_web(payload, work, log):
    options = payload["web"]
    # The group identifier is generated locally, never taken from website data.
    # Enforce that contract before creating or cleaning a staging directory.
    try:
        valid_batch = uuid.UUID(options["batch"]).hex == options["batch"]
        valid_index = 1 <= int(options["entry"]["index"]) <= 5000
    except (ValueError, TypeError, AttributeError):
        valid_batch = valid_index = False
    if not valid_batch or not valid_index:
        raise ValueError("Ungültiger Web-Arbeitsordner")
    settings = Settings(**payload["settings"])
    if options["mode"] not in {"audio", "video"}:
        raise ValueError("Unbekannter Web-Downloadmodus")
    target = output_path(options)
    target.parent.mkdir(parents=True, exist_ok=True)
    result = saved_result(options)
    if result:
        log("Bereits geprüft", target.name)
    else:
        # Resolve the active tools once at execution start. Updates can fix queued
        # or retried jobs, while this local snapshot stays fixed for the entire
        # running process. Explicit test/migration snapshots work without a pool.
        manager = ToolManager()
        if manager.state():
            options = {**options, "tools": manager.snapshot()}
        tools = options["tools"]
        command = command_base(tools, settings)
        # Partial media lives in the selected destination, not on the system disk.
        stage = (
            target.parent
            / ".teddyshelf-web"
            / options["batch"]
            / str(options["entry"]["index"])
        )
        if not stage.resolve().is_relative_to(target.parent):
            raise ValueError("Ungültiger Web-Arbeitsordner")
        if any(
            path.is_symlink() for path in (stage, stage.parent, stage.parent.parent)
        ):
            raise ValueError("Ungültiger Web-Arbeitsordner")
        stage.mkdir(parents=True, exist_ok=True)
        command += [
            "--no-playlist",
            "--newline",
            "--no-simulate",
            "--continue",
            "--progress-delta",
            "1",
            "--progress-template",
            "download:" + PROGRESS + "%(progress)j",
            "--print",
            "after_move:" + FINAL + "%(filepath)j",
            "--output",
            str(stage / "media.%(ext)s"),
        ]
        if options["mode"] == "audio":
            command += [
                "--format",
                "bestaudio/best",
                "--extract-audio",
                "--audio-format",
                "mp3",
                "--audio-quality",
                "0",
            ]
        else:
            command += [
                "--format",
                "bv*+ba/b",
                "--merge-output-format",
                "mp4",
                "--recode-video",
                "mp4",
            ]
        command += ["--", web_url(options["entry"]["url"])]
        files = []
        log(
            "Herunterladen",
            tools["yt-dlp"]["version"] + " · " + options["entry"]["title"],
            0,
        )

        def consume(line):
            if line.startswith(FINAL):
                files.append(Path(json.loads(line[len(FINAL) :])).resolve())
            elif line.startswith(PROGRESS):
                progress = json.loads(line[len(PROGRESS) :])
                total = (
                    progress.get("total_bytes")
                    or progress.get("total_bytes_estimate")
                    or 0
                )
                downloaded = progress.get("downloaded_bytes") or 0
                fraction = min(0.9, max(0, downloaded / total * 0.9)) if total else 0
                log("Herunterladen", f"{downloaded} / {total} Bytes", fraction)
            elif line:
                log("Web-Werkzeug", line[-3000:])

        stream_process(command, consume, settings.timeout, max_output=32_000_000)
        if (
            len(files) != 1
            or not files[0].is_relative_to(stage.resolve())
            or not files[0].is_file()
        ):
            raise ValueError("yt-dlp meldet keine eindeutige fertige Mediendatei")
        source = files[0]
        if source.suffix.lower() != target.suffix.lower():
            raise ValueError("Ausgabeformat passt nicht zur gewählten Vorschau")
        try:
            info = (
                media.probe(source, settings.ffprobe)
                if options["mode"] == "audio"
                else {}
            )
            if options["mode"] == "video":
                video = json.loads(
                    media.run(
                        [
                            media.binary("ffprobe", settings.ffprobe),
                            "-v",
                            "error",
                            "-show_streams",
                            "-show_format",
                            "-of",
                            "json",
                            source,
                        ],
                        120,
                    )
                )
                if not any(
                    s.get("codec_type") == "video" for s in video.get("streams", [])
                ):
                    raise ValueError("Die Videodatei enthält keine Videospur")
                info["duration"] = float(video.get("format", {}).get("duration", 0))
                if not math.isfinite(info["duration"]) or info["duration"] <= 0:
                    raise ValueError("Ungültige Videodauer")
            log("Prüfen", "Mediendatei vollständig dekodieren", 0.95)
            if options["mode"] == "audio":
                media.decoded_duration(
                    source, settings.ffmpeg, timeout=settings.timeout
                )
            else:
                media.run(
                    [
                        media.binary("ffmpeg", settings.ffmpeg),
                        "-v",
                        "error",
                        "-xerror",
                        "-i",
                        source,
                        "-map",
                        "0:v:0",
                        "-map",
                        "0:a?",
                        "-f",
                        "null",
                        "-",
                    ],
                    settings.timeout,
                )
        except Exception:
            # This is an owned, finished staging file, never an existing user file.
            # Discard invalid media so a targeted retry actually downloads anew.
            source.unlink(missing_ok=True)
            raise
        result = {
            "url": options["entry"]["url"],
            "mode": options["mode"],
            "sha256": sha256(source),
            "duration": info["duration"],
            "title": options["entry"]["title"],
            "index": options["entry"]["index"],
            "engine": tools["yt-dlp"]["version"],
        }
        # Reserve the destination rather than silently replacing an unrelated file.
        # A receipt is written first; interrupted publication remains recoverable.
        receipt = target.with_suffix(target.suffix + ".teddyshelf-web.json")
        if target.exists():
            raise ValueError("Web-Zieldatei wurde inzwischen angelegt")
        atomic_json(receipt, result)
        try:
            if os.name == "nt":
                os.rename(source, target)
            else:
                os.link(source, target)
        except FileExistsError:
            raise ValueError("Web-Zieldatei wurde inzwischen angelegt")
        if source.exists():
            source.unlink()
        result = {"path": str(target), **result}
        for partial in stage.glob("*"):
            if partial.is_file() and not partial.is_symlink():
                partial.unlink()
        stage.rmdir()
    atomic_json(Path(work) / "web-result.json", result)
    return {
        "status": "Lokal gespeichert",
        "message": result["path"],
        "web_result": result,
    }
