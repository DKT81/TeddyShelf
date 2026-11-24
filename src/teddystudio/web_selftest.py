"""Optional acceptance fixture using official external tools and local media only."""

from dataclasses import asdict
from functools import partial
from pathlib import Path
import http.server
import threading
import time

from . import media, web_downloads
from .queue import Worker
from .web_tools import ToolManager


def run(root, store, settings, tools_root):
    """No internet sources: the caller explicitly supplies installed test tools."""
    tools = ToolManager(tools_root).snapshot()
    fixtures = Path(root) / "web-fixtures"
    fixtures.mkdir(exist_ok=True)
    for number in (1, 2, 3):
        media.run(
            [
                media.binary("ffmpeg", settings.ffmpeg),
                "-v",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=c=teal:s=160x120:d=2.2",
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency={number * 220}:duration=2.2",
                "-c:v",
                "libx264",
                "-c:a",
                "aac",
                "-shortest",
                fixtures / f"{number}.mp4",
            ]
        )
    (fixtures / "index.html").write_text(
        "<html><head><title>Synthetic playlist</title></head><body>"
        + "".join(f'<video controls src="/{n}.mp4"></video>' for n in (1, 2, 3))
        + "</body></html>",
        encoding="utf-8",
    )

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(Quiet, directory=str(fixtures))
    )
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    try:
        preview = web_downloads.preview(
            f"http://127.0.0.1:{server.server_port}/index.html", tools, settings
        )
        assert len(preview["entries"]) == 3
        audio = web_downloads.make_payloads(
            preview,
            [preview["entries"][0], preview["entries"][2]],
            Path(root) / "web-audio",
            "audio",
            tools,
            settings,
        )
        video = web_downloads.make_payloads(
            preview,
            preview["entries"][1:2],
            Path(root) / "web-video",
            "video",
            tools,
            settings,
        )
        identifiers = [
            store.add_job(web_downloads.JOB_KIND, payload) for payload in audio + video
        ]
        worker = Worker(store, settings=settings)
        worker.start()
        try:
            deadline = time.monotonic() + 90
            while worker.running and time.monotonic() < deadline:
                time.sleep(0.05)
            assert not worker.running, "Web queue timed out"
            rows = [job for job in store.jobs() if job["id"] in identifiers]
            assert len(rows) == 3 and all(
                job["status"] == "Lokal gespeichert" for job in rows
            ), store.events()
        finally:
            if worker.running:
                worker.cancel_all()
                worker.thread.join(20)
        results = [
            web_downloads.saved_result(payload["web"]) for payload in audio + video
        ]
        assert all(Path(result["path"]).is_file() for result in results)
        return {
            "passed": True,
            "tools": {name: value["version"] for name, value in tools.items()},
            "playlist_entries": 3,
            "audio_indices": [1, 3],
            "video_indices": [2],
            "batch": preview["id"],
            "sha256": [result["sha256"] for result in results],
        }
    finally:
        server.shutdown()
        server.server_close()
        serving.join(timeout=3)
