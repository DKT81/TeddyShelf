from dataclasses import asdict
import json
import pytest
from PySide6.QtWidgets import QMessageBox, QLineEdit, QScrollArea
from teddystudio.domain import Book, Track, NamingProfile
from teddystudio.storage import Store, Settings
from teddystudio.library import playlist_order
from teddystudio.jobs import Worker, exclusive_lock
from teddystudio.ui_jobs import JobsPage
from teddystudio.ui_library import LibraryPage
from teddystudio.ui_common import LIGHT


def payload():
    return {
        "book": Book("1", "", "Test", tracks=[Track("one.wav")]).to_dict(),
        "settings": asdict(Settings()),
        "profile": asdict(NamingProfile()),
    }


def test_legacy_cp850_playlist_with_trailing_spaces(tmp_path):
    paths = [tmp_path / f"Hörspiel_{n:03}.mp3" for n in range(9)]
    playlist = tmp_path / "playlist.m3u"
    playlist.write_bytes(
        ("#EXTM3U \r\n" + "".join(p.name + " \r\n" for p in reversed(paths))).encode(
            "cp850"
        )
    )
    assert playlist_order(playlist, paths) == list(reversed(paths))
    playlist.write_text(paths[0].name + "\n" + paths[0].name, encoding="utf-8")
    assert playlist_order(playlist, paths) is None


def test_case_and_file_urls_match_but_partial_playlists_do_not(tmp_path):
    paths = [tmp_path / "Audio One.mp3", tmp_path / "Audio Two.mp3"]
    playlist = tmp_path / "playlist.m3u"
    playlist.write_text(
        '  #EXTM3U\n"AUDIO TWO.MP3"\nfile:///C:/old/Audio%20One.mp3\n', encoding="utf-8"
    )
    assert playlist_order(playlist, paths) == list(reversed(paths))
    playlist.write_text("AUDIO TWO.MP3\n", encoding="utf-8")
    assert playlist_order(playlist, paths) is None


def test_delete_journal_is_atomic_and_keeps_audio_and_associations(tmp_path):
    store = Store(tmp_path / "test.sqlite")
    a, b = [store.add_job("Import", payload()) for _ in range(2)]
    store.update_job(a, "Aktiv")
    with exclusive_lock(store.path.with_suffix(".lock")):
        with pytest.raises(ValueError):
            store.delete_jobs([a, b])
    assert len(store.jobs()) == 2 and store.events(b)
    store.update_job(a, "Fehlgeschlagen")
    with exclusive_lock(store.path.with_suffix(".lock")):
        assert store.delete_jobs([a, b]) == 2
    assert not store.jobs() and not store.events()


def test_jobs_multi_delete_hide_finished_and_safe_history(app, tmp_path, monkeypatch):
    store = Store(tmp_path / "test.sqlite")
    worker = Worker(store)
    ids = [store.add_job("Import", payload()) for _ in range(4)]
    for identifier, status in zip(
        ids, ["Bestätigt", "Fehlgeschlagen", "Wartet", "Unterbrochen"]
    ):
        store.update_job(identifier, status)
    page = JobsPage(store, worker)
    page.hide_finished.setChecked(True)
    assert len(page.rows) == 3
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes
    )
    page.clear_history()
    assert {r["status"] for r in store.jobs()} == {"Wartet", "Unterbrochen"}
    page.table.selectAll()
    page.delete_selected()
    assert not store.jobs() and not page.log.toPlainText()


def test_retry_corrected_import_creates_new_snapshot(app, tmp_path, monkeypatch):
    store = Store(tmp_path / "test.sqlite")
    original = payload()
    identifier = store.add_job("Import", original)
    store.update_job(identifier, "Fehlgeschlagen")
    book = Book.from_dict(original["book"])
    book.origin = "Manuell bestätigte Spurenreihenfolge"
    book.title = "Aktualisierter Titel"
    store.save_book(book)
    worker = Worker(store)
    monkeypatch.setattr(worker, "start", lambda: None)
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes
    )
    page = JobsPage(store, worker)
    page.table.setCurrentCell(0, 0)
    page.retry_one()
    assert len(store.jobs()) == 2
    old = next(r for r in store.jobs() if r["id"] == identifier)
    assert json.loads(old["payload"])["book"] == original["book"]
    new = next(r for r in store.jobs() if r["id"] != identifier)
    assert json.loads(new["payload"])["book"]["origin"] == book.origin


def test_inspector_scrolls_instead_of_squeezing_text(app, tmp_path):
    page = LibraryPage(Store(tmp_path / "test.sqlite"), Settings())
    page.setStyleSheet(LIGHT)
    page.resize(1480, 750)
    page.show()
    page.vertical_split.setSizes([500, 180])
    app.processEvents()
    assert page.findChildren(QScrollArea)
    for edit in page.edits.values():
        assert edit.height() >= edit.fontMetrics().height() + 6
    page.close()


def test_legacy_warning_resolves_without_manual_confirmation(tmp_path):
    from teddystudio.library import resolve_legacy_order, PLAYLIST_WARNING

    files = [tmp_path / f"Spur_{n}.mp3" for n in range(2)]
    for file in files:
        file.write_bytes(b"placeholder")
    (tmp_path / "playlist.m3u").write_bytes(
        ("#EXTM3U\n" + "".join(p.name + " \n" for p in files)).encode("cp850")
    )
    book = Book(
        "1",
        str(tmp_path),
        "Titel",
        tracks=[Track(str(p), error=PLAYLIST_WARNING) for p in files],
    )
    assert resolve_legacy_order(book)
    assert not any(t.error for t in book.tracks)
    assert not book.order_pending and not book.confirmed_order
    assert not resolve_legacy_order(book)


def test_confirmed_order_survives_rescan_until_sources_change(app, tmp_path):
    from teddystudio.library import PLAYLIST_WARNING

    store = Store(tmp_path / "test.sqlite")
    paths = [str(tmp_path / "two.mp3"), str(tmp_path / "one.mp3")]
    old = Book(
        "1",
        str(tmp_path),
        "Titel",
        origin="Manuell bearbeitete Metadaten",
        tracks=[Track(p) for p in paths],
        confirmed_order=paths,
    )
    store.save_book(old)
    page = LibraryPage(store, Settings())
    fresh = Book(
        "1",
        str(tmp_path),
        "Titel",
        tracks=[Track(p, error=PLAYLIST_WARNING) for p in reversed(paths)],
    )
    page.scanned([fresh])
    assert [t.path for t in page.books[0].tracks] == paths
    assert not any(t.error for t in page.books[0].tracks)
    assert page.books[0].confirmed_order == paths
    changed = Book(
        "1",
        str(tmp_path),
        "Titel",
        tracks=[Track(p) for p in paths] + [Track(str(tmp_path / "new.mp3"))],
    )
    page.scanned([changed])
    assert page.books[0].order_pending


def test_only_reordered_title_becomes_pending(app, tmp_path):
    store = Store(tmp_path / "test.sqlite")
    for n in range(2):
        store.save_book(
            Book(
                str(n),
                str(tmp_path),
                str(n),
                tracks=[Track("one.mp3"), Track("two.mp3")],
            )
        )
    page = LibraryPage(store, Settings())
    page.cards.setCurrentRow(0)
    assert not any(b.order_pending for b in page.books)
    assert not page.confirm_order_button.isEnabled()
    page.reorder_tracks(0, 1)
    assert sum(b.order_pending for b in page.books) == 1
    assert page.confirm_order_button.isEnabled()
    page.confirm_order()
    assert not any(b.order_pending for b in page.books)


def test_retry_status_change_keeps_original_job_and_work_cache(
    app, tmp_path, monkeypatch
):
    store = Store(tmp_path / "test.sqlite")
    original = payload()
    original["book"].pop("order_pending", None)
    original["book"].pop("confirmed_order", None)
    identifier = store.add_job("Import", original)
    store.update_job(identifier, "Fehlgeschlagen")
    current = Book.from_dict(original["book"])
    current.status = "Fehlgeschlagen"
    store.save_book(current)
    worker = Worker(store)
    monkeypatch.setattr(worker, "start", lambda: None)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *a, **k: pytest.fail(
            "Unveränderte Quelle darf keinen neuen Auftrag verlangen"
        ),
    )
    page = JobsPage(store, worker)
    page.table.setCurrentCell(0, 0)
    page.retry_one()
    assert len(store.jobs()) == 1 and store.jobs()[0]["id"] == identifier
    assert store.jobs()[0]["status"] == "Wartet"
