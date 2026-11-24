"""Offline contracts for web jobs, executable updates and playlist handoff."""

from dataclasses import asdict
from pathlib import Path
import hashlib
import json
import shutil
import sys
import time

import pytest
from PySide6.QtCore import Qt

from teddystudio import media, web_downloads as web
from teddystudio.storage import Settings, Store, atomic_json
from teddystudio.web_tools import ToolManager, sha256, release_url


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("TEDDYSHELF_DATA", str(tmp_path / "data"))
    return Settings(source=str(tmp_path / "source"), export=str(tmp_path / "export"))


def snapshot():
    return {
        "id": "a" * 32,
        "title": "Demo playlist",
        "url": "https://example.org/list",
        "entries": [
            {
                "index": n,
                "id": str(n),
                "title": "Chapter " + str(n),
                "url": f"https://example.org/media/{n}",
                "duration": 4.2,
                "error": "",
            }
            for n in (1, 2, 3)
        ],
    }


@pytest.mark.parametrize(
    "url",
    [
        "file:///tmp/audio",
        "--exec=bad",
        "ftp://example.org/x",
        "https://user:secret@example.org/x",
        "",
    ],
)
def test_reject_non_web_urls(url):
    with pytest.raises(ValueError):
        web.web_url(url)


def test_preview_keeps_order_and_unavailable_slots(settings, monkeypatch):
    data = {
        "_type": "playlist",
        "title": "Test",
        "extractor": "youtube:tab",
        "entries": [
            {"id": "first", "title": "First", "url": "first"},
            None,
            {"id": "third", "title": "Third", "url": "third"},
        ],
    }
    monkeypatch.setattr(web, "command_base", lambda *a: ["yt-dlp.exe"])
    commands = []

    def run(command, consume, *a):
        commands.append(command)
        consume(json.dumps(data))

    monkeypatch.setattr(web, "stream_process", run)
    result = web.preview("https://example.org/list", {}, settings)
    assert [e["index"] for e in result["entries"]] == [1, 2, 3]
    assert result["entries"][0]["url"] == "https://www.youtube.com/watch?v=first"
    assert result["entries"][1]["error"]
    assert "--dump-single-json" in commands[0] and "--skip-download" in commands[0]


def test_selection_uses_original_positions_and_collision_safe_names(settings, tmp_path):
    data = snapshot()
    payloads = web.make_payloads(
        data, [data["entries"][2], data["entries"][0]], tmp_path, "audio", {}, settings
    )
    assert [p["web"]["entry"]["index"] for p in payloads] == [1, 3]
    paths = [web.output_path(p["web"]) for p in payloads]
    assert paths[0].name.startswith("0001-") and paths[1].name.startswith("0003-")
    assert all(p.parent.parent == tmp_path for p in paths)
    other = json.loads(json.dumps(payloads[0]["web"]))
    other["entry"]["url"] += "?different=1"
    assert web.output_path(other) != paths[0]


def test_generic_playlist_does_not_repeat_parent_page(settings, monkeypatch):
    parent = "https://example.org/playlist"
    data = {
        "_type": "playlist",
        "title": "Demo",
        "webpage_url": parent,
        "entries": [
            {"webpage_url": parent, "url": f"https://example.org/{n}.mp4"}
            for n in (1, 2)
        ],
    }
    monkeypatch.setattr(web, "command_base", lambda *a: [])
    monkeypatch.setattr(
        web, "stream_process", lambda command, consume, *a: consume(json.dumps(data))
    )
    result = web.preview(parent, {}, settings)
    assert [e["url"] for e in result["entries"]] == [
        "https://example.org/1.mp4",
        "https://example.org/2.mp4",
    ]


def test_deno_windows_checksum_format(settings, tmp_path, monkeypatch):
    import io
    import zipfile

    package = io.BytesIO()
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("deno.exe", b"runtime")
    digest = hashlib.sha256(package.getvalue()).hexdigest().upper()
    name = "deno-x86_64-pc-windows-msvc.zip"
    prefix = "https://github.com/denoland/deno/releases/download/v2.9.8/"
    session = ReleaseSession()
    session.files[prefix + name] = package.getvalue()
    session.files[prefix + name + ".sha256sum"] = (
        "Algorithm : SHA256\r\nHash : "
        + digest
        + "\r\nPath : C:\\build\\"
        + name
        + "\r\n"
    ).encode()
    release = {
        "component": "deno",
        "channel": "stable",
        "repository": "denoland/deno",
        "tag": "v2.9.8",
        "asset": {"name": name, "browser_download_url": prefix + name},
        "checksum": {
            "name": name + ".sha256sum",
            "browser_download_url": prefix + name + ".sha256sum",
        },
    }
    monkeypatch.setattr(media, "run", lambda *a, **k: "deno 2.9.8\nv8 test")
    manager = ToolManager(tmp_path / "runtime", session)
    tool = manager.install(release)
    assert tool["version"] == "deno 2.9.8"
    manager.verify(tool)


def test_download_verification_and_idempotent_retry(
    settings, sample, tmp_path, monkeypatch
):
    source = tmp_path / "source.mp3"
    media.run([media.binary("ffmpeg"), "-v", "error", "-y", "-i", sample, source])
    data = snapshot()
    payload = web.make_payloads(
        data, data["entries"][:1], tmp_path / "output", "audio", {}, settings
    )[0]
    payload["web"]["tools"] = {"yt-dlp": {"version": "test"}}
    active_tools = {"yt-dlp": {"version": "updated-before-start"}}

    class ActiveManager:
        def state(self):
            return {"configured": True}

        def snapshot(self):
            return json.loads(json.dumps(active_tools))

    monkeypatch.setattr(web, "ToolManager", ActiveManager)
    commands = []
    monkeypatch.setattr(web, "command_base", lambda *a: ["yt-dlp.exe"])

    def run(command, consume, *a, **k):
        # A mid-download update must not change this job's captured version.
        active_tools["yt-dlp"]["version"] = "updated-during-download"
        commands.append(command)
        path = Path(command[command.index("--output") + 1].replace("%(ext)s", "mp3"))
        shutil.copy2(source, path)
        consume(
            web.PROGRESS + json.dumps({"downloaded_bytes": 100, "total_bytes": 200})
        )
        consume(web.FINAL + json.dumps(str(path)))

    monkeypatch.setattr(web, "stream_process", run)
    events = []
    work = tmp_path / "work"
    work.mkdir()
    result = web.execute_web(payload, work, lambda *e: events.append(e))
    assert result["status"] == "Lokal gespeichert"
    assert result["web_result"]["engine"] == "updated-before-start"
    assert payload["web"]["tools"]["yt-dlp"]["version"] == "test"
    assert Path(result["web_result"]["path"]).is_file()
    assert not list(work.rglob("*.pcm"))
    assert any(len(event) == 3 and event[2] == 0.45 for event in events)
    assert web.execute_web(payload, work, lambda *e: None) == result
    assert len(commands) == 1
    # A process killed between receipt creation and publication can resume.
    Path(result["web_result"]["path"]).unlink()
    resumed = web.execute_web(payload, work, lambda *e: None)
    assert resumed["web_result"]["engine"] == "updated-during-download"
    assert len(commands) == 2
    Path(result["web_result"]["path"]).write_bytes(b"changed")
    with pytest.raises(ValueError, match="verändert"):
        web.execute_web(payload, work, lambda *e: None)


def test_external_process_cancel_and_timeout_reap_children():
    started = time.monotonic()
    with pytest.raises(InterruptedError):
        web.stream_process(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            lambda _: None,
            60,
            lambda: True,
        )

    assert time.monotonic() - started < 5
    with pytest.raises(TimeoutError):
        web.stream_process(
            [sys.executable, "-c", "import time; time.sleep(30)"], lambda _: None, 0.2
        )


def test_staging_never_accepts_a_path_as_group_identifier(settings, tmp_path):
    data = snapshot()
    payload = web.make_payloads(
        data, data["entries"][:1], tmp_path / "out", "audio", {}, settings
    )[0]
    payload["web"]["batch"] = "../.."
    protected = tmp_path / "keep.txt"
    protected.write_text("user data", encoding="utf-8")
    with pytest.raises(ValueError, match="Arbeitsordner"):
        web.execute_web(payload, tmp_path / "work", lambda *e: None)
    assert protected.read_text("utf-8") == "user data"


class Response:
    def __init__(self, content):
        self.content = content

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def raise_for_status(self):
        pass

    def iter_content(self, size):
        yield self.content


class ReleaseSession:
    def __init__(self):
        self.files = {}

    def get(self, url, **kwargs):
        return Response(self.files[url])


def fake_release(session, version, content):
    prefix = "https://github.com/yt-dlp/yt-dlp/releases/download/" + version + "/"
    session.files[prefix + "yt-dlp.exe"] = content
    session.files[prefix + "SHA2-256SUMS"] = (
        hashlib.sha256(content).hexdigest() + "  yt-dlp.exe\n"
    ).encode()
    return {
        "component": "yt-dlp",
        "channel": "stable",
        "repository": "yt-dlp/yt-dlp",
        "tag": version,
        "asset": {"name": "yt-dlp.exe", "browser_download_url": prefix + "yt-dlp.exe"},
        "checksum": {
            "name": "SHA2-256SUMS",
            "browser_download_url": prefix + "SHA2-256SUMS",
        },
    }


def test_verified_update_rollback_and_pinned_old_jobs(settings, tmp_path, monkeypatch):
    session = ReleaseSession()
    manager = ToolManager(tmp_path / "tools", session)
    version = ["2026.01.01"]
    monkeypatch.setattr(
        media,
        "run",
        lambda command, **kwargs: (
            "--js-runtimes --progress-template --print"
            if "--help" in command
            else version[0]
        ),
    )
    first = manager.install(fake_release(session, version[0], b"first"))
    version[0] = "2026.01.02"
    second = manager.install(fake_release(session, version[0], b"second"))
    assert (
        Path(first["path"]).read_bytes() == b"first" and first["path"] != second["path"]
    )
    assert manager.state()["yt-dlp"] == second
    assert manager.rollback() == first
    manager.verify(second)
    broken = fake_release(session, "2026.01.03", b"third")
    session.files[broken["asset"]["browser_download_url"]] = b"tampered"
    with pytest.raises(ValueError, match="Prüfsumme"):
        manager.install(broken)
    assert manager.state()["yt-dlp"] == first
    version[0] = "wrong"
    with pytest.raises(ValueError, match="Version"):
        manager.install(fake_release(session, "2026.01.04", b"unhealthy"))
    assert manager.state()["yt-dlp"] == first
    with pytest.raises(ValueError):
        release_url("https://evil.invalid/yt-dlp.exe", "yt-dlp/yt-dlp")


def test_gui_selection_retry_and_ordered_handoff(app, settings, tmp_path, monkeypatch):
    from teddystudio.ui_web import WebPage

    store = Store(tmp_path / "library.sqlite")

    class Worker:
        starts = 0

        def start(self):
            self.starts += 1

        def pause_all(self):
            pass

        def cancel(self, ids):
            self.cancelled = ids

    worker = Worker()
    result = []
    page = WebPage(
        store,
        settings,
        worker,
        lambda: None,
        lambda title, tracks: result.extend(tracks),
    )
    page.snapshot = snapshot()
    page.render_entries()
    assert all(
        page.table.item(i, 1).checkState() == Qt.CheckState.Checked for i in range(3)
    )
    page.table.item(1, 1).setCheckState(Qt.CheckState.Unchecked)
    page.destination.setText(str(tmp_path / "downloads"))
    monkeypatch.setattr(page.tools, "snapshot", lambda: {})
    page.enqueue()
    assert len(store.jobs()) == 2
    for job in store.jobs():
        store.update_job(job["id"], "Fehlgeschlagen")
    page.check_all(False)
    page.table.item(1, 1).setCheckState(
        Qt.CheckState.Checked
    )  # Original entry 3 after reload.
    page.retry_selected()
    assert sum(job["status"] == "Wartet" for job in store.jobs()) == 1
    assert worker.starts == 1
    page.check_all(True)
    for job in store.jobs():
        store.update_job(job["id"], "Lokal gespeichert", 1)
    monkeypatch.setattr(
        web,
        "saved_result",
        lambda o: {"path": str(tmp_path / (str(o["entry"]["index"]) + ".mp3"))},
    )
    monkeypatch.setattr(media, "probe", lambda *a: {"duration": 4.2})
    monkeypatch.setattr(page, "task", lambda work, done: done(work(lambda _: None)))
    page.handoff()
    assert [track.track for track in result] == ["1", "3"]
    page.timer.stop()
    page.close()
