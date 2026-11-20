"""Application composition. Views call services; workers never manipulate widgets."""

from .i18n import text as tr_text, format_text as _f
from .i18n_widgets import call as _ui, create as _new

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
from . import __version__
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
            / tr_text("NotoSans-Regular.ttf")
        )
        if font.is_file():
            QFontDatabase.addApplicationFont(str(font))
        _ui(self, "setWindowTitle", "TeddyShelf · Import & Audio")
        assets = Path(getattr(sys, "_MEIPASS", Path(__file__).parents[2])) / "assets"
        self.setWindowIcon(QIcon(str(assets / "teddystudio.svg")))
        self.resize(1480, 980)
        self.settings = settings or Settings.load()
        from .i18n import manager

        manager.set_language(self.settings.language)
        self.store = store or Store()
        from .workspace import clean_inactive

        clean_inactive(data_directory() / "work")
        self.bridge = Bridge()
        self.worker = Worker(self.store, self.bridge.changed.emit, self.settings)
        self._closing_when_idle = False
        self._close_prompt_open = False
        self._shutdown_timer = QTimer(self)
        self._shutdown_timer.setInterval(100)
        self._shutdown_timer.timeout.connect(self._finish_shutdown)
        root = QWidget()
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        sidebar = QVBoxLayout()
        mark = _new(QLabel)
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
        logo = _new(QLabel, "TeddyShelf\nImport & Audio")
        logo.setStyleSheet(
            "font-size: 23px; font-weight: 700; padding: 20px; color: #00877e;"
        )
        sidebar.addWidget(logo)
        self.navigation = QListWidget()
        _ui(
            self.navigation,
            "addItems",
            [
                tr_text("Sammlung"),
                tr_text("Stapelbearbeitung"),
                tr_text("Audio"),
                tr_text("Cover & Etiketten"),
                tr_text("Aufträge"),
                tr_text("Einstellungen"),
                tr_text("TeddyCloud-Bestand"),
                tr_text("Tonies / Tags"),
            ],
        )
        self.navigation.setFixedWidth(205)
        sidebar.addWidget(self.navigation)
        sidebar.addWidget(
            _new(
                QLabel,
                tr_text(
                    "Lokal arbeiten · geprüft übertragen\nTagänderungen nur nach Vorschau"
                ),
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
            _ui(self.tags_page.overlay, "setText", self.cloud_page.snapshot["overlay"])
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
        ml.addWidget(
            _new(QLabel, tr_text("Metadaten mehrerer Hörspiele gemeinsam ändern"))
        )
        ml.addWidget(
            _new(
                QLabel,
                tr_text(
                    "Einzelne Titel und Spuren bearbeiten Sie in der Sammlung. Diese Ansicht ist für Stapeländerungen, z. B. derselbe Sprecher oder dasselbe Genre für eine ganze Serie.\nFür mehrere ausgewählte Hörspiele können hier gemeinsame Angaben mit Vorschau geändert werden."
                ),
            )
        )
        ml.addWidget(
            button(
                tr_text("Detailansicht der Sammlung öffnen"),
                lambda: self.navigation.setCurrentRow(0),
            )
        )
        ml.addWidget(
            button(
                tr_text("Ausgewählte Hörspiele gemeinsam bearbeiten …"),
                self.batch_metadata,
                True,
            )
        )
        ml.addWidget(
            button(tr_text("Serienzuordnung ändern …"), self.collection.regroup)
        )
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
        _ui(
            self.statusBar(),
            "showMessage",
            tr_text("Bereit · Quell-Audios bleiben erhalten"),
        )
        self.version_label = _new(QLabel, _f("V {p0}", p0=__version__))
        self.version_label.setObjectName("applicationVersion")
        _ui(
            self.version_label,
            "setToolTip",
            _f("TeddyShelf · Version {p0}", p0=__version__),
        )
        self.statusBar().addPermanentWidget(self.version_label)
        self.apply_theme()
        manager.listen(self, "_language_changed")
        if self.settings.show_unfinished and any(
            j["status"] in {tr_text("Wartet"), tr_text("Unterbrochen")}
            for j in self.store.jobs()
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

    def _language_changed(self):
        # Refresh values read from persisted jobs without changing their storage.
        self.collection.refresh_cards()
        self.jobs_page.refresh()
        self.audio_page.update_plan()
        self.cloud_page.refresh()
        for child in self.findChildren(QWidget):
            child.update()

    def apply_theme(self):
        dark = (
            self.settings.theme == tr_text("Dunkel")
            or self.settings.theme == tr_text("System")
            and QApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
        )
        QApplication.instance().setStyleSheet(DARK if dark else LIGHT)

    def batch_metadata(self):
        books = self.collection.selection()
        if not books:
            _ui(
                QMessageBox,
                "information",
                self,
                tr_text("Metadaten"),
                tr_text("Bitte Hörspiele in der Sammlung auswählen."),
            )
            return
        label, ok = _ui(
            QInputDialog,
            "getItem",
            self,
            tr_text("Stapelbearbeitung"),
            tr_text("Gemeinsames Feld"),
            list(FIELDS.values()),
            editable=False,
        )
        if not ok:
            return
        key = next(k for k, v in FIELDS.items() if v == label)
        value, ok = _ui(
            QInputDialog,
            "getText",
            self,
            tr_text("Stapelbearbeitung"),
            _f("Neuer Wert für {p0} Hörspiele:", p0=len(books)),
        )
        if not ok:
            return
        try:
            value = (
                int(value)
                if key == "episode" and value
                else None if key == "episode" else value
            )
            preview = tr_text("\n").join(
                _f("{p0}: {p1} → {p2}", p0=book.title, p1=getattr(book, key), p2=value)
                for book in books
            )
            if (
                _ui(
                    QMessageBox,
                    "question",
                    self,
                    tr_text("Änderungsvorschau"),
                    preview
                    + tr_text(
                        "\n\nIn der Sammlung übernehmen (Quelldateien bleiben unverändert)?"
                    ),
                )
                != QMessageBox.StandardButton.Yes
            ):
                return
            for book in books:
                setattr(book, key, value)
                book.origin = tr_text("Manuell bearbeitete Metadaten")
                self.store.save_book(book)
            self.collection.refresh()
            self.collection.changed.emit()
        except Exception as exc:
            _ui(QMessageBox, "warning", self, tr_text("Metadaten"), str(exc))

    def preview_import(self, books):
        if not books:
            _ui(
                QMessageBox,
                "information",
                self,
                tr_text("Import"),
                tr_text("Bitte mindestens ein Hörspiel auswählen."),
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
                    tr_text("Import noch nicht möglich:\n")
                    + tr_text("\n").join(
                        book.title
                        + ": "
                        + tr_text("; ").join(
                            dict.fromkeys(t.error for t in book.tracks if t.error)
                        )
                        for book in unresolved
                    )
                    + tr_text(
                        "\n\nNur geänderte oder nicht eindeutig erkennbare Reihenfolgen unter Spuren & Reihenfolge prüfen und bestätigen. Defekte Audiodateien müssen separat behoben werden."
                    )
                )
            naming = [(book, self.settings.profile().render(book)) for book in books]
            models = [n["model"].casefold() for _, n in naming]
            files = [
                (n["audio_folder"] + "/" + n["filename"]).casefold() for _, n in naming
            ]
            if len(set(models)) != len(models) or len(set(files)) != len(files):
                raise ValueError(
                    tr_text(
                        "Modellkennungen oder Audio-Zielpfade kollidieren. Bitte Namensprofil korrigieren."
                    )
                )
            for job in self.store.jobs():
                if job["status"] in {
                    tr_text("Wartet"),
                    tr_text("Aktiv"),
                    tr_text("Unterbrochen"),
                } and job["kind"] == tr_text("Import"):
                    payload = json.loads(job["payload"])
                    from .domain import NamingProfile

                    queued = NamingProfile(**payload["profile"]).render(
                        Book.from_dict(payload["book"])
                    )
                    if queued["model"].casefold() in models:
                        raise ValueError(
                            tr_text(
                                "Für diese Modellkennung besteht bereits ein offener Auftrag: "
                            )
                            + queued["model"]
                        )
        except Exception as exc:
            _ui(QMessageBox, "warning", self, tr_text("Importvorschau"), str(exc))
            return
        _ui(
            self.statusBar(),
            "showMessage",
            tr_text("Serverbestand wird für die Importvorschau gelesen …"),
        )

        def read(_):
            with TeddyCloud(self.settings) as client:
                return client.models()

        def show(models):
            lines = []
            for book, names in naming:
                matches = [m for m in models if m.get("model") == names["model"]]
                action = (
                    tr_text("Neu")
                    if not matches
                    else tr_text(
                        "Vorhanden – Audio und Daten werden geprüft, Abweichungen aktualisiert"
                    )
                )
                if len(matches) > 1:
                    _ui(
                        QMessageBox,
                        "warning",
                        self,
                        tr_text("Import"),
                        tr_text("Doppelte Serverkennung: ") + names["model"],
                    )
                    return
                book.status = tr_text("Vorhanden") if matches else tr_text("Neu")
                lines += [
                    _f("{p0} · {p1}", p0=book.title, p1=tr_text(action)),
                    _f("  Modell: {p0}", p0=names["model"]),
                    _f(
                        "  Audio: {p0}/{p1}.taf",
                        p0=names["audio_folder"],
                        p1=names["filename"],
                    ),
                    _f(
                        "  Cover: {p0}",
                        p0=book.cover or tr_text("Fehlt – Import erfolgt trotzdem"),
                    ),
                    "",
                ]
            dialog = QDialog(self)
            _ui(dialog, "setWindowTitle", tr_text("Importvorschau"))
            dialog.resize(900, 600)
            layout = QVBoxLayout(dialog)
            text = _new(QPlainTextEdit, tr_text("\n").join(lines))
            text.setReadOnly(True)
            layout.addWidget(text)
            layout.addWidget(
                _new(
                    QLabel,
                    tr_text(
                        "Nur diese Modelle werden bearbeitet. Keine RFID-Zuordnung. Erfolg erst nach vollständiger Prüfung."
                    ),
                )
            )
            buttons = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Ok
                | QDialogButtonBox.StandardButton.Cancel
            )
            _ui(
                buttons.button(QDialogButtonBox.StandardButton.Ok),
                "setText",
                tr_text("Importaufträge erstellen"),
            )
            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
            layout.addWidget(buttons)
            if dialog.exec():
                for book, _ in naming:
                    self.store.add_job(
                        tr_text("Import"),
                        {
                            "book": book.to_dict(),
                            "settings": asdict(self.settings),
                            "profile": asdict(self.settings.profile()),
                        },
                    )
                self.jobs_added()
            _ui(
                self.statusBar(), "showMessage", tr_text("Importvorschau abgeschlossen")
            )

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
            if entry["kind"] in {tr_text("Import"), tr_text("Prüfung")}:
                identifier = json.loads(entry["payload"])["book"]["id"]
                latest.setdefault(identifier, entry)
        for book in self.collection.books:
            entry = latest.get(book.id)
            if entry:
                status = (
                    tr_text("Bestätigt")
                    if entry["status"] == tr_text("Bestätigt")
                    else (
                        tr_text("Prüfen")
                        if entry["status"] == tr_text("Fehlgeschlagen")
                        else entry["status"]
                    )
                )
                if book.status != status:
                    book.status = status
                    self.store.save_book(book)
        self.collection.refresh_cards()

    def _background_busy(self):
        return any(
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
        )

    def _finish_shutdown(self):
        """Keep Qt responsive while workers reap their converters and clean PCM."""
        if self.worker.running or self._background_busy():
            return
        self._shutdown_timer.stop()
        self.close()

    def closeEvent(self, event):
        if self._close_prompt_open:
            event.ignore()
            return
        if self.worker.running and not self._closing_when_idle:
            self._close_prompt_open = True
            try:
                dialog = QMessageBox(self)
                _ui(dialog, "setWindowTitle", tr_text("Laufende Aufträge abbrechen?"))
                dialog.setIcon(QMessageBox.Icon.Warning)
                _ui(dialog, "setText", tr_text("Es werden noch Aufträge verarbeitet."))
                dialog.setInformativeText(
                    tr_text(
                        "Beim Beenden werden die Warteschlange und laufende Konverter abgebrochen. "
                        "Temporäre Audiodaten werden bereinigt. Bereits gespeicherte Dateien "
                        "und TeddyCloud-Inhalte bleiben erhalten."
                    )
                )
                abort = _ui(
                    dialog,
                    "addButton",
                    tr_text("Aufträge abbrechen und beenden"),
                    QMessageBox.ButtonRole.DestructiveRole,
                )
                keep = _ui(
                    dialog,
                    "addButton",
                    tr_text("Programm weiter ausführen"),
                    QMessageBox.ButtonRole.RejectRole,
                )
                dialog.setDefaultButton(keep)
                dialog.setEscapeButton(keep)
                dialog.exec()
                confirmed = dialog.clickedButton() == abort
            finally:
                self._close_prompt_open = False
            if not confirmed:
                event.ignore()
                return
            self._closing_when_idle = True
            self.centralWidget().setEnabled(False)
            self.worker.cancel_all()
        if self._closing_when_idle and (self.worker.running or self._background_busy()):
            _ui(
                self.statusBar(),
                "showMessage",
                tr_text(
                    "Aufträge werden abgebrochen und Arbeitsdateien bereinigt. Die Anwendung schließt anschließend automatisch."
                ),
            )
            self._shutdown_timer.start()
            event.ignore()
            return
        if self._background_busy():
            _ui(
                self.statusBar(),
                "showMessage",
                tr_text("Bitte den laufenden Vorgang abwarten."),
            )
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
    app.setApplicationName("TeddyShelf")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("TeddyShelf")
    try:
        with exclusive_lock(data_directory() / "application.lock"):
            window = MainWindow()
            window.show()
            return app.exec()
    except Exception as exc:
        _ui(QMessageBox, "critical", None, "TeddyShelf", str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
