"""End-to-end HTTP contract tests; no access to the user's real library."""

from dataclasses import asdict
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote
import hashlib
import json
import threading
import socket
import pytest

from teddystudio.client import TeddyCloud
from teddystudio.domain import Book, Track
from teddystudio.jobs import execute
from teddystudio.storage import Settings
from teddystudio import taf


@pytest.fixture
def server():
    state = {
        "models": [{"model": "unrelated", "title": "Keep me", "extra": True}],
        "files": {},
        "dirs": {"library": set(), "custom_img": set()},
        "writes": 0,
        "corrupt": False,
        "requests": [],
        "drop_cover_before": 0,
        "drop_cover_after": 0,
        "cut_download": 0,
        "hold_models": False,
        "models_entered": threading.Event(),
        "models_release": threading.Event(),
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, value, status=200):
            raw = (
                value
                if isinstance(value, bytes)
                else json.dumps(value, ensure_ascii=False).encode()
            )
            self.send_response(status)
            self.send_header("Content-Type", "text/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query, keep_blank_values=True)
            state["requests"].append(("GET", parsed.path))
            if parsed.path == "/api/toniesCustomJson":
                if state["hold_models"]:
                    state["models_entered"].set()
                    state["models_release"].wait(15)
                return self.send(state["models"])
            if parsed.path == "/api/fileIndexV2":
                special = query["special"][0]
                folder = query.get("path", [""])[0]
                files = [
                    {"name": name} for name in state["dirs"][special] if not folder
                ]
                for name, raw in state["files"].items():
                    if str(Path(name).parent).replace("\\", "/") == "/".join(
                        filter(None, [special, folder])
                    ):
                        entry = {"name": Path(name).name}
                        if name.endswith(".taf"):
                            # Parse the actual bytes with the independent TAF reader.
                            import tempfile

                            with tempfile.NamedTemporaryFile(
                                suffix=".taf", delete=False
                            ) as stream:
                                stream.write(raw)
                                temp = Path(stream.name)
                            try:
                                header = taf.validate_header(temp)
                                entry["tafHeader"] = {
                                    "valid": True,
                                    "audioId": int(header["audioId"]),
                                    "sha1Hash": header["sha1Hash"].upper(),
                                }
                            finally:
                                temp.unlink()
                        files.append(entry)
                return self.send({"files": files})
            value = state["files"].get(unquote(parsed.path).lstrip("/"))
            if value is None:
                return self.send({}, 404)
            if state["cut_download"]:
                state["cut_download"] -= 1
                self.send_response(200)
                self.send_header("Content-Length", str(len(value) + 50))
                self.end_headers()
                self.wfile.write(value[: max(1, len(value) // 2)])
                self.wfile.flush()
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            return self.send(value + b"corrupt" if state["corrupt"] else value)

        def do_POST(self):
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query, keep_blank_values=True)
            state["requests"].append(("POST", parsed.path))
            state["writes"] += 1
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            if parsed.path == "/api/dirCreate":
                state["dirs"][query["special"][0]].add(raw.decode().strip("/"))
                return self.send(b"OK")
            if parsed.path == "/api/fileUpload":
                if query["special"][0] == "custom_img" and state["drop_cover_before"]:
                    state["drop_cover_before"] -= 1
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
                message = BytesParser(policy=default).parsebytes(
                    b"Content-Type: "
                    + self.headers["Content-Type"].encode()
                    + b"\r\n\r\n"
                    + raw
                )
                for part in message.iter_parts():
                    name = "/".join(
                        filter(
                            None,
                            [
                                query["special"][0],
                                query.get("path", [""])[0],
                                part.get_filename(),
                            ],
                        )
                    )
                    state["files"][name] = part.get_payload(decode=True)
                if query["special"][0] == "custom_img" and state["drop_cover_after"]:
                    state["drop_cover_after"] -= 1
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
                return self.send(b"OK")
            if parsed.path == "/api/toniesCustomJsonUpsert":
                entry = json.loads(raw)
                if not isinstance(entry.get("no"), str):
                    return self.send(
                        b"Invalid entry at index 0: 'no' must be string", 400
                    )
                state["models"] = [
                    m for m in state["models"] if m["model"] != entry["model"]
                ] + [entry]
                return self.send(b"OK")
            return self.send({}, 404)

    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{http.server_port}", state
    http.shutdown()
    http.server_close()
    thread.join()


@pytest.mark.parametrize(
    "failure,expected_posts", [("drop_cover_before", 2), ("drop_cover_after", 1)]
)
def test_cover_disconnect_recovery_checks_state_before_resend(
    server, tmp_path, failure, expected_posts
):
    url, state = server
    state[failure] = 1
    image = tmp_path / "cover.jpg"
    image.write_bytes(b"synthetic-cover-content")
    with TeddyCloud(Settings(url=url)) as client:
        assert client.upload_verified(image, "custom_img/Serie", "cover.jpg")
        assert not client.upload_verified(image, "custom_img/Serie", "cover.jpg")
    assert state["files"]["custom_img/Serie/cover.jpg"] == image.read_bytes()
    assert (
        sum(
            method == "POST" and path == "/api/fileUpload"
            for method, path in state["requests"]
        )
        == expected_posts
    )


def test_hash_restarts_after_partial_download(server):
    url, state = server
    raw = b"correct complete data" * 100
    state["files"]["library/file.taf"] = raw
    state["cut_download"] = 1
    with TeddyCloud(Settings(url=url)) as client:
        assert client.remote_hash("library/file.taf") == hashlib.sha256(raw).hexdigest()


def test_no_upload_resend_when_state_cannot_be_read(server, tmp_path, monkeypatch):
    import requests

    url, state = server
    image = tmp_path / "cover.jpg"
    image.write_bytes(b"test")
    state["drop_cover_before"] = 1
    client = TeddyCloud(Settings(url=url))
    calls = []

    def remote_hash(path):
        calls.append(path)
        if len(calls) == 1:
            return None
        raise requests.ConnectionError("server unreachable")

    monkeypatch.setattr(client, "remote_hash", remote_hash)
    with client, pytest.raises(ValueError, match="Kein weiterer Upload"):
        client.upload_verified(image, "custom_img/Serie", "cover.jpg")
    assert (
        sum(m == "POST" and p == "/api/fileUpload" for m, p in state["requests"]) == 1
    )


def test_verified_import_without_cover_and_second_run_has_no_writes(
    server, sample, tmp_path
):
    url, state = server
    settings = Settings(url=url, export=str(tmp_path / "export"))
    book = Book(
        "1",
        str(sample.parent),
        "Über den Wolken",
        "Bibi-Tina",
        1,
        tracks=[Track(str(sample), duration=4.2)],
    )
    payload = {
        "book": book.to_dict(),
        "settings": asdict(settings),
        "profile": asdict(settings.profile()),
    }
    payload["book"]["cover"] = str(tmp_path / "missing-cover.jpg")
    result = execute("Import", payload, tmp_path / "work", lambda *_: None)
    assert result["status"] == "Bestätigt"
    imported = next(m for m in state["models"] if m["model"] == "bibi-tina-001")
    assert imported["pic"] == "" and imported["episodes"] == "Über den Wolken"
    assert imported["no"] == "1"
    assert state["models"][0] == {
        "model": "unrelated",
        "title": "Keep me",
        "extra": True,
    }
    writes = state["writes"]
    # Older deployments returned numeric episode numbers. Readback stays compatible.
    imported["no"] = 1
    execute("Import", payload, tmp_path / "work", lambda *_: None)
    assert state["writes"] == writes
    assert not any("rfid" in path.casefold() for _, path in state["requests"])
    state["corrupt"] = True
    with pytest.raises(ValueError, match="überein"):
        execute("Import", payload, tmp_path / "work", lambda *_: None)


def test_window_close_cancels_job_blocked_in_http(
    app, server, sample, tmp_path, monkeypatch
):
    import time
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox
    from teddystudio.app import MainWindow
    from teddystudio.storage import Store

    monkeypatch.setenv("TEDDYSTUDIO_DATA", str(tmp_path / "data"))
    url, state = server
    settings = Settings(url=url)
    book = Book(
        "close",
        str(sample.parent),
        "Shutdown test",
        "Demo",
        1,
        tracks=[Track(str(sample), duration=4.2)],
    )
    payload = {
        "book": book.to_dict(),
        "settings": asdict(settings),
        "profile": asdict(settings.profile()),
    }
    store = Store(tmp_path / "library.sqlite")
    identifier = store.add_job("Import", payload)
    window = MainWindow(settings, store)
    window.show()
    state["hold_models"] = True
    window.worker.start()
    try:
        assert state["models_entered"].wait(15)

        def confirm():
            dialog = QApplication.activeModalWidget()
            assert isinstance(dialog, QMessageBox)
            next(
                b
                for b in dialog.buttons()
                if b.text() == "Aufträge abbrechen und beenden"
            ).click()

        QTimer.singleShot(0, confirm)
        started = time.monotonic()
        window.close()
        deadline = started + 10
        while window.isVisible() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.02)
        assert not window.isVisible() and not window.worker.running
        assert store.jobs()[0]["status"] == "Abgebrochen"
        assert not list((tmp_path / "data" / "work" / identifier).rglob("*.pcm"))
    finally:
        state["models_release"].set()
        window.worker.cancel_all()
        if window.worker.thread:
            window.worker.thread.join(20)
        window.close()


def test_model_rejection_includes_server_validation_message(server):
    import requests

    url, state = server
    before = list(state["models"])
    with TeddyCloud(Settings(url=url)) as client:
        with pytest.raises(requests.HTTPError, match="'no' must be string") as error:
            client.request(
                "POST",
                "/api/toniesCustomJsonUpsert",
                json={"model": "invalid", "series": "Serie", "no": 8},
            )
    assert error.value.response.status_code == 400
    assert state["models"] == before


def test_verification_does_not_mutate_server(server, sample, tmp_path):
    url, state = server
    settings = Settings(url=url)
    book = Book(
        "1",
        str(sample.parent),
        "Titel",
        "Serie",
        1,
        tracks=[Track(str(sample), duration=4.2)],
    )
    payload = {
        "book": book.to_dict(),
        "settings": asdict(settings),
        "profile": asdict(settings.profile()),
    }
    execute("Import", payload, tmp_path / "work", lambda *_: None)
    payload["taf"] = str(tmp_path / "work" / "serie-001.taf")
    writes = state["writes"]
    execute("Prüfung", payload, tmp_path / "verify", lambda *_: None)
    assert state["writes"] == writes


def test_complete_import_finishes_after_cover_disconnect(server, sample, tmp_path):
    from PIL import Image

    url, state = server
    cover = tmp_path / "cover.jpg"
    Image.new("RGB", (40, 40), "blue").save(cover)
    settings = Settings(url=url)
    book = Book(
        "retry",
        str(sample.parent),
        "Titel",
        "Serie",
        1,
        cover=str(cover),
        tracks=[Track(str(sample), duration=4.2)],
    )
    payload = {
        "book": book.to_dict(),
        "settings": asdict(settings),
        "profile": asdict(settings.profile()),
    }
    state["drop_cover_before"] = 1
    result = execute("Import", payload, tmp_path / "work", lambda *_: None)
    assert result["status"] == "Bestätigt"
    assert any(m["model"] == "serie-001" and m["pic"] for m in state["models"])
    writes = state["writes"]
    execute("Import", payload, tmp_path / "work", lambda *_: None)
    assert state["writes"] == writes


def test_entire_queue_can_pause_during_blocked_http_and_resume(
    server, sample, tmp_path, monkeypatch
):
    import time
    from teddystudio.jobs import Worker
    from teddystudio.storage import Store

    monkeypatch.setenv("TEDDYSTUDIO_DATA", str(tmp_path / "data"))
    url, state = server
    settings = Settings(url=url)
    book = Book(
        "pause",
        str(sample.parent),
        "Titel",
        "Serie",
        1,
        tracks=[Track(str(sample), duration=4.2)],
    )
    payload = {
        "book": book.to_dict(),
        "settings": asdict(settings),
        "profile": asdict(settings.profile()),
    }
    store = Store(tmp_path / "test.sqlite")
    identifier = store.add_job("Import", payload)
    worker = Worker(store, settings=settings)
    state["hold_models"] = True
    worker.start()
    try:
        assert state["models_entered"].wait(15)
        start = time.monotonic()
        worker.pause_all()
        worker.thread.join(5)
        assert not worker.running and time.monotonic() - start < 5
        assert store.jobs()[0]["status"] == "Pausiert"
        before = sum(e["stage"] == "Konvertieren" for e in store.events(identifier))
        assert not (tmp_path / "data" / "work" / identifier / "source.pcm").exists()
        state["hold_models"] = False
        state["models_release"].set()
        worker.start()
        worker.thread.join(20)
        assert not worker.running
        assert store.jobs()[0]["status"] == "Bestätigt", store.events()
        assert (
            sum(e["stage"] == "Konvertieren" for e in store.events(identifier)) > before
        )
        assert not list((tmp_path / "data" / "work" / identifier).rglob("*.pcm"))
    finally:
        state["models_release"].set()
        worker.pause.set()
        worker.cancel([identifier])
        if worker.thread:
            worker.thread.join(10)


@pytest.mark.parametrize(
    "mode",
    [
        "Separate Dateien / Custom-Tonies",
        "TAF mit Kapiteln",
        "TAF als Playlist (Originaldateien vollständig)",
    ],
)
@pytest.mark.parametrize(
    "target", ["Nur lokal", "Nur TeddyCloud", "Lokal und TeddyCloud"]
)
def test_split_targets_with_real_cover(server, sample, tmp_path, mode, target):
    from PIL import Image
    import shutil

    url, state = server
    source = tmp_path / "sources"
    source.mkdir()
    shutil.copyfile(sample, source / "source.wav")
    cover = source / "cover.jpg"
    Image.new("RGB", (120, 80), "blue").save(cover)
    settings = Settings(url=url, source=str(source), export=str(tmp_path / "export"))
    book = Book(
        "split",
        str(source),
        "Test",
        "Serie",
        1,
        cover=str(cover),
        tracks=[Track(str(source / "source.wav"), duration=4.2)],
    )
    payload = {
        "book": book.to_dict(),
        "settings": asdict(settings),
        "profile": asdict(settings.profile()),
        "audio": {
            "seconds": 2,
            "mode": mode,
            "target": target,
            "format": "wav",
            "destination": settings.export,
        },
    }
    result = execute("Audio aufteilen", payload, tmp_path / "work", lambda *_: None)
    assert result["status"] == (
        "Lokal gespeichert" if target == "Nur lokal" else "Bestätigt"
    )
    expected = 3 if mode.startswith("Separate") else 1
    models = [m for m in state["models"] if m["model"] != "unrelated"]
    assert len(models) == (0 if target == "Nur lokal" else expected)
    assert all(model["pic"].startswith("/custom_img/") for model in models)
    exports = list(Path(settings.export).glob("*.wav")) + list(
        Path(settings.export).glob("*.taf")
    )
    assert len(exports) == (0 if target == "Nur TeddyCloud" else expected)
    writes = state["writes"]
    execute("Audio aufteilen", payload, tmp_path / "work", lambda *_: None)
    assert state["writes"] == writes
