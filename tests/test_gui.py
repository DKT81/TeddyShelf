from pathlib import Path
from PySide6.QtCore import Qt
from teddystudio.app import MainWindow
from teddystudio.storage import Settings, Store
from teddystudio.domain import Book, Track


def test_all_views_profiles_metadata_and_audio_plan(app, tmp_path, monkeypatch):
    monkeypatch.setenv("TEDDYSTUDIO_DATA", str(tmp_path / "data"))
    settings = Settings(
        source=str(tmp_path / "source"), export=str(tmp_path / "export")
    )
    store = Store(tmp_path / "app.sqlite")
    store.save_book(
        Book(
            "1",
            str(tmp_path / "source"),
            "Das Fohlen",
            "Bibi-Tina",
            1,
            tracks=[Track("demo.wav", "Spur eins", "07", 5022)],
        )
    )
    window = MainWindow(settings, store)
    assert window.audio_page.player is None
    window.show()
    app.processEvents()
    for index in range(6):
        window.navigation.setCurrentRow(index)
        app.processEvents()
        assert window.stack.currentIndex() == index
    for index in range(8):
        window.settings_page.navigation.setCurrentRow(index)
        app.processEvents()
        assert window.settings_page.stack.currentIndex() == index
    collection = window.collection
    collection.cards.setCurrentRow(0)
    collection.show_book(collection.cards.item(0))
    collection.edits["episode"].setText("2")
    edited = collection.edited_book()
    assert edited.episode == 2 and edited.tracks[0].track == "07"
    audio = window.audio_page
    collection.cards.item(0).setSelected(True)
    audio.from_collection()
    assert audio.parts.rowCount() == 9 and audio.parts.item(8, 2).text() == "00:03:42"
    profiles = window.settings_page
    profiles.profile_controls["model_numbered"].setText("{serie_slug}-{folge:04}")
    assert "bibi-tina-0001" in profiles.preview_label.toPlainText()
    profiles.new_profile()
    profiles.delete_profile()
    assert len(profiles.profiles) == 1
    window.close()
    app.processEvents()


def test_label_pdf_and_explicit_playlist_confirmation(app, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox

    monkeypatch.setenv("TEDDYSTUDIO_DATA", str(tmp_path / "data"))
    store = Store(tmp_path / "data" / "app.sqlite")
    book = Book(
        "labels",
        str(tmp_path / "source"),
        "Testetikett",
        tracks=[
            Track(
                "demo.wav",
                "Spur",
                "07",
                4.2,
                error="Playlist und Audiodateien passen nicht eindeutig zusammen.",
            )
        ],
    )
    store.save_book(book)
    window = MainWindow(
        Settings(source=str(tmp_path / "source"), export=str(tmp_path / "export")),
        store,
    )
    window.collection.cards.setCurrentRow(0)
    window.collection.show_book(window.collection.cards.item(0))
    window.collection.current.tracks.append(Track("second.wav", "Zweite Spur", "08", 1))
    window.collection.show_book(window.collection.cards.item(0))
    window.collection.edits["title"].setText("Geänderter Titel")
    window.collection.tracks.setCurrentCell(0, 0)
    window.collection.move_track(1)
    assert window.collection.edits["title"].text() == "Geänderter Titel"
    window.collection.confirm_order()
    assert not any(track.error for track in store.books()[0].tracks)
    window.covers_page.book = window.collection.current
    target = tmp_path / "labels.pdf"
    monkeypatch.setattr(QInputDialog, "getDouble", lambda *a, **k: (35.0, True))
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), "PDF")
    )
    monkeypatch.setattr(
        QMessageBox, "information", lambda *a, **k: QMessageBox.StandardButton.Ok
    )
    window.covers_page.labels()
    assert target.read_bytes().startswith(b"%PDF-") and target.stat().st_size > 1000
    window.close()
