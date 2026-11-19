"""Offline acceptance check of the actual packaged executable and its tools."""

from dataclasses import asdict
from pathlib import Path
import json
import os
import sys
import traceback


def run(directory):
    root = Path(directory).resolve()
    root.mkdir(parents=True, exist_ok=True)
    os.environ["TEDDYSTUDIO_DATA"] = str(root / "data")
    os.environ["TEDDYSHELF_DATA"] = str(root / "data")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    report = {"passed": False, "frozen": bool(getattr(sys, "frozen", False))}
    try:
        from PySide6.QtWidgets import QApplication
        from . import media, taf
        from .domain import Book, Track
        from .storage import Store, Settings
        from .app import MainWindow
        from .ui_library import LibraryPage
        from .jobs import execute
        from .covers import prepare

        app = QApplication.instance() or QApplication([])
        # Package acceptance is deliberately offline; server reads have separate tests.
        LibraryPage.sync_remote = lambda self: None
        source = root / "sources"
        source.mkdir(exist_ok=True)
        audio = source / "Test.wav"
        media.run(
            [
                media.binary("ffmpeg"),
                "-v",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:duration=5.2",
                "-ac",
                "2",
                audio,
            ]
        )
        pcm = root / "test.pcm"
        media.concat_pcm([audio], pcm)
        taf.create(pcm, root / "test.taf", [0, 2, 4])
        report["taf"] = taf.validate(root / "test.taf", expected=5.2, chapters=3)
        settings = Settings(
            source=str(source), export=str(root / "export"), reopen_collection=False
        )
        cover = prepare(
            "", root / "cover.jpg", Settings(cover_missing="Neutrales Symbol")
        )
        book = Book(
            "selftest",
            str(source),
            "Das Fohlen – Testdaten",
            "Die Pferdefreunde",
            1,
            cover=str(cover),
            tracks=[Track(str(audio), "Testspur", "01", 5.2)],
        )
        payload = {
            "book": book.to_dict(),
            "settings": asdict(settings),
            "profile": asdict(settings.profile()),
            "audio": {
                "seconds": 2,
                "mode": "Separate Dateien / Custom-Tonies",
                "target": "Nur lokal",
                "format": "mp3",
                "destination": str(root / "export"),
            },
        }
        report["split"] = execute(
            "Audio aufteilen", payload, root / "work", lambda *_: None
        )
        playlist = json.loads(json.dumps(payload))
        playlist["book"]["tracks"] *= 2
        playlist["audio"].update(
            mode=media.AUDIO_MODES[2],
            seconds=0,
            destination=str(root / "playlist-export"),
        )
        execute("Audio aufteilen", playlist, root / "playlist-work", lambda *_: None)
        playlist_taf = next((root / "playlist-export").glob("*.taf"))
        report["playlist"] = taf.validate(playlist_taf, expected=10.4, chapters=2)
        assert not list((root / "playlist-work").rglob("*.pcm"))
        store = Store(root / "data" / "library.sqlite")
        store.save_book(book)
        from .jobs import Worker
        import time

        settings.parallel_jobs = 2
        for index in range(2):
            queued = json.loads(json.dumps(payload))
            queued["book"]["title"] = f"Warteschlangen-Test {index+1}"
            queued["book"]["episode"] = index + 101
            queued["audio"]["destination"] = str(root / "queue-export")
            store.add_job("Audio aufteilen", queued)
        worker = Worker(store, settings=settings)
        peak = 0
        worker.start()
        deadline = time.monotonic() + 40
        try:
            while worker.running and time.monotonic() < deadline:
                peak = max(peak, sum(r["status"] == "Aktiv" for r in store.jobs()))
                time.sleep(0.02)
            assert (
                not worker.running
            ), "Warteschlangen-Selbsttest überschreitet Zeitlimit"
            assert peak == 2 and all(
                r["status"] == "Lokal gespeichert" for r in store.jobs()
            ), store.events()
            report["parallel_queue"] = {
                "peak_active": peak,
                "completed": len(store.jobs()),
            }
            assert not list((root / "work").rglob("*.pcm"))
            assert not list((root / "data" / "work").rglob("*.pcm"))
            report["pcm_cleanup"] = (
                "no decoded PCM retained after local and parallel jobs"
            )
        finally:
            if worker.running:
                worker.cancel_all()
                worker.thread.join(20)
        window = MainWindow(settings, store)
        from . import __version__

        assert window.version_label.text() == f"V {__version__}"
        report["version"] = __version__
        window.show()
        window.collection.cards.setCurrentRow(0)
        window.collection.cards.item(0).setSelected(True)
        window.collection.show_book(window.collection.cards.item(0))
        window.audio_page.from_collection()
        # The Windows acceptance test exercises the real native preview backend,
        # muted so an automated test does not unexpectedly play a tone.
        if sys.platform == "win32":
            window.audio_page.ensure_player()
            window.audio_page.output_audio.setVolume(0)
            window.audio_page.play()
            app.processEvents()
            window.audio_page.stop()
            report["preview_backend"] = "initialized"
        window.audio_page.wave.values = media.waveform([audio])
        window.covers_page.list.setCurrentRow(0)
        screenshots = []
        for index, name in enumerate(
            [
                "Sammlung",
                "Metadaten",
                "Audio",
                "Cover",
                "Auftraege",
                "Einstellungen",
                "TeddyCloud-Bestand",
                "Tonies-Tags",
            ]
        ):
            window.navigation.setCurrentRow(index)
            app.processEvents()
            path = root / f"GUI-{name}.png"
            if not window.grab().save(str(path)):
                raise RuntimeError("Screenshot fehlgeschlagen")
            screenshots.append(str(path))
        for index, name in [(2, "Namensprofile"), (3, "Cover-Vorgaben")]:
            window.settings_page.navigation.setCurrentRow(index)
            app.processEvents()
            window.grab().save(str(root / f"GUI-{name}.png"))
        window.close()
        app.processEvents()
        report.update(
            {
                "passed": True,
                "screenshots": screenshots,
                "ffmpeg": media.binary("ffmpeg"),
                "ffprobe": media.binary("ffprobe"),
            }
        )
    except Exception:
        report["error"] = traceback.format_exc()
    (root / "selftest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return 0 if report["passed"] else 1
