"""Language changes affect presentation, never persisted media or option values."""

import json
from pathlib import Path

import pytest
from PySide6.QtWidgets import QLabel, QComboBox, QListWidgetItem

from teddystudio.i18n import manager, text, format_text, render, validate_catalog
from teddystudio.i18n_widgets import create, call


@pytest.fixture(autouse=True)
def german_after_test():
    yield
    manager.set_language("de")


def test_catalogs_placeholders_and_canonical_values(app):
    assert set(manager.catalogs["de"]["messages"]) == set(
        manager.catalogs["en"]["messages"]
    )
    for catalog in manager.catalogs.values():
        validate_catalog(catalog)
    value = text("Einstellungen")
    manager.set_language("en")
    assert render(value) == "Settings"
    assert value == "Einstellungen"
    assert json.loads(json.dumps({"option": value}))["option"] == "Einstellungen"
    assert render("Einstellungen") == "Einstellungen"  # A user title is untouched.
    assert (
        render(format_text("Profil: {p0}", p0="Mein Hörspiel"))
        == "Profile: Mein Hörspiel"
    )
    assert manager.normalize("Deutsch") == "de"
    assert manager.normalize("missing") == "de"


def test_live_bindings_keep_canonical_selections_and_user_text(app):
    label = create(QLabel, text("Einstellungen"))
    combo = create(QComboBox)
    combo.addItems([text("Hell"), text("Dunkel")])
    combo.setCurrentText("Dunkel")
    item = create(QListWidgetItem, text("Sammlung"))
    manager.set_language("en")
    assert label.text() == "Settings"
    assert combo.itemText(1) == "Dark"
    assert combo.currentText() == "Dunkel"
    assert item.text() == "Sammlung"
    assert QListWidgetItem.text(item) == "Collection"
    call(label, "setText", "Mein Titel")
    manager.set_language("de")
    assert label.text() == "Mein Titel"
    assert combo.currentText() == "Dunkel"


def test_extra_language_fallback_and_invalid_placeholders(app, tmp_path):
    catalog = {
        "code": "fr",
        "name": "Français",
        "messages": {"Einstellungen": "Paramètres"},
    }
    (tmp_path / "fr.json").write_text(json.dumps(catalog), encoding="utf-8")
    try:
        manager.reload([tmp_path])
        manager.set_language("fr")
        assert render(text("Einstellungen")) == "Paramètres"
        assert render(text("Sammlung")) == "Sammlung"
        catalog["messages"] = {"Profil: {p0}": "Profil"}
        with pytest.raises(ValueError, match="placeholders"):
            validate_catalog(catalog)
        (tmp_path / "broken.json").write_text("not JSON", encoding="utf-8")
        manager.reload([tmp_path])  # A bad optional catalog does not break startup.
    finally:
        manager.reload()
        manager.set_language("de")


def test_main_window_switch_is_immediate_and_saved(app, tmp_path, monkeypatch):
    from teddystudio.app import MainWindow
    from teddystudio.domain import Book
    from teddystudio.storage import Settings, Store

    monkeypatch.setenv("TEDDYSHELF_DATA", str(tmp_path))
    store = Store(tmp_path / "library.sqlite")
    store.save_book(Book("example", str(tmp_path), "Einstellungen", "Meine Serie"))
    settings = Settings(language="de", source=str(tmp_path), reopen_collection=False)
    window = MainWindow(settings, store)
    worker = window.worker
    combo = window.settings_page.controls["language"]
    combo.setCurrentIndex(combo.findData("en"))
    app.processEvents()
    assert settings.language == "en"
    assert QListWidgetItem.text(window.navigation.item(0)) == "Collection"
    assert window.collection.books[0].title == "Einstellungen"
    assert window.worker is worker
    assert not store.jobs()
    assert (
        json.loads((tmp_path / "settings.json").read_text("utf-8"))["language"] == "en"
    )
    combo.setCurrentIndex(combo.findData("de"))
    app.processEvents()
    assert QListWidgetItem.text(window.navigation.item(0)) == "Sammlung"
    window.close()
    app.processEvents()


def test_saved_english_starts_in_english(app, tmp_path, monkeypatch):
    from teddystudio.app import MainWindow
    from teddystudio.storage import Settings, Store

    monkeypatch.setenv("TEDDYSHELF_DATA", str(tmp_path))
    window = MainWindow(
        Settings(language="en", source=str(tmp_path), reopen_collection=False),
        Store(tmp_path / "library.sqlite"),
    )
    assert QListWidgetItem.text(window.navigation.item(0)) == "Collection"
    assert window.settings_page.controls["language"].currentData() == "en"
    window.close()
    app.processEvents()


def test_qt_standard_buttons_and_custom_catalog_fallback(app, tmp_path):
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtWidgets import QMessageBox

    for language, expected in [("en", "Cancel"), ("de", "Abbrechen")]:
        manager.set_language(language)
        dialog = QMessageBox()
        dialog.setStandardButtons(QMessageBox.StandardButton.Cancel)
        assert expected in dialog.button(QMessageBox.StandardButton.Cancel).text()
    catalog = {
        "code": "fr",
        "name": "Français",
        "messages": {},
        "qt": {"Cancel": "Annuler"},
    }
    (tmp_path / "fr.json").write_text(json.dumps(catalog), encoding="utf-8")
    try:
        manager.reload([tmp_path])
        manager.set_language("fr")
        assert QCoreApplication.translate("QDialogButtonBox", "Cancel") == "Annuler"
        assert (
            QCoreApplication.translate("QDialogButtonBox", "Untouched") == "Untouched"
        )
    finally:
        manager.reload()
        manager.set_language("de")
