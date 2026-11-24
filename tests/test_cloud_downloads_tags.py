from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote
from pathlib import Path
import json, threading, socket, hashlib, time
import pytest, requests
from teddystudio import media, taf
from teddystudio.client import TeddyCloud
from teddystudio.cloud_library import inventory, entry_book
from teddystudio.downloads import execute_download, fetch_taf, output_folder
from teddystudio.tags import read_tag, assign_tag, normalize_uid, FIELDS
from teddystudio.storage import Settings, Store


@pytest.fixture
def cloud_server(sample, tmp_path):
    pcm = tmp_path / "raw.pcm"
    media.concat_pcm([sample], pcm)
    original = tmp_path / "original.taf"
    taf.create(pcm, original, [0, 2])
    info = taf.validate_header(original)
    extra = tmp_path / "extra.pcm"
    extra.write_bytes(pcm.read_bytes() * 2)
    other = tmp_path / "other.taf"
    taf.create(extra, other, [0])
    other_info = taf.validate_header(other)

    def model(name, header):
        return {
            "model": name,
            "title": name,
            "series": "Testserie",
            "episodes": name,
            "no": "1",
            "audio_id": [header["audioId"]],
            "hash": [header["sha1Hash"]],
            "tracks": ["Kapitel Eins", "Kapitel Zwei"],
        }

    custom = model("custom-one", info)
    official = model("official-one", other_info)
    uid = "0011223344556677"
    state = {
        "files": {
            ("library", "Serie/one.taf"): original.read_bytes(),
            ("", "AABBCCDD/11223344"): other.read_bytes(),
        },
        "headers": {
            ("library", "Serie/one.taf"): info,
            ("", "AABBCCDD/11223344"): other_info,
        },
        "custom": [custom],
        "official": [official],
        "tags": {("", uid): {**FIELDS, "cloud_auth": "SECRET-MUST-STAY"}},
        "posts": [],
        "gets": [],
        "cut": False,
        "range": True,
        "lose_tag_response": False,
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, value, status=200, headers=None):
            raw = value if isinstance(value, bytes) else json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(raw)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query, keep_blank_values=True)
            path = unquote(parsed.path)
            state["gets"].append((path, dict(query), self.headers.get("Range")))
            if path == "/api/toniesCustomJson":
                return self.send(state["custom"])
            if path == "/api/toniesJson":
                return self.send(state["official"])
            if path.startswith("/content/json/get/"):
                key = (query.get("overlay", [""])[0], path.rsplit("/", 1)[1])
                value = state["tags"].get(key)
                return self.send(
                    value if value is not None else {},
                    200 if value is not None else 404,
                )
            if path == "/api/fileIndexV2":
                special = query.get("special", [""])[0]
                folder = query.get("path", [""])[0]
                prefix = folder + "/" if folder else ""
                entries = {}
                all_files = dict(state["files"])
                if not special:
                    for (overlay, tag), data in state["tags"].items():
                        if overlay == query.get("overlay", [""])[0]:
                            all_files[
                                ("", tag[:8].upper() + "/" + tag[8:].upper() + ".json")
                            ] = json.dumps(data).encode()
                for (space, name), raw in all_files.items():
                    if space != special or not name.startswith(prefix):
                        continue
                    remaining = name[len(prefix) :]
                    part = remaining.split("/")[0]
                    if "/" in remaining:
                        entries[part] = {"name": part, "isDir": True}
                        continue
                    entry = {"name": part, "isDir": False, "size": len(raw)}
                    if (space, name) in state["headers"]:
                        h = state["headers"][(space, name)]
                        entry["tafHeader"] = {
                            "valid": True,
                            "audioId": int(h["audioId"]),
                            "sha1Hash": h["sha1Hash"],
                        }
                    entries[part] = entry
                return self.send(
                    {"files": [{"name": "..", "isDir": True}] + list(entries.values())}
                )
            if path.startswith("/content/"):
                key = (query.get("special", [""])[0], path[len("/content/") :])
                raw = state["files"].get(key)
                if raw is None:
                    return self.send({}, 404)
                if state["cut"]:
                    state["cut"] = False
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw[:10000])
                    self.wfile.flush()
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
                if self.headers.get("Range") and state["range"]:
                    start = int(self.headers["Range"].split("=")[1].split("-")[0])
                    return self.send(
                        raw[start:],
                        206,
                        {"Content-Range": f"bytes {start}-{len(raw)-1}/{len(raw)}"},
                    )
                return self.send(raw)
            self.send({}, 404)

        def do_POST(self):
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query, keep_blank_values=True)
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            state["posts"].append((parsed.path, raw))
            if parsed.path.startswith("/content/json/set/"):
                key = (query.get("overlay", [""])[0], parsed.path.rsplit("/", 1)[1])
                old = state["tags"].get(key, dict(FIELDS))
                new = dict(old)
                for k, values in parse_qs(raw.decode(), keep_blank_values=True).items():
                    new[k] = (
                        values[0] == "true"
                        if k in {"live", "hide", "nocloud"}
                        else values[0]
                    )
                state["tags"][key] = new
                if state["lose_tag_response"]:
                    state["lose_tag_response"] = False
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
                return self.send(b"OK")
            self.send({}, 404)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", state, uid
    server.shutdown()
    server.server_close()
    thread.join()


def setup_download(cloud_server, tmp_path, fmt="ogg", split=False):
    url, state, uid = cloud_server
    settings = Settings(url=url)
    with TeddyCloud(settings) as client:
        snapshot = inventory(client)
    entry = next(e for e in snapshot["entries"] if e["kind"] == "Custom")
    book = entry_book(entry, url)
    payload = {
        "book": book.to_dict(),
        "settings": asdict(settings),
        "profile": asdict(settings.profile()),
        "download": {
            "entry": entry,
            "destination": str(tmp_path / "downloads"),
            "format": fmt,
            "keep_taf": True,
            "import_collection": True,
            "split_chapters": split,
        },
    }
    return payload, snapshot


def test_inventory_matches_custom_original_deduplicates_and_finds_tags(
    cloud_server, tmp_path
):
    url, state, uid = cloud_server
    state["files"][("", "ABCDABCD/12345678")] = state["files"][
        ("library", "Serie/one.taf")
    ]
    state["headers"][("", "ABCDABCD/12345678")] = state["headers"][
        ("library", "Serie/one.taf")
    ]
    state["custom"].append(
        {
            "model": "missing",
            "title": "Missing",
            "audio_id": ["999"],
            "hash": ["a" * 40],
        }
    )
    payload, snapshot = setup_download(cloud_server, tmp_path)
    assert len([e for e in snapshot["entries"] if e["available"]]) == 2
    assert {e["kind"] for e in snapshot["entries"] if e["available"]} == {
        "Custom",
        "Katalog",
    }
    assert len(payload["download"]["entry"]["locations"]) == 2
    assert any(not e["available"] for e in snapshot["entries"])
    assert uid in snapshot["tags"] and not state["posts"]


def test_download_validates_original_and_imports_ogg_without_server_writes(
    cloud_server, tmp_path
):
    payload, _ = setup_download(cloud_server, tmp_path)
    result = execute_download(payload, tmp_path / "work", lambda *_: None)
    assert result["status"] == "Lokal gespeichert" and result["book"]["tracks"][0][
        "path"
    ].endswith(".ogg")
    folder = output_folder(payload)
    assert (folder / "source.taf").read_bytes() == cloud_server[1]["files"][
        ("library", "Serie/one.taf")
    ]
    count = len([x for x in cloud_server[1]["gets"] if x[0].endswith("one.taf")])
    execute_download(payload, tmp_path / "work", lambda *_: None)
    assert (
        len([x for x in cloud_server[1]["gets"] if x[0].endswith("one.taf")]) == count
    )
    assert not cloud_server[1]["posts"]


@pytest.mark.parametrize("ranges", [True, False])
def test_interrupted_download_resumes_or_restarts_safely(
    cloud_server, tmp_path, ranges
):
    payload, _ = setup_download(cloud_server, tmp_path)
    entry = payload["download"]["entry"]
    url, state, _ = cloud_server
    state["range"] = ranges
    state["cut"] = True
    work = tmp_path / "work"
    work.mkdir()
    with TeddyCloud(Settings(url=url)) as client:
        with pytest.raises(ValueError):
            fetch_taf(client, entry, work, lambda *_: None)
        assert (work / "source.partial.taf").stat().st_size > 0
        path = fetch_taf(client, entry, work, lambda *_: None)
    assert path.read_bytes() == state["files"][("library", "Serie/one.taf")]
    assert any(x[2] for x in state["gets"])


def test_corrupt_download_does_not_publish_success(cloud_server, tmp_path):
    payload, _ = setup_download(cloud_server, tmp_path)
    cloud_server[1]["files"][("library", "Serie/one.taf")] += b"broken"
    with pytest.raises(ValueError):
        execute_download(payload, tmp_path / "work", lambda *_: None)
    assert not (output_folder(payload) / "source.taf").exists()


def test_local_modified_file_is_not_overwritten(cloud_server, tmp_path):
    payload, _ = setup_download(cloud_server, tmp_path)
    folder = output_folder(payload)
    folder.mkdir(parents=True)
    (folder / "audio.ogg").write_bytes(b"local changes")
    with pytest.raises(ValueError, match="überschrieben"):
        execute_download(payload, tmp_path / "work", lambda *_: None)
    assert (folder / "audio.ogg").read_bytes() == b"local changes"


@pytest.mark.parametrize("fmt", ["mp3", "flac", "wav"])
def test_download_can_export_chapter_tracks(cloud_server, tmp_path, fmt):
    payload, _ = setup_download(cloud_server, tmp_path, fmt, True)
    result = execute_download(payload, tmp_path / "work", lambda *_: None)
    assert len(result["book"]["tracks"]) == 2
    assert abs(sum(t["duration"] for t in result["book"]["tracks"]) - 4.2) < 0.3
    assert all(t["path"].endswith("." + fmt) for t in result["book"]["tracks"])
    assert [Path(t["path"]).name for t in result["book"]["tracks"]] == [
        f"001-Kapitel Eins.{fmt}",
        f"002-Kapitel Zwei.{fmt}",
    ]
    assert [
        media.probe(t["path"])["tags"].get("title") for t in result["book"]["tracks"]
    ] == ["Kapitel Eins", "Kapitel Zwei"]


def test_unknown_or_mismatched_track_titles_are_not_misassigned():
    from teddystudio.downloads import chapter_title, chapter_filename

    assert chapter_title({"model": {"tracks": ["Wrong chapter"]}}, 0, 2) == "Spur 001"
    assert (
        chapter_filename("Überraschung / Ein Bagger!", 2, "mp3")
        == "002-Ueberraschung - Ein Bagger.mp3"
    )


def tag_payload(cloud_server):
    url, state, uid = cloud_server
    with TeddyCloud(Settings(url=url)) as client:
        before = read_tag(client, uid)
    after = {
        **before["values"],
        "source": "lib://Serie/one.taf",
        "tonie_model": "custom-one",
        "nocloud": True,
    }
    return {
        "settings": asdict(Settings(url=url)),
        "tag": {"uid": uid, "overlay": "", "before": before, "after": after},
    }


def test_tag_assignment_preserves_auth_and_unrelated_fields(cloud_server, tmp_path):
    payload = tag_payload(cloud_server)
    result = assign_tag(payload, tmp_path, lambda *_: None)
    assert result["status"] == "Bestätigt"
    url, state, uid = cloud_server
    assert state["tags"][("", uid)]["cloud_auth"] == "SECRET-MUST-STAY"
    assert state["tags"][("", uid)]["tonie_model"] == "custom-one"
    assert (
        "SECRET" not in json.dumps(payload)
        and "SECRET" not in (tmp_path / "tag-change.json").read_text()
    )
    count = len(state["posts"])
    assign_tag(payload, tmp_path, lambda *_: None)
    assert len(state["posts"]) == count


def test_tag_concurrent_change_is_not_overwritten(cloud_server, tmp_path):
    payload = tag_payload(cloud_server)
    cloud_server[1]["tags"][("", cloud_server[2])]["hide"] = True
    with pytest.raises(ValueError, match="verändert"):
        assign_tag(payload, tmp_path, lambda *_: None)
    assert not cloud_server[1]["posts"]


def test_tag_lost_response_is_verified_without_duplicate_write(cloud_server, tmp_path):
    payload = tag_payload(cloud_server)
    cloud_server[1]["lose_tag_response"] = True
    assert assign_tag(payload, tmp_path, lambda *_: None)["status"] == "Bestätigt"
    assert len(cloud_server[1]["posts"]) == 1


def test_wrong_model_audio_pair_is_rejected(cloud_server, tmp_path):
    payload = tag_payload(cloud_server)
    payload["tag"]["after"]["tonie_model"] = "official-one"
    with pytest.raises(ValueError, match="passen nicht"):
        assign_tag(payload, tmp_path, lambda *_: None)
    assert not cloud_server[1]["posts"]


def test_uid_normalization_is_explicit():
    assert normalize_uid("00:11:22:33:44:55:66:77") == "0011223344556677"
    assert normalize_uid("0011223344556677", True) == "7766554433221100"
    with pytest.raises(ValueError):
        normalize_uid("../../invalid")


def test_native_content_can_be_assigned_to_a_tonie(cloud_server, tmp_path):
    payload = tag_payload(cloud_server)
    payload["tag"]["after"].update(
        source="content://AABBCCDD/11223344", tonie_model="official-one"
    )
    assert assign_tag(payload, tmp_path, lambda *_: None)["status"] == "Bestätigt"
    assert cloud_server[1]["tags"][("", cloud_server[2])]["source"].startswith(
        "content://"
    )


def test_new_tag_and_model_only_assignment(cloud_server, tmp_path):
    payload = tag_payload(cloud_server)
    uid = "8899aabbccddeeff"
    with TeddyCloud(Settings(url=cloud_server[0])) as client:
        payload["tag"]["before"] = read_tag(client, uid)
    payload["tag"]["uid"] = uid
    assert assign_tag(payload, tmp_path, lambda *_: None)["status"] == "Bestätigt"
    assert ("", uid) in cloud_server[1]["tags"]
    with TeddyCloud(Settings(url=cloud_server[0])) as client:
        payload["tag"]["before"] = read_tag(client, uid)
    payload["tag"]["after"] = {
        **payload["tag"]["before"]["values"],
        "source": "",
        "tonie_model": "official-one",
    }
    assign_tag(payload, tmp_path, lambda *_: None)
    assert cloud_server[1]["tags"][("", uid)]["tonie_model"] == "official-one"


def test_download_job_imports_into_local_store(cloud_server, tmp_path, monkeypatch):
    from teddystudio.jobs import Worker

    monkeypatch.setenv("TEDDYSTUDIO_DATA", str(tmp_path / "data"))
    payload, _ = setup_download(cloud_server, tmp_path)
    store = Store(tmp_path / "store.sqlite")
    store.add_job("Aus TeddyCloud holen", payload)
    worker = Worker(store, settings=Settings(parallel_jobs=1))
    worker.start()
    worker.thread.join(20)
    try:
        assert not worker.running
        assert store.jobs()[0]["status"] == "Lokal gespeichert", store.events()
        assert len(store.books()) == 1 and store.books()[0].tracks
        assert Path(store.books()[0].tracks[0].path).exists()
        assert not cloud_server[1]["posts"]
    finally:
        if worker.running:
            worker.cancel_all()
            worker.thread.join(10)


def test_cloud_ui_selection_and_tag_preview_create_only_explicit_jobs(
    app, cloud_server, tmp_path, monkeypatch
):
    from PySide6.QtWidgets import QPushButton, QMessageBox
    from teddystudio.ui_cloud import CloudPage, TagsPage

    monkeypatch.setenv("TEDDYSTUDIO_DATA", str(tmp_path / "data"))
    payload, snapshot = setup_download(cloud_server, tmp_path)
    settings = Settings(
        url=cloud_server[0], download_destination=str(tmp_path / "downloads")
    )
    store = Store(tmp_path / "test.sqlite")
    cloud = CloudPage(store, settings, lambda: None)
    cloud.snapshot = snapshot
    cloud.refresh()
    next(
        b
        for b in cloud.findChildren(QPushButton)
        if b.text() == "Sichtbare Audios auswählen"
    ).click()
    assert len(cloud.selected_entries()) == 2
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes
    )
    cloud.create_jobs()
    assert len(store.jobs()) == 2 and all(
        r["kind"] == "Aus TeddyCloud holen" for r in store.jobs()
    )
    tags = TagsPage(store, settings, cloud, lambda: None)
    uid = cloud_server[2]
    tags.uid.setText(uid)
    with TeddyCloud(settings) as client:
        before = read_tag(client, uid)
    tags.loaded = {"uid": uid, "overlay": "", "url": cloud_server[0], "before": before}
    tags.bind_entry(payload["download"]["entry"])
    tags.apply()
    jobs = store.jobs()
    assert len(jobs) == 3 and jobs[0]["kind"] == "Tonie / Tag zuordnen"
    assert "SECRET" not in jobs[0]["payload"]
    assert not cloud_server[1]["posts"]
