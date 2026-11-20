"""Server inventory, explicit local downloads, and concrete Tonie/tag assignment."""

from .i18n import text as tr_text, format_text as _f
from .i18n_widgets import call as _ui, create as _new

from dataclasses import asdict
from pathlib import Path
import hashlib, json
import threading
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QLabel,
    QLineEdit,
    QComboBox,
    QTreeWidget,
    QTreeWidgetItem,
    QCheckBox,
    QMessageBox,
    QPlainTextEdit,
    QFormLayout,
    QInputDialog,
    QSplitter,
)
from .client import TeddyCloud
from .cloud_library import inventory, entry_book
from .domain import Book
from .storage import data_directory, atomic_json
from .tags import read_tag, normalize_uid, FIELDS
from .ui_common import background, button, row, heading, path_control


class CloudPage(QWidget):
    assign_requested = Signal(object)
    inventory_changed = Signal(object)

    def __init__(self, store, settings, jobs_added):
        super().__init__()
        self.store = store
        self.settings = settings
        self.jobs_added = jobs_added
        self.snapshot = None
        self.busy = False
        self.cancel_read = threading.Event()
        layout = QVBoxLayout(self)
        layout.addWidget(
            heading(
                tr_text("TeddyCloud-Bestand"),
                tr_text(
                    "Vorhandene Tonies einlesen und Audio lokal sichern – keine automatischen Tagänderungen"
                ),
            )
        )
        self.overlay = _new(QLineEdit, settings.cloud_overlay)
        _ui(self.overlay, "setPlaceholderText", tr_text("Overlay / Box (optional)"))
        self.overlay.setMaxLength(15)
        self.search = _new(QLineEdit)
        _ui(
            self.search,
            "setPlaceholderText",
            tr_text("Titel, Serie oder Modell suchen"),
        )
        self.kind = _new(QComboBox)
        _ui(
            self.kind,
            "addItems",
            [
                tr_text("Alle Typen"),
                tr_text("Custom"),
                tr_text("Katalog"),
                tr_text("Unzugeordnet"),
            ],
        )
        self.available = _new(QComboBox)
        _ui(
            self.available,
            "addItems",
            [
                tr_text("Alle Einträge"),
                tr_text("Audio vorhanden"),
                tr_text("Audio fehlt / ungeklärt"),
            ],
        )
        self.read_button = button(tr_text("TeddyCloud einlesen"), self.read, True)
        layout.addWidget(
            row(
                self.read_button,
                self.overlay,
                self.search,
                self.kind,
                self.available,
                button(tr_text("Einlesen abbrechen"), self.cancel_read.set),
            )
        )
        self.tree = QTreeWidget()
        self.tree.setColumnCount(5)
        _ui(
            self.tree,
            "setHeaderLabels",
            [
                tr_text("Titel"),
                tr_text("Serie / Folge"),
                tr_text("Typ"),
                tr_text("Status"),
                tr_text("Serverdatei"),
            ],
        )
        self.tree.setColumnWidth(0, 300)
        layout.addWidget(self.tree, 1)
        self.status = _new(
            QLabel,
            tr_text(
                "Noch nicht eingelesen. Es werden nur bereits auf TeddyCloud vorhandene Dateien angeboten."
            ),
        )
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.destination = _new(QLineEdit, settings.download_destination)
        self.format = _new(QComboBox)
        _ui(
            self.format,
            "addItems",
            [
                tr_text("OGG/Opus"),
                tr_text("Original-TAF"),
                tr_text("MP3"),
                tr_text("FLAC"),
                tr_text("WAV"),
            ],
        )
        self.keep = _new(QCheckBox, tr_text("Original-TAF zusätzlich behalten"))
        self.keep.setChecked(True)
        self.local = _new(QCheckBox, tr_text("In lokale Sammlung übernehmen"))
        self.local.setChecked(True)
        self.split = _new(QCheckBox, tr_text("MP3/FLAC/WAV nach Kapiteln aufteilen"))
        form = QFormLayout()
        _ui(
            form,
            "addRow",
            tr_text("Lokaler Zielordner"),
            path_control(self.destination, self),
        )
        _ui(form, "addRow", tr_text("Ausgabe"), row(self.format, self.keep, self.local))
        _ui(form, "addRow", self.split)
        layout.addLayout(form)
        hint = _new(
            QLabel,
            tr_text(
                "TAF/OGG bewahren den vorhandenen Tonie-Inhalt. Bei Sammlungsübernahme aus TAF wird zusätzlich eine abspielbare OGG-Datei erzeugt. MP3 wird neu kodiert. Kein Abruf aus der offiziellen Toniecloud."
            ),
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addWidget(
            row(
                button(
                    tr_text("Sichtbare Audios auswählen"),
                    lambda: self.select_visible(True),
                ),
                button(tr_text("Auswahl aufheben"), lambda: self.select_visible(False)),
                button(
                    tr_text("Aus TeddyCloud lokal übernehmen …"), self.create_jobs, True
                ),
                button(tr_text("Tonie / Tag zuordnen …"), self.assign),
            )
        )
        for signal in (
            self.search.textChanged,
            self.kind.currentTextChanged,
            self.available.currentTextChanged,
        ):
            signal.connect(self.refresh)
        self.format.currentIndexChanged.connect(
            lambda: self.split.setEnabled(self.format.currentIndex() > 1)
        )
        self.split.setEnabled(False)
        try:
            cached = json.loads(self.cache_path().read_text(encoding="utf-8"))
            if (
                cached.get("url") == settings.url.rstrip("/")
                and cached.get("overlay") == self.overlay.text()
            ):
                self.snapshot = cached
                self.snapshot["cached"] = True
                self.refresh()
        except (OSError, ValueError, KeyError):
            pass

    def cache_path(self):
        key = hashlib.sha256(
            (self.settings.url.rstrip("/") + self.overlay.text()).encode()
        ).hexdigest()[:24]
        return data_directory() / ("cloud-inventory-" + key + ".json")

    def read(self):
        if self.busy:
            return
        self.busy = True
        self.cancel_read.clear()
        self.read_button.setEnabled(False)
        _ui(self.status, "setText", tr_text("Bestand wird gelesen …"))
        settings = type(self.settings)(**asdict(self.settings))
        overlay = self.overlay.text().strip()

        def work(progress):
            def report(message):
                if self.cancel_read.is_set():
                    raise ValueError(
                        tr_text(
                            "Einlesen abgebrochen; letzter vollständiger Bestand bleibt erhalten"
                        )
                    )
                progress(message)

            try:
                with TeddyCloud(settings) as client:
                    return inventory(client, overlay, report)
            except Exception as exc:
                return {"error": str(exc)}

        def done(result):
            self.busy = False
            self.read_button.setEnabled(True)
            if "error" in result:
                if self.snapshot:
                    self.snapshot["cached"] = True
                _ui(
                    self.status,
                    "setText",
                    tr_text("Bestand nicht aktualisiert: ") + result["error"],
                )
                return
            self.snapshot = result
            key = hashlib.sha256(
                (result["url"] + result["overlay"]).encode()
            ).hexdigest()[:24]
            atomic_json(data_directory() / ("cloud-inventory-" + key + ".json"), result)
            self.settings.cloud_overlay = overlay
            self.settings.save()
            self.refresh()
            self.inventory_changed.emit(result)

        task = background(self, work, done)
        task.signals.progress.connect(
            lambda message: _ui(self.status, "setText", tr_text(message))
        )

    def refresh(self, *args):
        selected = {
            self.tree.topLevelItem(n).data(0, Qt.ItemDataRole.UserRole)["id"]
            for n in range(self.tree.topLevelItemCount())
            if self.tree.topLevelItem(n).checkState(0) == Qt.CheckState.Checked
        }
        self.tree.clear()
        if not self.snapshot:
            return
        query = self.search.text().casefold()
        for entry in self.snapshot["entries"]:
            if (
                query
                not in (
                    entry["title"]
                    + " "
                    + entry["series"]
                    + " "
                    + entry.get("model", {}).get("model", "")
                ).casefold()
            ):
                continue
            if self.kind.currentIndex() and entry["kind"] != self.kind.currentText():
                continue
            if self.available.currentIndex() == 1 and not entry["available"]:
                continue
            if self.available.currentIndex() == 2 and entry["available"]:
                continue
            model = entry.get("model", {})
            episode = str(model.get("no", ""))
            files = tr_text("; ").join(x["path"] for x in entry["locations"])
            item = _new(
                QTreeWidgetItem,
                [
                    entry["title"],
                    entry["series"] + (" · " + episode if episode else ""),
                    tr_text(entry["kind"]),
                    tr_text(entry["status"]),
                    files,
                ],
            )
            item.setData(0, Qt.ItemDataRole.UserRole, entry)
            _ui(
                item,
                "setToolTip",
                0,
                json.dumps(entry.get("aliases", []), ensure_ascii=False, indent=2),
            )
            if entry["available"]:
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(
                    0,
                    (
                        Qt.CheckState.Checked
                        if entry["id"] in selected
                        else Qt.CheckState.Unchecked
                    ),
                )
            else:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
            self.tree.addTopLevelItem(item)
        warning = (
            tr_text(" · Bestand unvollständig: ")
            + tr_text("; ").join(self.snapshot["warnings"])
            if self.snapshot["warnings"]
            else ""
        )
        _ui(
            self.status,
            "setText",
            (
                tr_text("Gespeicherter Stand – neu einlesen empfohlen · ")
                if self.snapshot.get("cached")
                else ""
            )
            + _f(
                "{p0} Einträge · {p1} · nur vorhandene Dateien",
                p0=self.tree.topLevelItemCount(),
                p1=self.snapshot["url"],
            )
            + warning,
        )

    def select_visible(self, checked=True):
        for n in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(n)
            if item.data(0, Qt.ItemDataRole.UserRole)["available"]:
                item.setCheckState(
                    0, Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
                )

    def selected_entries(self):
        return [
            self.tree.topLevelItem(n).data(0, Qt.ItemDataRole.UserRole)
            for n in range(self.tree.topLevelItemCount())
            if self.tree.topLevelItem(n).checkState(0) == Qt.CheckState.Checked
        ]

    def validate_snapshot(self):
        if (
            not self.snapshot
            or self.snapshot["url"] != self.settings.url.rstrip("/")
            or self.snapshot["overlay"] != self.overlay.text().strip()
        ):
            raise ValueError(
                tr_text(
                    "Bestand für die aktuelle Adresse / das aktuelle Overlay zuerst neu einlesen"
                )
            )

    def create_jobs(self):
        try:
            self.validate_snapshot()
            entries = self.selected_entries()
            if not entries:
                raise ValueError(
                    tr_text("Audiodateien zuerst über die Kästchen auswählen")
                )
            destination = Path(self.destination.text()).expanduser().resolve()
            formats = ["ogg", "taf", "mp3", "flac", "wav"]
            fmt = formats[self.format.currentIndex()]
            preview = tr_text("\n").join(e["title"] for e in entries) + _f(
                "\n\nZiel: {p0}\nFormat: {p1}\nTeddyCloud bleibt unverändert.",
                p0=destination,
                p1=self.format.currentText(),
            )
            if (
                _ui(QMessageBox, "question", self, tr_text("Lokale Übernahme"), preview)
                != QMessageBox.StandardButton.Yes
            ):
                return
            open_ids = {
                json.loads(j["payload"]).get("download", {}).get("entry", {}).get("id")
                for j in self.store.jobs()
                if j["status"] in {"Wartet", "Aktiv", "Pausiert", "Unterbrochen"}
            }
            added = 0
            for entry in entries:
                if entry["id"] in open_ids:
                    continue
                book = entry_book(entry, self.snapshot["url"], self.snapshot["overlay"])
                self.store.add_job(
                    tr_text("Aus TeddyCloud holen"),
                    {
                        "book": book.to_dict(),
                        "settings": asdict(self.settings),
                        "profile": asdict(self.settings.profile()),
                        "overlay": self.snapshot["overlay"],
                        "download": {
                            "entry": entry,
                            "destination": str(destination),
                            "format": fmt,
                            "keep_taf": self.keep.isChecked(),
                            "import_collection": self.local.isChecked(),
                            "split_chapters": self.split.isChecked()
                            and self.split.isEnabled(),
                        },
                    },
                )
                added += 1
            self.settings.download_destination = str(destination)
            self.settings.save()
            if added:
                self.jobs_added()
            else:
                _ui(
                    QMessageBox,
                    "information",
                    self,
                    tr_text("Downloads"),
                    tr_text("Für diese Auswahl bestehen bereits offene Aufträge."),
                )
        except Exception as exc:
            _ui(QMessageBox, "warning", self, tr_text("Lokale Übernahme"), str(exc))

    def assign(self):
        try:
            self.validate_snapshot()
            item = self.tree.currentItem()
            if not item:
                raise ValueError(tr_text("Einen vorhandenen Tonie auswählen"))
            self.assign_requested.emit(item.data(0, Qt.ItemDataRole.UserRole))
        except Exception as exc:
            _ui(QMessageBox, "warning", self, tr_text("Tagzuordnung"), str(exc))


class TagsPage(QWidget):
    def __init__(self, store, settings, cloud, jobs_added):
        super().__init__()
        self.store = store
        self.settings = settings
        self.cloud = cloud
        self.jobs_added = jobs_added
        self.loaded = None
        layout = QVBoxLayout(self)
        layout.addWidget(
            heading(
                tr_text("Tonies / Tags"),
                tr_text(
                    "Konkreten Tonie mit Modell und Audio verknüpfen – kein Beschreiben eines Chips"
                ),
            )
        )
        sections = QSplitter(Qt.Orientation.Vertical)
        sections.setChildrenCollapsible(False)
        sections.setHandleWidth(7)
        layout.addWidget(sections, 1)
        self.tag_tree = QTreeWidget()
        self.tag_tree.setColumnCount(4)
        _ui(
            self.tag_tree,
            "setHeaderLabels",
            [
                tr_text("Tonie / zugeordnetes Modell"),
                tr_text("RUID"),
                tr_text("Modellkennung"),
                tr_text("Audioquelle"),
            ],
        )
        self.tag_tree.setColumnWidth(0, 270)
        self.tag_tree.setColumnWidth(1, 170)
        self.tag_tree.setMinimumHeight(90)
        _ui(
            self.tag_tree,
            "setToolTip",
            tr_text(
                "Doppelklick lädt die bestehende Zuordnung dieses konkreten Tonies."
            ),
        )
        sections.addWidget(self.tag_tree)
        editor = QWidget()
        sections.addWidget(editor)
        layout = QVBoxLayout(editor)
        sections.setSizes([180, 500])
        _ui(
            sections.handle(1),
            "setToolTip",
            tr_text("Tonie-Liste und Zuordnungseditor in der Höhe anpassen"),
        )
        self.uid = _new(QLineEdit)
        _ui(
            self.uid, "setPlaceholderText", tr_text("16 Hex-Zeichen, wie in TeddyCloud")
        )
        self.uid_mode = _new(QComboBox)
        _ui(
            self.uid_mode,
            "addItems",
            [tr_text("TeddyCloud-RUID"), tr_text("NFC-UID: Byte-Reihenfolge umkehren")],
        )
        self.overlay = _new(QLineEdit, settings.cloud_overlay)
        self.overlay.setMaxLength(15)
        _ui(self.overlay, "setPlaceholderText", tr_text("Overlay / Box (optional)"))
        layout.addWidget(
            row(
                self.uid,
                self.uid_mode,
                self.overlay,
                button(tr_text("Tonie / Tag laden"), self.load, True),
                button(tr_text("Bekannten Tag auswählen"), self.choose),
            )
        )
        self.source = _new(QLineEdit)
        _ui(
            self.source,
            "setPlaceholderText",
            tr_text("lib://… oder content://… – leer = eigener Tonie-Inhalt"),
        )
        self.model = _new(QLineEdit)
        _ui(self.model, "setPlaceholderText", tr_text("Modellkennung"))
        self.live = _new(QCheckBox, tr_text("Live-Inhalt"))
        self.nocloud = _new(QCheckBox, tr_text("Kein Abruf aus Toniecloud"))
        self.hide = _new(QCheckBox, tr_text("In TeddyCloud ausblenden"))
        form = QFormLayout()
        _ui(form, "addRow", tr_text("Audiodatei / Quelle"), self.source)
        _ui(form, "addRow", tr_text("Zugeordnetes Modell"), self.model)
        _ui(form, "addRow", row(self.live, self.nocloud, self.hide))
        layout.addLayout(form)
        layout.addWidget(
            row(
                button(tr_text("Aus Bestand auswählen …"), self.choose_model),
                button(tr_text("Modell im Katalog suchen …"), self.search_model),
                button(tr_text("Modell-/Dateizuordnung lösen"), self.clear),
                button(tr_text("Änderung prüfen und zuordnen …"), self.apply, True),
            )
        )
        self.info = _new(
            QLabel,
            tr_text(
                "Zuerst UID/RUID und Overlay wählen und die bestehende Zuordnung laden. Es werden weder Chip-UID noch Authentifizierungsdaten verändert."
            ),
        )
        self.info.setWordWrap(True)
        layout.addWidget(self.info)
        self.preview = _new(QPlainTextEdit)
        self.preview.setReadOnly(True)
        layout.addWidget(self.preview, 1)
        self.pending_entry = None
        self.cloud.inventory_changed.connect(self.refresh_tags)
        self.tag_tree.itemDoubleClicked.connect(self.open_tag)
        if self.cloud.snapshot:
            self.refresh_tags(self.cloud.snapshot)

    def refresh_tags(self, snapshot):
        self.tag_tree.clear()
        for record in snapshot.get("tag_records", []):
            item = _new(
                QTreeWidgetItem,
                [record["title"], record["uid"], record["model"], record["source"]],
            )
            item.setData(0, Qt.ItemDataRole.UserRole, record)
            self.tag_tree.addTopLevelItem(item)

    def open_tag(self, item, column=0):
        record = item.data(0, Qt.ItemDataRole.UserRole)
        self.uid_mode.setCurrentIndex(0)
        _ui(self.uid, "setText", record["uid"])
        _ui(self.overlay, "setText", record["overlay"])
        self.load()

    def values(self):
        return {
            "source": self.source.text().strip(),
            "tonie_model": self.model.text().strip(),
            "live": self.live.isChecked(),
            "nocloud": self.nocloud.isChecked(),
            "hide": self.hide.isChecked(),
        }

    def choose(self):
        tags = (self.cloud.snapshot or {}).get("tags", [])
        if not tags:
            _ui(
                QMessageBox,
                "information",
                self,
                tr_text("Tags"),
                tr_text("Bestand zuerst einlesen oder UID/RUID direkt eingeben."),
            )
            return
        value, ok = _ui(
            QInputDialog,
            "getItem",
            self,
            tr_text("Tonie / Tag"),
            tr_text("TeddyCloud-RUID"),
            tags,
            editable=False,
        )
        if ok:
            self.uid_mode.setCurrentIndex(0)
            _ui(self.uid, "setText", value)
            self.load()

    def load(self):
        try:
            uid = normalize_uid(self.uid.text(), self.uid_mode.currentIndex() == 1)
        except ValueError as exc:
            _ui(QMessageBox, "warning", self, tr_text("UID"), str(exc))
            return
        overlay = self.overlay.text().strip()
        settings = type(self.settings)(**asdict(self.settings))

        def read(_):
            with TeddyCloud(settings) as client:
                return read_tag(client, uid, overlay)

        def done(value):
            self.loaded = {
                "uid": uid,
                "overlay": overlay,
                "url": settings.url.rstrip("/"),
                "before": value,
            }
            _ui(self.source, "setText", value["values"]["source"])
            _ui(self.model, "setText", value["values"]["tonie_model"])
            for key in ("live", "nocloud", "hide"):
                getattr(self, key).setChecked(value["values"][key])
            _ui(
                self.info,
                "setText",
                _f(
                    "RUID: {p0} · {p1} · Overlay: {p2} · ",
                    p0=uid,
                    p1=settings.url,
                    p2=overlay or tr_text("Standard"),
                )
                + (
                    tr_text("bestehender Tonie/Tag")
                    if value["exists"]
                    else tr_text("noch kein Eintrag; wird erst nach Vorschau angelegt")
                ),
            )
            _ui(
                self.preview,
                "setPlainText",
                json.dumps(value["values"], ensure_ascii=False, indent=2),
            )
            if self.pending_entry:
                entry = self.pending_entry
                self.pending_entry = None
                self.bind_entry(entry)

        background(self, read, done)

    def choose_model(self):
        try:
            self.cloud.validate_snapshot()
            if self.cloud.snapshot["overlay"] != self.overlay.text().strip():
                raise ValueError(
                    tr_text("Bestand für dasselbe Overlay / dieselbe Box einlesen")
                )
        except ValueError as exc:
            _ui(QMessageBox, "information", self, tr_text("Bestand"), str(exc))
            return
        entries = [
            e
            for e in self.cloud.snapshot["entries"]
            if e.get("model") or e["available"]
        ]
        if not entries:
            return
        choices = [
            e["title"] + " · " + e.get("model", {}).get("model", e["id"])
            for e in entries
        ]
        value, ok = _ui(
            QInputDialog,
            "getItem",
            self,
            tr_text("Modell und Audiodatei"),
            tr_text("Vorhandenen Inhalt wählen"),
            choices,
            editable=False,
        )
        if ok:
            self.bind_entry(entries[choices.index(value)])

    def search_model(self):
        query, ok = _ui(
            QInputDialog,
            "getText",
            self,
            tr_text("Modell suchen"),
            tr_text("Modellkennung, Titel oder Serie"),
        )
        if not ok or not query.strip():
            return
        settings = type(self.settings)(**asdict(self.settings))
        needle = query.strip().casefold()

        def work(_):
            with TeddyCloud(settings) as client:
                models = (
                    client.models()
                    + client.request(
                        tr_text("GET"), "/api/toniesJson", timeout=(5, 30)
                    ).json()
                )
                return [
                    m
                    for m in models
                    if needle
                    in " ".join(
                        str(m.get(k, ""))
                        for k in ("model", "series", "title", "episodes")
                    ).casefold()
                ][:100]

        def done(models):
            if settings.url != self.settings.url:
                return
            if not models:
                _ui(
                    QMessageBox,
                    "information",
                    self,
                    tr_text("Modellsuche"),
                    tr_text("Keine passende Modellkennung gefunden."),
                )
                return
            labels = [
                str(m.get("series", ""))
                + " · "
                + str(m.get("episodes") or m.get("title", ""))
                + " · "
                + str(m["model"])
                for m in models
            ]
            value, accepted = _ui(
                QInputDialog,
                "getItem",
                self,
                tr_text("Modellzuweisung"),
                tr_text("Modell wählen – Audiodatei bleibt unverändert"),
                labels,
                editable=False,
            )
            if accepted:
                _ui(self.model, "setText", models[labels.index(value)]["model"])

        background(self, work, done)

    def bind_entry(self, entry):
        location = next(
            (p for p in entry["locations"] if p["special"] == "library"),
            next(iter(entry["locations"]), None),
        )
        if not location:
            _ui(self.model, "setText", entry.get("model", {}).get("model", ""))
            _ui(
                self.preview,
                "setPlainText",
                tr_text(
                    "Nur Modellzuweisung – vorhandene Audioquelle bleibt unverändert. Noch nicht gespeichert.\n"
                )
                + entry["title"],
            )
            return
        _ui(
            self.source,
            "setText",
            ("lib://" if location["special"] == "library" else "content://")
            + location["path"],
        )
        _ui(self.model, "setText", entry.get("model", {}).get("model", ""))
        self.nocloud.setChecked(True)
        _ui(
            self.preview,
            "setPlainText",
            tr_text("Vorgeschlagene Zuordnung – noch nicht gespeichert:\n")
            + entry["title"]
            + "\n"
            + self.source.text()
            + "\n"
            + self.model.text(),
        )

    def clear(self):
        self.source.clear()
        self.model.clear()
        _ui(
            self.preview,
            "setPlainText",
            tr_text(
                "Zuordnung lösen – noch nicht gespeichert. Vorhandene Audiodateien werden nicht gelöscht."
            ),
        )

    def apply(self):
        try:
            if not self.loaded:
                raise ValueError(tr_text("Tonie / Tag zuerst laden"))
            uid = normalize_uid(self.uid.text(), self.uid_mode.currentIndex() == 1)
            if (uid, self.overlay.text().strip(), self.settings.url.rstrip("/")) != (
                self.loaded["uid"],
                self.loaded["overlay"],
                self.loaded["url"],
            ):
                raise ValueError(
                    tr_text("Adresse, UID oder Overlay geändert: zuerst neu laden")
                )
            after = self.values()
            changes = [
                _f(
                    "{p0}: {p1} → {p2}",
                    p0=key,
                    p1=self.loaded["before"]["values"][key],
                    p2=value,
                )
                for key, value in after.items()
                if self.loaded["before"]["values"][key] != value
            ]
            if not changes:
                _ui(
                    QMessageBox,
                    "information",
                    self,
                    tr_text("Zuordnung"),
                    tr_text("Keine Änderungen."),
                )
                return
            text = (
                _f(
                    "Tonie/RUID: {p0}\nZiel: {p1}\nOverlay: {p2}\n\n",
                    p0=uid,
                    p1=self.loaded["url"],
                    p2=self.loaded["overlay"] or tr_text("Standard"),
                )
                + tr_text("\n").join(changes)
                + tr_text("\n\nNur diese Tag-Zuordnung ändern?")
            )
            _ui(self.preview, "setPlainText", text)
            if (
                _ui(QMessageBox, "question", self, tr_text("Zuordnungsvorschau"), text)
                != QMessageBox.StandardButton.Yes
            ):
                return
            payload = {
                "book": Book("tag-" + uid, "", tr_text("Tag ") + uid).to_dict(),
                "settings": asdict(self.settings),
                "profile": asdict(self.settings.profile()),
                "tag": {
                    "uid": uid,
                    "overlay": self.loaded["overlay"],
                    "before": self.loaded["before"],
                    "after": after,
                },
            }
            self.store.add_job(tr_text("Tonie / Tag zuordnen"), payload)
            self.jobs_added()
        except Exception as exc:
            _ui(QMessageBox, "warning", self, tr_text("Zuordnung"), str(exc))
