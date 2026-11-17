"""Application composition. Views call services; workers never manipulate widgets."""

from dataclasses import asdict
from pathlib import Path
import json
import os
import sys

from PySide6.QtCore import QObject, Signal, QTimer, Qt
from PySide6.QtGui import QPalette, QFontDatabase, QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QListWidget,
    QStackedWidget,
    QLabel,
    QMessageBox,
    QDialog,
    QPlainTextEdit,
    QDialogButtonBox,
    QInputDialog,
)

from .client import TeddyCloud
from .domain import Book
from .jobs import Worker, exclusive_lock
from .storage import Store, Settings, data_directory
from .ui_common import LIGHT, DARK, background, button
from .ui_library import LibraryPage, FIELDS
from .ui_settings import SettingsPage
from .ui_covers import CoversPage
from .ui_audio import AudioPage
from .ui_jobs import JobsPage


class Bridge(QObject):
    changed = Signal()


class MainWindow(QMainWindow):
    def __init__(self, settings=None, store=None):
        super().__init__()
        font = (
            Path(getattr(sys, "_MEIPASS", Path(__file__).parents[2]))
            / "assets"
            / "NotoSans-Regular.ttf"
        )
        if font.is_file():
            QFontDatabase.addApplicationFont(str(font))
        self.setWindowTitle("TeddyStudio · Import & Audio")
        assets = Path(getattr(sys, "_MEIPASS", Path(__file__).parents[2])) / "assets"
        self.setWindowIcon(QIcon(str(assets / "teddystudio.svg")))
        self.resize(1480, 980)
        self.settings = settings or Settings.load()
        self.store = store or Store()
        from .workspace import clean_inactive

        clean_inactive(data_directory() / "work")
        self.bridge = Bridge()
        self.worker = Worker(self.store, self.bridge.changed.emit, self.settings)
        root = QWidget()
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        sidebar = QVBoxLayout()
        mark = QLabel()
        mark.setPixmap(
            QPixmap(str(assets / "teddystudio.svg")).scaled(
                96,
                96,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sidebar.addWidget(mark)
        logo = QLabel("TeddyStudio\nImport & Audio")
        logo.setStyleSheet(
            "font-size: 23px; font-weight: 700; padding: 20px; color: #00877e;"
        )
        sidebar.addWidget(logo)
        self.navigation = QListWidget()
        self.navigation.addItems(
            [
                "Sammlung",
                "Stapelbearbeitung",
                "Audio",
                "Cover & Etiketten",
                "Aufträge",
                "Einstellungen",
                "TeddyCloud-Bestand",
                "Tonies / Tags",
            ]
        )
        self.navigation.setFixedWidth(205)
        sidebar.addWidget(self.navigation)
        sidebar.addWidget(
            QLabel(
                "Lokal arbeiten · geprüft übertragen\nTagänderungen nur nach Vorschau"
            )
        )
        layout.addLayout(sidebar)
        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)
        self.collection = LibraryPage(self.store, self.settings)
        self.settings_page = SettingsPage(self.settings)
        self.jobs_page = JobsPage(self.store, self.worker)
        self.audio_page = AudioPage(
            self.store, self.settings, self.collection, self.jobs_added
        )
        self.covers_page = CoversPage(self.collection, self.settings_page)
        from .ui_cloud import CloudPage, TagsPage

        self.cloud_page = CloudPage(self.store, self.settings, self.jobs_added)
        self.tags_page = TagsPage(
            self.store, self.settings, self.cloud_page, self.jobs_added
        )

        def offer_tag(entry):
            self.tags_page.loaded = None
            self.tags_page.overlay.setText(self.cloud_page.snapshot["overlay"])
            self.tags_page.pending_entry = entry
            self.tags_page.bind_entry(entry)
            self.navigation.setCurrentRow(7)

        self.cloud_page.assign_requested.connect(offer_tag)

        def inventory_changed(snapshot):
            self.collection.server_inventory = snapshot
            self.collection.refresh_cards()

        self.cloud_page.inventory_changed.connect(inventory_changed)
        # Metadata shares the same source of truth, with explicit batch operations.
        metadata = QWidget()
        ml = QVBoxLayout(metadata)
        ml.addWidget(QLabel("Metadaten mehrerer Hörspiele gemeinsam ändern"))
        ml.addWidget(
            QLabel(
                "Einzelne Titel und Spuren bearbeiten Sie in der Sammlung. Diese Ansicht ist für Stapeländerungen, z. B. derselbe Sprecher oder dasselbe Genre für eine ganze Serie.\nFür mehrere ausgewählte Hörspiele können hier gemeinsame Angaben mit Vorschau geändert werden."
            )
        )
        ml.addWidget(
            button(
                "Detailansicht der Sammlung öffnen",
                lambda: self.navigation.setCurrentRow(0),
            )
        )
        ml.addWidget(
            button(
                "Ausgewählte Hörspiele gemeinsam bearbeiten …",
                self.batch_metadata,
                True,
            )
        )
        ml.addWidget(button("Serienzuordnung ändern …", self.collection.regroup))
        ml.addStretch()
        for page in [
            self.collection,
            metadata,
            self.audio_page,
            self.covers_page,
            self.jobs_page,
            self.settings_page,
            self.cloud_page,
            self.tags_page,
        ]:
            self.stack.addWidget(page)
        self.navigation.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.navigation.setCurrentRow(0)
        self.settings_page.saved.connect(self.apply_theme)
        self.collection.import_requested.connect(self.preview_import)
        self.collection.selected.connect(self.settings_page.set_preview_book)
        self.bridge.changed.connect(self.jobs_changed)
        self.statusBar().showMessage("Bereit · Quell-Audios bleiben erhalten")
        self.apply_theme()
        if self.settings.show_unfinished and any(
            j["status"] in {"Wartet", "Unterbrochen"} for j in self.store.jobs()
        ):
            self.navigation.setCurrentRow(4)
        if (
            self.settings.reopen_collection
            and self.settings.last_collection
            and not self.collection.books
        ):
            QTimer.singleShot(
                0, lambda: self.collection.start_scan(self.settings.last_collection)
            )

    def apply_theme(self):
        dark = (
            self.settings.theme == "Dunkel"
            or self.settings.theme == "System"
            and QApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
        )
        QApplication.instance().setStyleSheet(DARK if dark else LIGHT)

    def batch_metadata(self):
        books = self.collection.selection()
        if not books:
            QMessageBox.information(
                self, "Metadaten", "Bitte Hörspiele in der Sammlung auswählen."
            )
            return
        label, ok = QInputDialog.getItem(
            self,
            "Stapelbearbeitung",
            "Gemeinsames Feld",
            list(FIELDS.values()),
            editable=False,
        )
        if not ok:
            return
        key = next(k for k, v in FIELDS.items() if v == label)
        value, ok = QInputDialog.getText(
            self, "Stapelbearbeitung", f"Neuer Wert für {len(books)} Hörspiele:"
        )
        if not ok:
            return
        try:
            value = (
                int(value)
                if key == "episode" and value
                else None if key == "episode" else value
            )
            preview = "\n".join(
                f"{book.title}: {getattr(book, key)} → {value}" for book in books
            )
            if (
                QMessageBox.question(
                    self,
                    "Änderungsvorschau",
                    preview
                    + "\n\nIn der Sammlung übernehmen (Quelldateien bleiben unverändert)?",
                )
                != QMessageBox.StandardButton.Yes
            ):
                return
            for book in books:
                setattr(book, key, value)
                book.origin = "Manuell bearbeitete Metadaten"
                self.store.save_book(book)
            self.collection.refresh()
            self.collection.changed.emit()
        except Exception as exc:
            QMessageBox.warning(self, "Metadaten", str(exc))

    def preview_import(self, books):
        if not books:
            QMessageBox.information(
                self, "Import", "Bitte mindestens ein Hörspiel auswählen."
            )
            return
        try:
            from .library import resolve_legacy_order

            for book in books:
                if resolve_legacy_order(book):
                    self.store.save_book(book)
            unresolved = [
                book
                for book in books
                if not book.tracks
                or book.order_pending
                or any(t.error for t in book.tracks)
            ]
            if unresolved:
                raise ValueError(
                    "Import noch nicht möglich:\n"
                    + "\n".join(
                        book.title
                        + ": "
                        + "; ".join(
                            dict.fromkeys(t.error for t in book.tracks if t.error)
                        )
                        for book in unresolved
                    )
                    + "\n\nNur geänderte oder nicht eindeutig erkennbare Reihenfolgen unter Spuren & Reihenfolge prüfen und bestätigen. Defekte Audiodateien müssen separat behoben werden."
                )
            naming = [(book, self.settings.profile().render(book)) for book in books]
            models = [n["model"].casefold() for _, n in naming]
            files = [
                (n["audio_folder"] + "/" + n["filename"]).casefold() for _, n in naming
            ]
            if len(set(models)) != len(models) or len(set(files)) != len(files):
                raise ValueError(
                    "Modellkennungen oder Audio-Zielpfade kollidieren. Bitte Namensprofil korrigieren."
                )
            for job in self.store.jobs():
                if (
                    job["status"] in {"Wartet", "Aktiv", "Unterbrochen"}
                    and job["kind"] == "Import"
                ):
                    payload = json.loads(job["payload"])
                    from .domain import NamingProfile

                    queued = NamingProfile(**payload["profile"]).render(
                        Book.from_dict(payload["book"])
                    )
                    if queued["model"].casefold() in models:
                        raise ValueError(
                            "Für diese Modellkennung besteht bereits ein offener Auftrag: "
                            + queued["model"]
                        )
        except Exception as exc:
            QMessageBox.warning(self, "Importvorschau", str(exc))
            return
        self.statusBar().showMessage(
            "Serverbestand wird für die Importvorschau gelesen …"
        )

        def read(_):
            with TeddyCloud(self.settings) as client:
                return client.models()

        def show(models):
            lines = []
            for book, names in naming:
                matches = [m for m in models if m.get("model") == names["model"]]
                action = (
                    "Neu"
                    if not matches
                    else "Vorhanden – Audio und Daten werden geprüft, Abweichungen aktualisiert"
                )
                if len(matches) > 1:
                    QMessageBox.warning(
                        self, "Import", "Doppelte Serverkennung: " + names["model"]
                    )
                    return
                book.status = "Vorhanden" if matches else "Neu"
                lines += [
                    f"{book.title} · {action}",
                    f"  Modell: {names['model']}",
                    f"  Audio: {names['audio_folder']}/{names['filename']}.taf",
                    f"  Cover: {book.cover or 'Fehlt – Import erfolgt trotzdem'}",
                    "",
                ]
            dialog = QDialog(self)
            dialog.setWindowTitle("Importvorschau")
            dialog.resize(900, 600)
            layout = QVBoxLayout(dialog)
            text = QPlainTextEdit("\n".join(lines))
            text.setReadOnly(True)
            layout.addWidget(text)
            layout.addWidget(
                QLabel(
                    "Nur diese Modelle werden bearbeitet. Keine RFID-Zuordnung. Erfolg erst nach vollständiger Prüfung."
                )
            )
            buttons = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Ok
                | QDialogButtonBox.StandardButton.Cancel
            )
            buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
                "Importaufträge erstellen"
            )
            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
            layout.addWidget(buttons)
            if dialog.exec():
                for book, _ in naming:
                    self.store.add_job(
                        "Import",
                        {
                            "book": book.to_dict(),
                            "settings": asdict(self.settings),
                            "profile": asdict(self.settings.profile()),
                        },
                    )
                self.jobs_added()
            self.statusBar().showMessage("Importvorschau abgeschlossen")

        background(self, read, show)

    def jobs_added(self):
        self.jobs_page.show_all()
        self.navigation.setCurrentRow(4)
        self.worker.start()

    def jobs_changed(self):
        self.jobs_page.refresh()
        known = {b.id for b in self.collection.books}
        added = [b for b in self.store.books() if b.id not in known]
        if added:
            self.collection.books.extend(added)
            self.collection.refresh()
            self.collection.changed.emit()
            self.cloud_page.refresh()
        latest = {}
        for entry in self.store.jobs():
            if entry["kind"] in {"Import", "Prüfung"}:
                identifier = json.loads(entry["payload"])["book"]["id"]
                latest.setdefault(identifier, entry)
        for book in self.collection.books:
            entry = latest.get(book.id)
            if entry:
                status = (
                    "Bestätigt"
                    if entry["status"] == "Bestätigt"
                    else (
                        "Prüfen"
                        if entry["status"] == "Fehlgeschlagen"
                        else entry["status"]
                    )
                )
                if book.status != status:
                    book.status = status
                    self.store.save_book(book)
        self.collection.refresh_cards()

    def closeEvent(self, event):
        if self.worker.running:
            self.worker.pause.set()
            self.statusBar().showMessage(
                "Nach dem aktuellen Titel pausiert. Danach kann die Anwendung geschlossen werden."
            )
            event.ignore()
            return
        if any(
            getattr(page, "_tasks", [])
            for page in [
                self.collection,
                self.audio_page,
                self.covers_page,
                self.cloud_page,
                self.tags_page,
                self.settings_page,
                self,
            ]
        ):
            self.statusBar().showMessage("Bitte den laufenden Vorgang abwarten.")
            event.ignore()
            return
        self.settings.save()
        self.audio_page.stop()
        event.accept()


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--self-test":
        from .selftest import run

        return run(sys.argv[2])
    app = QApplication(sys.argv)
    app.setApplicationName("TeddyStudio")
    app.setOrganizationName("TeddyStudio")
    try:
        with exclusive_lock(data_directory() / "application.lock"):
            window = MainWindow()
            window.show()
            return app.exec()
    except Exception as exc:
        QMessageBox.critical(None, "TeddyStudio", str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
