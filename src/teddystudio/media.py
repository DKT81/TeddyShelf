"""Audio probing, accurate splitting and waveforms, isolated from the GUI."""

from __future__ import annotations

from array import array
from pathlib import Path
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile


def binary(name: str, configured="") -> str:
    candidates = [Path(configured)] if configured else []
    candidates.extend(
        [
            Path(getattr(sys, "_MEIPASS", Path(__file__).parents[2]))
            / "tools"
            / (name + ".exe"),
            Path(__file__).parents[2] / "tools" / (name + ".exe"),
        ]
    )
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate.resolve())
    found = shutil.which(name)
    if not found:
        raise FileNotFoundError(
            f"{name} fehlt. Bitte unter Einstellungen → Audio & Schnitt auswählen."
        )
    return found


def run(command, timeout=1800, binary_output=False):
    """No shell; hide console windows and always reap timed-out child processes."""
    process = subprocess.Popen(
        [str(x) for x in command],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except BaseException:
        process.kill()
        process.communicate()
        raise
    if process.returncode:
        raise RuntimeError(
            stderr.decode("utf-8", "replace")[-3000:]
            or f"Werkzeug beendet mit {process.returncode}"
        )
    return stdout if binary_output else stdout.decode("utf-8", "replace")


def probe(path, ffprobe="") -> dict:
    result = json.loads(
        run(
            [
                binary("ffprobe", ffprobe),
                "-v",
                "error",
                "-show_format",
                "-show_streams",
                "-of",
                "json",
                path,
            ],
            120,
        )
    )
    streams = [s for s in result.get("streams", []) if s.get("codec_type") == "audio"]
    duration = float(result.get("format", {}).get("duration", 0))
    if not streams or not math.isfinite(duration) or duration <= 0:
        raise ValueError(f"Keine gültige Audioquelle: {path}")
    return {
        "duration": duration,
        "tags": {
            **result.get("format", {}).get("tags", {}),
            **streams[0].get("tags", {}),
        },
        "streams": streams,
    }


def decoded_duration(path, ffmpeg="", taf=False, timeout=1800):
    args = [binary("ffmpeg", ffmpeg), "-v", "error", "-xerror"]
    if taf:
        args += ["-f", "ogg", "-skip_initial_bytes", "4096"]
    result = run(
        args + ["-i", path, "-map", "0:a:0", "-progress", "pipe:1", "-f", "null", "-"],
        timeout,
    )
    times = [
        int(line.split("=", 1)[1])
        for line in result.splitlines()
        if line.startswith("out_time_us=")
        and line.split("=", 1)[1].lstrip("-").isdigit()
    ]
    if not times or times[-1] <= 0:
        raise ValueError("Die Audiodatei konnte nicht vollständig dekodiert werden.")
    return times[-1] / 1_000_000


def concat_pcm(paths, output, ffmpeg="", timeout=1800):
    """Normalize sources separately then concatenate PCM: mixed codecs and rates are safe."""
    with Path(output).open("wb") as destination:
        for path in paths:
            with tempfile.TemporaryDirectory(prefix="teddystudio-") as directory:
                part = Path(directory) / "part.pcm"
                run(
                    [
                        binary("ffmpeg", ffmpeg),
                        "-v",
                        "error",
                        "-xerror",
                        "-y",
                        "-i",
                        path,
                        "-map",
                        "0:a:0",
                        "-ac",
                        "2",
                        "-ar",
                        "48000",
                        "-f",
                        "s16le",
                        part,
                    ],
                    timeout,
                )
                with part.open("rb") as source:
                    shutil.copyfileobj(source, destination)


def split_plan(duration, seconds):
    if not math.isfinite(seconds) or seconds <= 0 or duration <= 0:
        raise ValueError("Abschnittslänge und Audiodauer müssen größer als null sein.")
    count = math.ceil(round(duration / seconds, 10))
    if count > 10000:
        raise ValueError("Mehr als 10.000 Teile: Bitte größere Abschnittslänge wählen.")
    return [
        (index * seconds, min(seconds, duration - index * seconds))
        for index in range(count)
    ]


def export_segment(
    pcm, output, start, duration, fmt="mp3", bitrate=128, ffmpeg="", timeout=1800
):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.stem + ".partial" + output.suffix)
    codec = {
        "mp3": ["-c:a", "libmp3lame", "-b:a", f"{bitrate}k"],
        "opus": ["-c:a", "libopus", "-b:a", f"{bitrate}k"],
        "flac": ["-c:a", "flac"],
        "wav": ["-c:a", "pcm_s16le"],
    }[fmt]
    run(
        [
            binary("ffmpeg", ffmpeg),
            "-v",
            "error",
            "-xerror",
            "-y",
            "-f",
            "s16le",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-ss",
            str(start),
            "-i",
            pcm,
            "-t",
            str(duration),
            *codec,
            temporary,
        ],
        timeout,
    )
    actual = decoded_duration(temporary, ffmpeg, timeout=timeout)
    if abs(actual - duration) > max(0.15, duration * 0.001):
        raise ValueError(
            f"Schnittdauer stimmt nicht: {actual:.3f} statt {duration:.3f} s"
        )
    temporary.replace(output)
    return actual


AUDIO_MODES = [
    "Separate Dateien / Custom-Tonies",
    "TAF mit Kapiteln",
    "TAF als Playlist (Originaldateien vollständig)",
]


def audio_plan(durations, seconds, mode):
    """One shared plan for GUI and execution; playlist boundaries follow sources."""
    if mode not in AUDIO_MODES:
        raise ValueError("Unbekannte Audioverarbeitung")
    if mode != AUDIO_MODES[2]:
        return split_plan(sum(durations), seconds)
    result, start = [], 0.0
    for duration in durations:
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("Jede Playlist-Datei benötigt eine gültige positive Dauer")
        result.append((start, duration))
        start += duration
    return result


def waveform(paths, ffmpeg="", points=900):
    """Measure a streamed 8-kHz signal; downsampling to display rate loses audible peaks."""
    total = sum(probe(path)["duration"] for path in paths)
    step = max(1, math.ceil(total * 8000 / points))
    peaks, peak, filled = [], 0, 0
    for path in paths:
        with tempfile.TemporaryFile() as errors:
            process = subprocess.Popen(
                [
                    binary("ffmpeg", ffmpeg),
                    "-v",
                    "error",
                    "-i",
                    str(path),
                    "-map",
                    "0:a:0",
                    "-ac",
                    "1",
                    "-ar",
                    "8000",
                    "-f",
                    "s16le",
                    "pipe:1",
                ],
                stdout=subprocess.PIPE,
                stderr=errors,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            try:
                while raw := process.stdout.read(16384):
                    values, index = array("h", raw), 0
                    while index < len(values):
                        count = min(step - filled, len(values) - index)
                        peak = max(
                            peak,
                            max(
                                (abs(v) for v in values[index : index + count]),
                                default=0,
                            ),
                        )
                        filled += count
                        index += count
                        if filled == step:
                            peaks.append(peak / 32768)
                            peak, filled = 0, 0
                if process.wait(timeout=30):
                    errors.seek(0)
                    raise RuntimeError(errors.read().decode("utf-8", "replace")[-2000:])
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait()
                process.stdout.close()
    if filled:
        peaks.append(peak / 32768)
    return peaks[:points]
