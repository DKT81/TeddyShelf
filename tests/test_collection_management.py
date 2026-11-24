"""Collection changes never delete media, and web receipts preserve identities."""

from pathlib import Path
import hashlib
import json
import shutil

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from teddystudio import library
from teddystudio.domain import Book, Track
from teddystudio.storage import Settings, Store, atomic_json
from teddystudio.ui_library import LibraryPage
from teddystudio.ui_jobs import JobsPage
from teddystudio.queue import Worker


def download(folder, sample, number, title):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{number:04}-ascii-name-0123456789.wav"
    shutil.copy2(sample, path)
    atomic_json(
        Path(str(path) + ".teddyshelf-web.json"),
        {
            "title": title,
            "index": number,
            "mode": "audio",
            "url": f"https://example.org/{number}",
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        },
    )
    return path


def test_old_web_receipts_are_individual_titles_and_partials_are_ignored(
    sample, tmp_path
):
    root = tmp_path / "Music" / "TeddyShelf-Web"
    paths = [download(root, sample, n, f"Original title {n}") for n in range(1, 5)]
    partial = root / ".teddyshelf-web" / "unfinished"
    partial.mkdir(parents=True)
    shutil.copy2(sample, partial / "media.wav")
    settings = Settings(export=str(tmp_path / "export"))
    books = library.scan(root, settings, tmp_path / "cache")
    assert len(books) == 4 and len({b.id for b in books}) == 4
    assert {b.title for b in books} == {f"Original title {n}" for n in range(1, 5)}
    assert all(not b.series and len(b.tracks) == 1 for b in books)
    assert {b.tracks[0].path for b in books} == {str(path) for path in paths}
    books_again = library.scan(root, settings, tmp_path / "cache")
    assert [b.id for b in books_again] == [b.id for b in books]


def test_web_receipt_hash_mismatch_is_visible(sample, tmp_path):
    path = download(tmp_path / "web", sample, 1, "Original")
    receipt = Path(str(path) + ".teddyshelf-web.json")
    metadata = json.loads(receipt.read_text("utf-8"))
    metadata["sha256"] = "0" * 64
    atomic_json(receipt, metadata)
    book = library.scan(
        path.parent, Settings(export=str(tmp_path / "export")), tmp_path / "cache"
    )[0]
    assert book.status == "Prüfen" and "verändert" in book.tracks[0].error


def test_refresh_replaces_legacy_aggregate_and_prunes_only_its_scope(
    app, sample, tmp_path, monkeypatch
):
    monkeypatch.setenv("TEDDYSHELF_DATA", str(tmp_path / "data"))
    root = tmp_path / "web"
    one = download(root, sample, 1, "First")
    two = download(root, sample, 2, "Second")
    store = Store(tmp_path / "library.sqlite")
    store.save_books(
        [
            Book(
                "legacy",
                str(root),
                "TeddyShelf-Web",
                "Music",
                tracks=[Track(str(one)), Track(str(two))],
            ),
            Book("elsewhere", str(tmp_path / "other"), "Other"),
        ]
    )
    page = LibraryPage(store, Settings(export=str(tmp_path / "export")))
    page.scanned(library.scan(root, page.settings, tmp_path / "cache"), scope=[root])
    assert {b.title for b in store.books()} == {"First", "Second", "Other"}
    first = next(b for b in page.books if b.title == "First")
    first.title = "My edit"
    first.origin = "Manuell bearbeitete Metadaten"
    store.save_book(first)
    two.unlink()
    page.scanned(library.scan(root, page.settings, tmp_path / "cache"), scope=[root])
    assert {b.title for b in store.books()} == {"My edit", "Other"}
    assert len(store.books()) == 2 and one.is_file()


def test_folder_removal_is_reference_only_and_survives_parent_refresh(
    app, sample, tmp_path, monkeypatch
):
    monkeypatch.setenv("TEDDYSHELF_DATA", str(tmp_path / "data"))
    root = tmp_path / "collection"
    keep = download(root / "Keep", sample, 1, "Keep")
    remove = download(root / "Remove", sample, 2, "Remove")
    prefix_sibling = download(root / "Remove-other", sample, 3, "Sibling")
    store = Store(tmp_path / "library.sqlite")
    settings = Settings(export=str(tmp_path / "export"))
    store.save_books(library.scan(root, settings, tmp_path / "cache"))
    store.register_collection_root(root)
    page = LibraryPage(store, settings)
    page.grouping.setCurrentIndex(1)

    def find(item, path):
        if item.data(0, Qt.ItemDataRole.UserRole) == str(path):
            return item
        for n in range(item.childCount()):
            found = find(item.child(n), path)
            if found:
                return found

    target = next(
        filter(
            None,
            (
                find(page.tree.topLevelItem(n), remove.parent)
                for n in range(page.tree.topLevelItemCount())
            ),
        )
    )
    page.tree.setCurrentItem(target)
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes
    )
    page.remove_folder()
    assert {b.title for b in store.books()} == {"Keep", "Sibling"}
    assert all(path.is_file() for path in (keep, remove, prefix_sibling))
    fresh = library.scan(
        root, settings, tmp_path / "cache", excluded=store.collection_exclusions()
    )
    page.scanned(fresh, scope=[root])
    assert {b.title for b in store.books()} == {"Keep", "Sibling"}
    store.register_collection_root(remove.parent)
    assert not store.collection_exclusions()
    assert len(library.scan(root, settings, tmp_path / "cache")) == 3


def test_readding_one_child_does_not_reinclude_removed_siblings(tmp_path):
    store = Store(tmp_path / "library.sqlite")
    root = tmp_path / "root"
    removed = root / "removed"
    child = removed / "child"
    store.register_collection_root(root)
    store.remove_collection_folder(removed)
    store.register_collection_root(child)
    assert str(removed.resolve()) in store.collection_exclusions()
    assert str(child.resolve()) in store.collection_roots()
    assert not library.within_folder(root / "removed-other", removed)


def test_completed_visibility_is_saved_and_not_reset_by_queue_start(
    app, tmp_path, monkeypatch
):
    monkeypatch.setenv("TEDDYSHELF_DATA", str(tmp_path))
    settings = Settings()
    worker = Worker(Store(tmp_path / "library.sqlite"), settings=settings)
    monkeypatch.setattr(worker, "start", lambda: None)
    page = JobsPage(worker.store, worker)
    page.hide_finished.setChecked(True)
    page.start_queue()
    page.show_all()
    assert page.hide_finished.isChecked() and Settings.load().hide_finished_jobs
    reopened = JobsPage(worker.store, Worker(worker.store, settings=Settings.load()))
    assert reopened.hide_finished.isChecked()


def test_refresh_discovers_new_children_without_reincluding_removed_siblings(tmp_path):
    root = tmp_path / "library"
    removed = root / "removed"
    included = removed / "explicit-child"
    store = Store(tmp_path / "library.sqlite")
    store.register_collection_root(root / "existing")
    store.remove_collection_folder(removed)
    store.register_collection_root(included)
    roots, excluded = store.collection_roots(), store.collection_exclusions()
    assert library.refresh_roots(str(root), roots, excluded) == [
        str(root),
        str(included),
    ]
    assert library.refresh_roots(str(removed), roots, excluded) == [str(included)]
    store.remember_collection_root(root)
    assert set(store.collection_roots()) == {str(root), str(included)}
    assert store.collection_exclusions() == [str(removed)]


def test_scan_refuses_partial_unreadable_directory(tmp_path, monkeypatch):
    root = tmp_path / "source"
    root.mkdir()

    def denied(*a, **kwargs):
        kwargs["onerror"](PermissionError("not readable"))
        yield root, [], []

    monkeypatch.setattr(library.os, "walk", denied)
    with pytest.raises(PermissionError):
        library.scan(
            root, Settings(export=str(tmp_path / "export")), tmp_path / "cache"
        )


def test_legacy_download_organizes_without_touching_original_audio(sample, tmp_path):
    import mutagen
    from teddystudio import media, web_downloads as web

    options = {
        "destination": str(tmp_path / "downloads"),
        "mode": "audio",
        "batch": "b" * 32,
        "playlist": "Demo album",
        "entry": {
            "index": 1,
            "url": "https://example.org/1",
            "title": "Meaningful title",
        },
    }
    source = web.output_path(options)
    source.parent.mkdir()
    media.run([media.binary("ffmpeg"), "-v", "error", "-y", "-i", sample, source])
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    atomic_json(
        Path(str(source) + ".teddyshelf-web.json"),
        {
            "title": "Meaningful title",
            "index": 1,
            "mode": "audio",
            "url": options["entry"]["url"],
            "sha256": original_hash,
            "duration": 4.2,
        },
    )
    converted = web.organize_download(options)
    target = Path(converted["path"])
    assert target.parent.parent == source.parent and target != source
    assert (
        source.is_file()
        and hashlib.sha256(source.read_bytes()).hexdigest() == original_hash
    )
    assert mutagen.File(target, easy=True)["title"] == ["Meaningful title"]
    assert mutagen.File(target, easy=True)["album"] == ["Meaningful title"]
    assert web.organize_download(options)["path"] == str(target)
    settings = Settings(export=str(tmp_path / "export"))
    books = library.scan(source.parent, settings, tmp_path / "cache")
    assert len(books) == 1 and books[0].folder == str(target.parent)
    # A missing copy must never cause the surviving original to disappear.
    target.unlink()
    books = library.scan(source.parent, settings, tmp_path / "cache")
    assert len(books) == 1 and books[0].tracks[0].path == str(source)
