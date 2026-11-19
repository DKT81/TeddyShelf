"""Settings pages and editable naming profiles with a real rendering preview."""

from dataclasses import asdict
from pathlib import Path
import json

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QListWidget,
    QStackedWidget,
    QLineEdit,
    QComboBox,
    QCheckBox,
    QSpinBox,
    QPlainTextEdit,
    QLabel,
    QFileDialog,
    QMessageBox,
    QScrollArea,
    QInputDialog,
)

from .domain import Book, NamingProfile
from .storage import Settings
from .client import TeddyCloud
from .ui_common import background, button, heading, path_control, row


class SettingsPage(QWidget):
    saved = Signal()

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.controls = {}
        self.preview_book = Book(
            "demo",
            "Sternenfreunde/001-Das leuchtende Tal",
            "Das leuchtende Tal",
            "Sternenfreunde",
            1,
        )
        outer = QVBoxLayout(self)
        outer.addWidget(
            heading("Einstellungen", "Verbindung, Arbeitsordner und Standardverhalten")
        )
        body = QHBoxLayout()
        outer.addLayout(body)
        self.navigation = QListWidget()
        self.navigation.setMaximumWidth(210)
        self.stack = QStackedWidget()
        body.addWidget(self.navigation)
        body.addWidget(self.stack, 1)
        self.pages = {}
        for name in [
            "Allgemein",
            "TeddyCloud",
            "Namen & Metadaten",
            "Cover",
            "Audio & Schnitt",
            "Speicherorte",
            "Importregeln",
            "Protokolle",
        ]:
            self.navigation.addItem(name)
            content = QWidget()
            form = QFormLayout(content)
            form.setSpacing(14)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(content)
            self.stack.addWidget(scroll)
            self.pages[name] = form
        self.navigation.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.navigation.setCurrentRow(0)
        self.add_combo("Allgemein", "theme", "Design", ["System", "Hell", "Dunkel"])
        self.add_combo("Allgemein", "language", "Sprache", ["Deutsch"])
        self.add_spin(
            "Allgemein",
            "parallel_jobs",
            "Gleichzeitige Aufträge / Konvertierungen",
            1,
            16,
        )
        import os

        cores = os.cpu_count() or 1
        parallel_help = QLabel(
            f"{cores} logische CPU-Kerne erkannt. Vorsichtiger Startwert: {max(1,min(4,cores//2))}.\n1 = nacheinander. Mehr Aufträge benötigen mehr CPU, RAM und Datenträgerleistung.\nÜbertragungen und Modelländerungen laufen je TeddyCloud-Ziel nacheinander.\nÄnderungen gelten für die nächsten freien Plätze, auch bei bereits wartenden Aufträgen."
        )
        parallel_help.setWordWrap(True)
        self.pages["Allgemein"].addRow(parallel_help)
        self.add_check(
            "Allgemein", "reopen_collection", "Letzte Sammlung beim Start öffnen"
        )
        self.add_check(
            "Allgemein", "show_unfinished", "Unerledigte Aufträge beim Start anzeigen"
        )
        self.pages["Allgemein"].addRow(
            button("Namensprofile bearbeiten", lambda: self.navigation.setCurrentRow(2))
        )
        self.add_text("TeddyCloud", "url", "Adresse")
        self.add_check("TeddyCloud", "verify_tls", "TLS-Zertifikat prüfen")
        self.add_text(
            "TeddyCloud", "ca_file", "Eigenes CA-Zertifikat (optional)", file=True
        )
        self.connection_status = QLabel("Noch nicht geprüft")
        self.pages["TeddyCloud"].addRow(
            row(
                button("Verbindung testen", self.test_connection),
                self.connection_status,
            )
        )
        self.pages["TeddyCloud"].addRow(
            QLabel(
                "Bei selbstsigniertem LAN-Zertifikat eigene CA wählen oder die Prüfung gezielt ausschalten."
            )
        )
        self.build_profiles()
        self.cover_order = QListWidget()
        self.cover_order.addItems(settings.cover_sources)
        self.pages["Cover"].addRow("Quellen in Prioritätsreihenfolge", self.cover_order)
        self.pages["Cover"].addRow(
            row(
                button("Nach oben", lambda: self.move_source(-1)),
                button("Nach unten", lambda: self.move_source(1)),
            )
        )
        self.add_check(
            "Cover",
            "preserve_manual_cover",
            "Manuell bestätigte Cover beim Neueinlesen erhalten",
        )
        self.add_combo(
            "Cover",
            "cover_missing",
            "Wenn ein Cover fehlt",
            ["Leer lassen", "Neutrales Symbol"],
        )
        self.add_combo("Cover", "cover_format", "Bildformat", ["JPG", "PNG"])
        self.add_spin("Cover", "cover_size", "Maximale Kantenlänge", 64, 4000)
        self.add_check("Cover", "cover_upscale", "Kleine Bilder vergrößern")
        self.add_check(
            "Cover", "embed_cover", "Cover in exportierte Audiodateien einbetten"
        )
        self.pages["Cover"].addRow(
            QLabel(
                "Seitenverhältnis bleibt erhalten. Online-Cover benötigen immer eine ausdrückliche Zuordnung."
            )
        )
        from .media import AUDIO_MODES

        self.add_combo(
            "Audio & Schnitt", "audio_mode", "Standard-Verarbeitung", AUDIO_MODES
        )
        self.pages["Audio & Schnitt"].addRow(
            QLabel(
                "TAF als Playlist: jede Quelldatei vollständig als eigenes Kapitel, ohne Zeitschnitt.\n"
                "Audio wird für TAF konvertiert; Originaldateien bleiben unverändert."
            )
        )
        self.add_combo(
            "Audio & Schnitt",
            "audio_format",
            "Standardformat",
            ["mp3", "opus", "flac", "wav"],
        )
        self.add_spin(
            "Audio & Schnitt", "audio_bitrate", "Export-Bitrate (kbit/s)", 32, 320
        )
        self.add_spin("Audio & Schnitt", "taf_bitrate", "TAF-Bitrate (kbit/s)", 32, 128)
        self.add_spin(
            "Audio & Schnitt",
            "split_seconds",
            "Standard-Abschnittslänge (Sekunden)",
            1,
            86400,
        )
        self.add_spin(
            "Audio & Schnitt",
            "timeout",
            "Zeitlimit pro Audioschritt (Sekunden)",
            30,
            86400,
        )
        self.add_text(
            "Audio & Schnitt", "ffmpeg", "FFmpeg (leer = mitgeliefert)", file=True
        )
        self.add_text(
            "Audio & Schnitt", "ffprobe", "FFprobe (leer = mitgeliefert)", file=True
        )
        self.add_text("Speicherorte", "source", "Standard-Quellordner", directory=True)
        self.add_text("Speicherorte", "export", "Arbeits-/Exportordner", directory=True)
        self.add_text(
            "Speicherorte",
            "download_destination",
            "TeddyCloud-Downloads",
            directory=True,
        )
        self.add_combo(
            "Importregeln",
            "existing",
            "Vorhandene Modelle",
            ["Prüfen und aktualisieren", "Bei vorhandenem Modell stoppen"],
        )
        self.pages["Importregeln"].addRow(
            QLabel(
                "Immer aktiv: vollständige Audio- und Serverprüfung, ASCII-Pfade, keine RFID-Zuordnung.\nKeine automatischen Importe beim Programmstart."
            )
        )
        from .storage import data_directory

        self.pages["Protokolle"].addRow("Datenordner", QLabel(str(data_directory())))
        self.pages["Protokolle"].addRow(
            button("Temporäre Audiodaten bereinigen", self.clean_audio_work)
        )
        self.pages["Protokolle"].addRow(
            QLabel(
                "PCM-Arbeitsaudio wird nach jedem Versuch und beim Start bereinigt.\n"
                "Laufende Aufträge bleiben geschützt; bei Wiederaufnahme wird Audio neu dekodiert."
            )
        )
        self.pages["Protokolle"].addRow(
            QLabel(
                "Aufträge enthalten einzelne Protokolle und einen exportierbaren Gesamtverlauf.\nFehler werden dauerhaft aufbewahrt; keine automatische Löschung."
            )
        )
        outer.addWidget(
            QLabel(
                "Inhaltsvorgaben gelten für neue Aufträge. Parallelität steuert sofort die nächsten freien Plätze."
            )
        )
        outer.addWidget(
            row(
                button("Profil exportieren", self.export_settings),
                button("Profil importieren", self.import_settings),
                button("Änderungen verwerfen", self.reload),
                button("Einstellungen speichern", self.save, True),
            )
        )

    def clean_audio_work(self):
        from .storage import data_directory
        from .workspace import clean_inactive

        count, size, errors = clean_inactive(data_directory() / "work")
        message = f"{count} temporäre PCM-Dateien entfernt · {size / 1048576:.1f} MiB freigegeben."
        if errors:
            message += "\nNicht entfernt:\n" + "\n".join(errors[:10])
        QMessageBox.information(self, "Arbeitsdateien bereinigt", message)

    def add_text(self, page, key, label, file=False, directory=False):
        control = QLineEdit(str(getattr(self.settings, key)))
        if key == "url":
            control.setPlaceholderText(
                "http://teddycloud.example – eigene Serveradresse eintragen"
            )
        self.controls[key] = control
        self.pages[page].addRow(
            label,
            (
                path_control(control, self, directory=directory)
                if file or directory
                else control
            ),
        )

    def add_check(self, page, key, label):
        control = QCheckBox(label)
        control.setChecked(getattr(self.settings, key))
        self.controls[key] = control
        self.pages[page].addRow(control)

    def add_combo(self, page, key, label, values):
        control = QComboBox()
        control.addItems(values)
        control.setCurrentText(str(getattr(self.settings, key)))
        self.controls[key] = control
        self.pages[page].addRow(label, control)

    def add_spin(self, page, key, label, minimum, maximum):
        control = QSpinBox()
        control.setRange(minimum, maximum)
        control.setValue(getattr(self.settings, key))
        self.controls[key] = control
        self.pages[page].addRow(label, control)

    def move_source(self, delta):
        index = self.cover_order.currentRow()
        target = index + delta
        if 0 <= target < self.cover_order.count():
            self.cover_order.insertItem(target, self.cover_order.takeItem(index))
            self.cover_order.setCurrentRow(target)

    def build_profiles(self):
        form = self.pages["Namen & Metadaten"]
        self.profiles = [asdict(NamingProfile(**x)) for x in self.settings.profiles]
        self.profile_choice = QComboBox()
        self.profile_choice.addItems([p["name"] for p in self.profiles])
        self.profile_choice.setCurrentIndex(self.settings.default_profile)
        self.profile_index = self.settings.default_profile
        form.addRow("Standardprofil", self.profile_choice)
        form.addRow(
            row(
                button("Neues Profil", self.new_profile),
                button("Profil kopieren", self.copy_profile),
                button("Profil löschen", self.delete_profile),
            )
        )
        self.profile_controls = {}
        self.source_controls = {}
        for key, label, options in [
            (
                "title_source",
                "Titelquelle",
                [
                    "Erkannter Titel",
                    "Album",
                    "Audiotag Titel",
                    "Ordnername",
                    "Dateiname",
                ],
            ),
            (
                "series_source",
                "Serienquelle",
                [
                    "Erkannte Serie",
                    "Ordner oder Album",
                    "Album",
                    "Übergeordneter Ordner",
                    "Keine Serie",
                ],
            ),
            (
                "episode_source",
                "Folgenquelle",
                ["Erkannte Folge", "Nummer aus Dateiname"],
            ),
        ]:
            control = QComboBox()
            control.addItems(options)
            control.setCurrentText(self.profiles[self.profile_index][key])
            control.currentTextChanged.connect(self.preview)
            self.source_controls[key] = control
            form.addRow(label, control)
        labels = {
            "name": "Profilname",
            "model_numbered": "Modellkennung mit Folge",
            "model_unnumbered": "Modellkennung ohne Folge",
            "title": "Sichtbarer Titel",
            "series": "Serie",
            "episodes": "Episodentext",
            "filename": "Audio-Dateiname",
            "audio_folder": "Audio-Zielordner",
            "image_folder": "Cover-Zielordner",
            "split_filename": "Teil-Dateiname",
            "language": "Sprache",
            "category": "Kategorie",
        }
        for key, label in labels.items():
            edit = QLineEdit(self.profiles[self.profile_index][key])
            edit.textChanged.connect(self.preview)
            self.profile_controls[key] = edit
            form.addRow(label, edit)
        form.addRow(
            QLabel(
                "Variablen: {serie}, {titel}, {folge:03}, {serie_slug}, {titel_slug},\n{serie_ordner}, {titel_ascii}, {modell}, {teil:03}, {sprache}, {kategorie}\nFolge, Teil und Track sind getrennte Angaben."
            )
        )
        self.variable = QComboBox()
        self.variable.addItems(
            [
                "{serie}",
                "{titel}",
                "{folge:03}",
                "{teil:03}",
                "{track:02}",
                "{album}",
                "{interpret}",
                "{albuminterpret}",
                "{genre}",
                "{datum}",
                "{ordner}",
                "{dateiname}",
            ]
        )
        self.template_target = QComboBox()
        self.template_target.addItems(list(labels.values()))
        form.addRow(
            "Platzhalter einfügen",
            row(
                self.variable,
                self.template_target,
                button(
                    "Einfügen",
                    lambda: self.profile_controls[
                        list(labels)[self.template_target.currentIndex()]
                    ].insert(self.variable.currentText()),
                ),
            ),
        )
        self.preview_cover = QLabel()
        form.addRow("Ausgewähltes Hörspiel", self.preview_cover)
        self.preview_label = QPlainTextEdit()
        self.preview_label.setReadOnly(True)
        self.preview_label.setMinimumHeight(220)
        form.addRow("Live-Vorschau (Beispiel)", self.preview_label)
        self.profile_choice.currentIndexChanged.connect(self.switch_profile)
        self.preview()

    def current_profile(self):
        return {
            **{key: edit.text() for key, edit in self.profile_controls.items()},
            **{
                key: control.currentText()
                for key, control in self.source_controls.items()
            },
        }

    def set_preview_book(self, book):
        self.preview_book = book
        from .ui_common import cover_pixmap

        self.preview_cover.setPixmap(cover_pixmap(book.cover, 120))
        self.preview()

    def switch_profile(self, index):
        if index < 0:
            return
        self.profiles[self.profile_index] = self.current_profile()
        self.profile_index = index
        for key, edit in self.profile_controls.items():
            edit.setText(self.profiles[index][key])
        for key, control in self.source_controls.items():
            control.setCurrentText(self.profiles[index][key])
        self.preview()

    def new_profile(self):
        self.profiles[self.profile_index] = self.current_profile()
        self.profiles.append(asdict(NamingProfile(name="Neues Profil")))
        self.profile_choice.addItem("Neues Profil")
        self.profile_choice.setCurrentIndex(len(self.profiles) - 1)

    def copy_profile(self):
        copied = self.current_profile()
        copied["name"] += " – Kopie"
        self.profiles.append(copied)
        self.profile_choice.addItem(copied["name"])
        self.profile_choice.setCurrentIndex(len(self.profiles) - 1)

    def delete_profile(self):
        if len(self.profiles) <= 1:
            return
        index = self.profile_choice.currentIndex()
        self.profile_choice.blockSignals(True)
        self.profiles.pop(index)
        self.profile_choice.removeItem(index)
        self.profile_index = min(index, len(self.profiles) - 1)
        self.profile_choice.setCurrentIndex(self.profile_index)
        self.profile_choice.blockSignals(False)
        for key, edit in self.profile_controls.items():
            edit.setText(self.profiles[self.profile_index][key])
        for key, control in self.source_controls.items():
            control.setCurrentText(self.profiles[self.profile_index][key])

    def preview(self):
        if not hasattr(self, "preview_label"):
            return
        try:
            value = NamingProfile(**self.current_profile()).render(self.preview_book)
            self.preview_label.setPlainText(
                json.dumps(value, ensure_ascii=False, indent=2)
            )
        except Exception as exc:
            self.preview_label.setPlainText(str(exc))

    def collect(self):
        value = asdict(self.settings)
        for key, control in self.controls.items():
            value[key] = (
                control.isChecked()
                if isinstance(control, QCheckBox)
                else (
                    control.value()
                    if isinstance(control, QSpinBox)
                    else (
                        control.currentText()
                        if isinstance(control, QComboBox)
                        else control.text().strip()
                    )
                )
            )
        self.profiles[self.profile_index] = self.current_profile()
        value["profiles"] = self.profiles
        value["default_profile"] = self.profile_choice.currentIndex()
        value["cover_sources"] = [
            self.cover_order.item(i).text() for i in range(self.cover_order.count())
        ]
        candidate = Settings(**value)
        candidate.profile().render(Book("test", "", "Titel", "Serie", 1))
        candidate.profile().render(Book("test", "", "Titel", "Serie"))
        source, export = (
            Path(candidate.source).resolve(),
            Path(candidate.export).resolve(),
        )
        if source == export or source in export.parents:
            raise ValueError("Der Exportordner muss außerhalb des Quellordners liegen.")
        return candidate

    def save(self):
        try:
            candidate = self.collect()
            candidate.save()
            self.settings.__dict__.update(candidate.__dict__)
            for index, profile in enumerate(candidate.profiles):
                self.profile_choice.setItemText(index, profile["name"])
            self.saved.emit()
            QMessageBox.information(
                self,
                "Einstellungen",
                "Gespeichert. Neue Aufträge verwenden diese Vorgaben.",
            )
        except Exception as exc:
            QMessageBox.warning(self, "Einstellungen", str(exc))

    def reload(self):
        for key, control in self.controls.items():
            value = getattr(self.settings, key)
            if isinstance(control, QCheckBox):
                control.setChecked(value)
            elif isinstance(control, QSpinBox):
                control.setValue(value)
            elif isinstance(control, QComboBox):
                control.setCurrentText(value)
            else:
                control.setText(str(value))
        self.profiles = [asdict(NamingProfile(**p)) for p in self.settings.profiles]
        self.profile_choice.blockSignals(True)
        self.profile_choice.clear()
        self.profile_choice.addItems([p["name"] for p in self.profiles])
        self.profile_index = self.settings.default_profile
        self.profile_choice.setCurrentIndex(self.profile_index)
        self.profile_choice.blockSignals(False)
        for key, edit in self.profile_controls.items():
            edit.setText(self.profiles[self.profile_index][key])
        for key, control in self.source_controls.items():
            control.setCurrentText(self.profiles[self.profile_index][key])
        self.cover_order.clear()
        self.cover_order.addItems(self.settings.cover_sources)

    def export_settings(self):
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Einstellungen exportieren",
            "TeddyShelf-Profil.json",
            "JSON (*.json)",
        )
        if path:
            try:
                self.collect().save(path)
            except Exception as exc:
                QMessageBox.warning(self, "Profil", str(exc))

    def import_settings(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Einstellungen importieren", "", "JSON (*.json)"
        )
        if path:
            try:
                candidate = Settings.load(path)
                candidate.profile().render(Book("test", "", "Titel", "Serie", 1))
                self.settings.__dict__.update(candidate.__dict__)
                self.reload()
            except Exception as exc:
                QMessageBox.warning(self, "Profil", str(exc))

    def test_connection(self):
        try:
            candidate = self.collect()
        except Exception as exc:
            QMessageBox.warning(self, "Verbindung", str(exc))
            return
        self.connection_status.setText("Wird geprüft …")

        def check(_):
            with TeddyCloud(candidate) as client:
                return len(client.models())

        background(
            self,
            check,
            lambda count: self.connection_status.setText(
                f"Erreichbar · {count} Custom-Tonies"
            ),
        )
