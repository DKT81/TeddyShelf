from dataclasses import asdict
from pathlib import Path
import json
import os
import subprocess
import sys
import time

from teddystudio.domain import Book, Track
from teddystudio.jobs import Worker
from teddystudio.processes import ProcessGuard
from teddystudio.queue import terminate_tree
from teddystudio.storage import Settings, Store
from teddystudio.ui_jobs import JobsPage


def make_payload(sample, root, title):
    import shutil

    source = root / "sources" / sample.name
    source.parent.mkdir(exist_ok=True)
    if not source.exists():
        shutil.copy2(sample, source)
    sample = source
    settings = Settings(source=str(sample.parent), export=str(root / "exports"))
    book = Book(
        title,
        str(sample.parent),
        title,
        "Tests",
        tracks=[Track(str(sample), duration=4.2)],
    )
    return {
        "book": book.to_dict(),
        "settings": asdict(settings),
        "profile": asdict(settings.profile()),
        "audio": {
            "seconds": 3,
            "mode": "TAF mit Kapiteln",
            "target": "Nur lokal",
            "format": "mp3",
            "destination": str(root / "exports"),
        },
    }


def wait_until(predicate, timeout=30):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("Timed out waiting for worker state")


def stop_worker(worker):
    worker.pause.set()
    worker.cancel([r["id"] for r in worker.store.jobs()])
    if worker.thread:
        worker.thread.join(timeout=30)
    assert not worker.running


def test_two_real_jobs_parallel_and_completed_rows_stay_visible(
    app, sample, tmp_path, monkeypatch
):
    monkeypatch.setenv("TEDDYSTUDIO_DATA", str(tmp_path / "data"))
    store = Store(tmp_path / "test.sqlite")
    for title in ("First", "Second"):
        store.add_job("Audio aufteilen", make_payload(sample, tmp_path, title))
    worker = Worker(store, settings=Settings(parallel_jobs=2))
    worker.start()
    try:
        wait_until(lambda: sum(r["status"] == "Aktiv" for r in store.jobs()) == 2)
        wait_until(lambda: not worker.running)
        assert {r["status"] for r in store.jobs()} == {
            "Lokal gespeichert"
        }, store.events()
        assert len(list((tmp_path / "exports").glob("*.taf"))) == 2
        page = JobsPage(store, worker)
        assert len(page.rows) == 2 and "2 fertig" in page.counts.text()
        assert all(page.table.item(n, 2).text().startswith("Fertig") for n in range(2))
    finally:
        stop_worker(worker)


def test_serial_limit_cancel_and_delete_active_without_stopping_other_jobs(
    sample, tmp_path, monkeypatch
):
    monkeypatch.setenv("TEDDYSTUDIO_DATA", str(tmp_path / "data"))
    store = Store(tmp_path / "test.sqlite")
    first = store.add_job("Audio aufteilen", make_payload(sample, tmp_path, "First"))
    second = store.add_job("Audio aufteilen", make_payload(sample, tmp_path, "Second"))
    worker = Worker(store, settings=Settings(parallel_jobs=1))
    worker.start()
    try:
        wait_until(lambda: any(r["status"] == "Aktiv" for r in store.jobs()))
        assert sum(r["status"] == "Aktiv" for r in store.jobs()) == 1
        worker.cancel([first])
        wait_until(
            lambda: next(r for r in store.jobs() if r["id"] == first)["status"]
            == "Abgebrochen"
        )
        wait_until(
            lambda: any(
                r["id"] == second and r["status"] == "Aktiv" for r in store.jobs()
            )
        )
        worker.delete([second])
        wait_until(lambda: not any(r["id"] == second for r in store.jobs()))
        wait_until(lambda: not worker.running)
        assert not store.events(second)
        worker.delete([first])
        assert not store.jobs()
    finally:
        stop_worker(worker)


def test_deleting_queued_job_does_not_start_it(sample, tmp_path, monkeypatch):
    monkeypatch.setenv("TEDDYSTUDIO_DATA", str(tmp_path / "data"))
    store = Store(tmp_path / "test.sqlite")
    identifier = store.add_job(
        "Audio aufteilen", make_payload(sample, tmp_path, "Queued")
    )
    worker = Worker(store)
    worker.delete([identifier])
    worker.start()
    wait_until(lambda: not worker.running)
    assert not store.jobs() and not (tmp_path / "exports").exists()


def test_process_guard_stops_native_descendant(tmp_path):
    from teddystudio.media import binary

    marker = tmp_path / "child.pid"
    script = "import subprocess,sys,time;from pathlib import Path;sys.stdin.readline();p=subprocess.Popen([sys.argv[2],'-v','error','-re','-f','lavfi','-i','sine=frequency=440:duration=60','-f','null','-'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);Path(sys.argv[1]).write_text(str(p.pid));time.sleep(90)"
    parent = subprocess.Popen(
        [sys.executable, "-c", script, str(marker), binary("ffmpeg")],
        stdin=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        start_new_session=os.name != "nt",
    )
    guard = ProcessGuard(parent)
    try:
        parent.stdin.write(b"start\n")
        parent.stdin.flush()
        wait_until(marker.exists)
        pid = int(marker.read_text())
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.argtypes = [
                wintypes.DWORD,
                wintypes.BOOL,
                wintypes.DWORD,
            ]
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = kernel.OpenProcess(0x100000, False, pid)
            assert handle
        terminate_tree(parent, guard)
        assert parent.poll() is not None
        if os.name == "nt":
            assert kernel.WaitForSingleObject(handle, 5000) == 0
            kernel.CloseHandle(handle)
        else:

            def descendant_stopped():
                try:
                    return Path(f"/proc/{pid}/stat").read_text().split()[2] == "Z"
                except (FileNotFoundError, ProcessLookupError):
                    # The process may exit between opening and reading /proc.
                    return True

            wait_until(descendant_stopped)
    finally:
        terminate_tree(parent, guard)


def test_local_filename_conflicts_are_not_dispatched_together(
    sample, tmp_path, monkeypatch
):
    monkeypatch.setenv("TEDDYSTUDIO_DATA", str(tmp_path / "data"))
    store = Store(tmp_path / "test.sqlite")
    for _ in range(2):
        store.add_job("Audio aufteilen", make_payload(sample, tmp_path, "Same"))
    worker = Worker(store, settings=Settings(parallel_jobs=2))
    worker.start()
    peak = 0
    try:
        deadline = time.monotonic() + 30
        while worker.running and time.monotonic() < deadline:
            peak = max(peak, sum(r["status"] == "Aktiv" for r in store.jobs()))
            time.sleep(0.01)
        assert not worker.running
        assert peak == 1
        assert {r["status"] for r in store.jobs()} == {
            "Lokal gespeichert"
        }, store.events()
    finally:
        stop_worker(worker)


def test_pause_entire_queue_and_resume(sample, tmp_path, monkeypatch):
    monkeypatch.setenv("TEDDYSTUDIO_DATA", str(tmp_path / "data"))
    store = Store(tmp_path / "test.sqlite")
    for name in ("One", "Two", "Three"):
        store.add_job("Audio aufteilen", make_payload(sample, tmp_path, name))
    worker = Worker(store, settings=Settings(parallel_jobs=2))
    worker.start()
    try:
        wait_until(lambda: sum(r["status"] == "Aktiv" for r in store.jobs()) == 2)
        worker.pause_all()
        wait_until(lambda: not worker.running)
        assert {r["status"] for r in store.jobs()} == {"Pausiert"}
        worker.start()
        wait_until(lambda: not worker.running)
        assert {r["status"] for r in store.jobs()} == {
            "Lokal gespeichert"
        }, store.events()
    finally:
        stop_worker(worker)


def test_cancel_and_clear_whole_queue(sample, tmp_path, monkeypatch):
    monkeypatch.setenv("TEDDYSTUDIO_DATA", str(tmp_path / "data"))
    store = Store(tmp_path / "test.sqlite")
    for name in ("One", "Two", "Three"):
        store.add_job("Audio aufteilen", make_payload(sample, tmp_path, name))
    worker = Worker(store, settings=Settings(parallel_jobs=2))
    worker.start()
    try:
        wait_until(lambda: sum(r["status"] == "Aktiv" for r in store.jobs()) == 2)
        worker.cancel_all()
        wait_until(lambda: not worker.running)
        assert {r["status"] for r in store.jobs()} == {"Abgebrochen"}
        for row in store.jobs():
            store.update_job(row["id"], "Wartet")
        worker.start()
        wait_until(lambda: any(r["status"] == "Aktiv" for r in store.jobs()))
        worker.delete_all()
        wait_until(lambda: not worker.running)
        assert not store.jobs() and not store.events()
        assert (tmp_path / "sources" / sample.name).exists()
    finally:
        stop_worker(worker)
