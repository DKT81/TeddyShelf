from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton

from teddystudio import library
from teddystudio.domain import Book, Track
from teddystudio.storage import Settings, Store
from teddystudio.ui_library import LibraryPage
from teddystudio.ui_audio import AudioPage


def test_folder_tree_resize_reorder_and_online_markers(app, tmp_path):
    settings = Settings(export=str(tmp_path / "export"))
    store = Store(tmp_path / "test.sqlite")
    book = Book(
        "a",
        str(tmp_path / "Serie" / "Episode"),
        "Episode",
        "Serie",
        1,
        tracks=[Track("one.wav", "One"), Track("two.wav", "Two")],
    )
    store.save_book(book)
    page = LibraryPage(store, settings)
    assert page.tree.maximumWidth() > 250
    assert page.vertical_split.count() == 2
    assert all(b.toolTip() for b in page.findChildren(QPushButton))
    page.grouping.setCurrentIndex(1)
    page.tree.setCurrentItem(page.tree.topLevelItem(0))
    assert page.cards.count() == 1
    assert page.tree.topLevelItem(1).childCount() > 0
    page.cards.setCurrentRow(0)
    page.reorder_tracks(0, 1)
    assert [t.title for t in page.current.tracks] == ["Two", "One"]
    assert page.remote_label(book) == "? Nicht geprüft"
    page.remote_models = []
    assert "Kein passendes" in page.remote_label(book)
    model = settings.profile().render(book)
    page.remote_models = [{**model, "audio_id": ["123"], "hash": ["abcd"]}]
    assert page.remote_label(book) == "● Modell online"
    page.refresh_cards()
    assert "Modell online" in page.cards.item(0).text()
    page.remote_models[0]["title"] = "Other"
    assert "abweichend" in page.remote_label(book)
    page.cloud_filter.setCurrentText("Prüfen")
    assert page.cards.count() == 1
    page.cloud_filter.setCurrentText("Cloud")
    assert page.cards.count() == 0
    page.remote_models[0]["title"] = model["title"]
    page.refresh_cards()
    assert page.cards.count() == 1
    page.cloud_filter.setCurrentText("Lokal")
    assert page.cards.count() == 0
    page.remote_models = []
    page.refresh_cards()
    assert page.cards.count() == 1
    page.remote_models = None
    page.refresh_cards()
    assert page.cards.count() == 0
    page.cloud_filter.setCurrentText("Prüfen")
    assert page.cards.count() == 1


def test_probe_cache_reuses_unchanged_and_invalidates_changed(
    sample, tmp_path, monkeypatch
):
    import shutil

    folder = tmp_path / "source" / "Book"
    folder.mkdir(parents=True)
    path = folder / "track.wav"
    shutil.copyfile(sample, path)
    settings = Settings(export=str(tmp_path / "export"))
    real_probe = library.probe
    calls = []

    def probe(path, binary):
        calls.append(path)
        return real_probe(path, binary)

    monkeypatch.setattr(library, "probe", probe)
    for _ in range(2):
        library.scan(folder.parent, settings, tmp_path / "cache")
    assert len(calls) == 1
    path.touch()
    library.scan(folder.parent, settings, tmp_path / "cache")
    assert len(calls) == 2


def test_audio_playlist_internal_order(app, tmp_path):
    page = AudioPage(Store(tmp_path / "test.sqlite"), Settings(), None, lambda: None)
    page.sources = [Track("one.wav", duration=2), Track("two.wav", duration=3)]
    page.refresh_sources()
    item = page.sources_list.takeItem(0)
    page.sources_list.addItem(item)
    page.sources_reordered()
    assert [t.path for t in page.sources] == ["two.wav", "one.wav"]
    assert page.sources_list.acceptDrops()
    assert page.parts.toolTip()


def test_split_preview_explains_26_sources_and_14_outputs(app, tmp_path):
    page = AudioPage(Store(tmp_path / "test.sqlite"), Settings(), None, lambda: None)
    page.sources = [Track(f"track-{n}.wav", duration=310) for n in range(26)]
    page.refresh_sources()
    assert page.parts.rowCount() == 14
    assert "26 Quelldateien" in page.summary.text()
    assert "14 Ausgabedateien" in page.summary.text()
    assert page.parts.item(0, 4).text() == "1, 2"
    assert page.parts.item(13, 4).text() == "26"
    assert "track-25.wav" in page.parts.item(13, 4).toolTip()
    assert page.splitter.count() == 3
    page.mode.setCurrentIndex(1)
    assert "1 TAF mit 14 Kapiteln" in page.summary.text()
    assert len({page.parts.item(n, 3).text() for n in range(14)}) == 1


def test_action_rows_stay_grouped_and_wrap_without_growing_buttons(app):
    from teddystudio.ui_common import row, button

    controls = [button(f"Aktion {n}", lambda: None) for n in range(4)]
    panel = row(*controls)
    panel.resize(2200, 300)
    panel.show()
    app.processEvents()
    assert all(b.height() <= b.sizeHint().height() for b in controls)
    for left, right in zip(controls, controls[1:]):
        assert 0 <= right.x() - (left.x() + left.width()) <= 6
    panel.resize(180, 300)
    app.processEvents()
    assert controls[-1].y() > controls[0].y()
    assert all(b.geometry().bottom() < panel.height() for b in controls)
    panel.close()


def test_compact_list_rows_on_large_window(app, tmp_path):
    from teddystudio.ui_common import LIGHT

    page = AudioPage(Store(tmp_path / "test.sqlite"), Settings(), None, lambda: None)
    page.setStyleSheet(LIGHT)
    page.sources = [Track(f"track-{n}.wav", duration=310) for n in range(26)]
    page.refresh_sources()
    page.resize(2300, 1300)
    page.show()
    app.processEvents()
    assert page.sources_list.sizeHintForRow(0) < 35
    assert all(b.height() < 45 for b in page.findChildren(QPushButton))
    page.close()
