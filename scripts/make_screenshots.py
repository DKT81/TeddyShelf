"""Render the real GUI with fictional fixtures, isolated storage and no network.

Run from the repository: python scripts/make_screenshots.py
Only documentation PNGs are published; generated cover fixtures stay in artifacts/.
"""

from dataclasses import asdict
from pathlib import Path
import math
import os
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_SCALE_FACTOR"] = "1"


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--language", default="de", choices=["de", "en"])
    parser.add_argument("--output", type=Path)
    options = parser.parse_args()
    # Never load the user's database, settings, covers or server inventory.
    temporary = tempfile.TemporaryDirectory(prefix="teddystudio-screenshots-")
    os.environ["TEDDYSHELF_DATA"] = temporary.name
    from PySide6.QtCore import Qt, QRectF, QTime
    from PySide6.QtGui import QColor, QPainter, QPixmap, QFont, QFontDatabase
    from PySide6.QtWidgets import QApplication
    from teddystudio.client import TeddyCloud
    from teddystudio.domain import Book, Track
    from teddystudio.storage import Store, Settings
    from teddystudio.ui_library import LibraryPage
    from teddystudio.app import MainWindow

    def no_network(*args, **kwargs):
        raise RuntimeError("Documentation screenshots must never access a server")

    TeddyCloud.request = no_network
    LibraryPage.sync_remote = lambda self: None
    app = QApplication([])
    app.setStyle("Fusion")
    QFontDatabase.addApplicationFont(str(ROOT / "assets" / "NotoSans-Regular.ttf"))
    symbols = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "seguisym.ttf"
    if symbols.is_file():
        QFontDatabase.addApplicationFont(str(symbols))
    output = (options.output or ROOT / "docs" / "images").resolve()
    fixtures = ROOT / "artifacts" / "screenshots-demo"
    output.mkdir(parents=True, exist_ok=True)
    fixtures.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        language=options.language,
        url="http://teddycloud.example",
        source="D:/Hoerspiele",
        export="D:/TeddyShelf/Export",
        download_destination="D:/TeddyShelf/Downloads",
        theme="Hell",
        parallel_jobs=2,
        show_unfinished=False,
    )
    store = Store(Path(temporary.name) / "library.sqlite")
    examples = [
        ("Sternenfreunde", "Das leuchtende Tal", "#17495c", "#eac56f"),
        ("Sternenfreunde", "Die Reise zum Mond", "#303963", "#bbc7ff"),
        ("Sternenfreunde", "Das Wolkenschiff", "#377984", "#d9f1de"),
        ("Waldgefluester", "Das Geheimnis der Eiche", "#315c4c", "#d4c58d"),
        ("Waldgefluester", "Spuren im Schnee", "#516c83", "#e5f0ec"),
        ("Waldgefluester", "Die kleine Lichtung", "#785c50", "#e7b678"),
        ("Traumreisen", "Am stillen See", "#475779", "#d0b7e1"),
        ("Traumreisen", "Unter tausend Sternen", "#354957", "#8dd1ca"),
    ]
    books = []
    for index, (series, title, dark, light) in enumerate(examples):
        episode = sum(1 for prior in examples[: index + 1] if prior[0] == series)
        image = QPixmap(480, 480)
        image.fill(QColor(dark))
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(light))
        painter.drawEllipse(295, 65, 110, 110)
        painter.setBrush(QColor("#ffffff"))
        for n in range(12):
            painter.drawEllipse(30 + (n * 67) % 410, 35 + (n * 37) % 160, 4, 4)
        painter.setOpacity(0.22)
        painter.drawEllipse(-100, 270, 650, 400)
        painter.setOpacity(1)
        painter.setPen(QColor("white"))
        painter.setFont(QFont("Noto Sans", 15))
        painter.drawText(QRectF(32, 28, 410, 40), series.upper())
        painter.setFont(QFont("Noto Sans", 28, QFont.Weight.Bold))
        painter.drawText(QRectF(32, 210, 410, 150), Qt.TextFlag.TextWordWrap, title)
        painter.setFont(QFont("Noto Sans", 13))
        painter.drawText(32, 445, f"FOLGE {episode:02}   /   DEMO")
        painter.end()
        cover = fixtures / f"cover-{index + 1}.png"
        image.save(str(cover))
        folder = f"D:/Hoerspiele/{series}/{episode:03}"
        tracks = [
            Track(f"{folder}/{n:02}-{name}.mp3", name, str(n), duration)
            for n, (name, duration) in enumerate(
                [
                    ("Ein neuer Morgen", 375),
                    ("Eine unerwartete Nachricht", 410),
                    ("Aufbruch ins Abenteuer", 482),
                    ("Der verborgene Weg", 396),
                    ("Zusammen ans Ziel", 420),
                    ("Wieder zu Hause", 277),
                ],
                1,
            )
        ]
        book = Book(
            f"demo-{index + 1}",
            folder,
            title,
            series,
            episode,
            album=title,
            artist="Demo-Ensemble",
            albumartist="Demo-Studio",
            cover=cover.relative_to(ROOT).as_posix(),
            cover_source="Erfundenes Demo-Cover",
            cover_confirmed=True,
            tracks=tracks,
            origin="Demo · erfundene Beispieldaten",
            status="Bereit",
        )
        books.append(book)
        store.save_book(book)

    window = MainWindow(settings, store)
    window.resize(1600, 1020)
    window.statusBar().showMessage(
        "DEMO · Alle Titel, Cover, Serverdaten und Auftragszustände sind erfunden. Kein Serverzugriff."
    )
    library = window.collection
    library.remote_models = []
    for index in (0, 1, 3, 6):
        model = settings.profile().render(books[index])
        model.update(audio_id=[str(1000 + index)], hash=["0" * 40])
        if index == 3:
            model["episodes"] = "Abweichender Demotitel"
        library.remote_models.append(model)
    library.remote_message = (
        "Demo-Bestand · Modellabgleich dargestellt; kein realer Server"
    )
    library.refresh()
    window.show()
    app.processEvents()
    library.cards.setCurrentRow(0)
    library.cards.item(0).setSelected(True)
    library.show_book(library.cards.item(0))
    library.vertical_split.setSizes([470, 290])

    def capture(index, filename):
        window.navigation.setCurrentRow(index)
        app.processEvents()
        app.processEvents()
        path = output / filename
        if not window.grab().save(str(path)):
            raise RuntimeError(f"Could not save {path}")
        print(path.relative_to(ROOT).as_posix())

    capture(0, "sammlung.png")
    audio = window.audio_page
    audio.from_collection()
    audio.length.setTime(QTime(0, 10, 0))
    audio.sources_list.setCurrentRow(2)
    # A deterministic illustrative waveform; no private audio needs to be read.
    audio.wave.values = [
        0.12 + 0.65 * abs(math.sin(n * 0.13) * math.cos(n * 0.037)) for n in range(650)
    ]
    audio.splitter.setSizes([220, 140, 400])
    capture(2, "audio.png")
    window.covers_page.list.setCurrentRow(1)
    capture(3, "cover.png")
    window.settings_page.set_preview_book(books[0])
    window.settings_page.navigation.setCurrentRow(2)
    capture(5, "namensprofile.png")

    entries = []
    for index, book in enumerate(books):
        model = settings.profile().render(book)
        available = index != 7
        entries.append(
            {
                "id": f"demo-entry-{index}",
                "title": book.title,
                "series": book.series,
                "model": model,
                "aliases": [model],
                "kind": "Katalog" if index in (4, 7) else "Custom",
                "available": available,
                "audio_id": str(1000 + index),
                "hash": "0" * 40,
                "status": (
                    "Audio vorhanden" if available else "Nur Metadaten · Audio fehlt"
                ),
                "locations": (
                    [{"path": f"library/{book.series}/{model['filename']}.taf"}]
                    if available
                    else []
                ),
            }
        )
    cloud = window.cloud_page
    cloud.snapshot = {
        "url": settings.url,
        "overlay": "",
        "entries": entries,
        "warnings": [],
    }
    cloud.refresh()
    cloud.tree.setColumnWidth(0, 290)
    cloud.tree.setColumnWidth(1, 180)
    cloud.tree.setColumnWidth(2, 90)
    cloud.tree.setColumnWidth(3, 230)
    for index in (0, 1, 2):
        cloud.tree.topLevelItem(index).setCheckState(0, Qt.CheckState.Checked)
    capture(6, "teddycloud.png")

    for index, (state, progress, message) in enumerate(
        [
            ("Bestätigt", 1, "Demo: Audio, Cover und Modell geprüft"),
            ("Lokal gespeichert", 1, "Demo: 4 Abschnitte lokal gespeichert"),
            ("Aktiv", 0.64, "Demo: TAF konvertieren · PCM nur temporär"),
            ("Pausiert", 0.25, "Demo: Arbeitsstand erhalten · PCM bereinigt"),
            ("Wartet", 0, "Demo: wartet auf freien Platz"),
            ("Fehlgeschlagen", 0, "Demo: Server nicht erreichbar · wiederholbar"),
        ]
    ):
        payload = {
            "book": books[index].to_dict(),
            "settings": asdict(settings),
            "profile": asdict(settings.profile()),
        }
        identifier = store.add_job(
            "Audio aufteilen" if index == 1 else "Import", payload
        )
        store.update_job(identifier, state, progress, message)
        store.event(identifier, state, message)
    window.jobs_page.refresh()
    table = window.jobs_page.table
    for column, width in enumerate([310, 130, 190, 100]):
        table.setColumnWidth(column, width)
    table.setCurrentCell(3, 0)
    table.selectRow(3)
    capture(4, "auftraege.png")
    window.close()
    app.processEvents()
    temporary.cleanup()


if __name__ == "__main__":
    main()
