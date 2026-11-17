"""Server inventory, explicit local downloads, and concrete Tonie/tag assignment."""

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
                "TeddyCloud-Bestand",
                "Vorhandene Tonies einlesen und Audio lokal sichern – keine automatischen Tagänderungen",
            )
        )
        self.overlay = QLineEdit(settings.cloud_overlay)
        self.overlay.setPlaceholderText("Overlay / Box (optional)")
        self.overlay.setMaxLength(15)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Titel, Serie oder Modell suchen")
        self.kind = QComboBox()
        self.kind.addItems(["Alle Typen", "Custom", "Katalog", "Unzugeordnet"])
        self.available = QComboBox()
        self.available.addItems(
            ["Alle Einträge", "Audio vorhanden", "Audio fehlt / ungeklärt"]
        )
        self.read_button = button("TeddyCloud einlesen", self.read, True)
        layout.addWidget(
            row(
                self.read_button,
                self.overlay,
                self.search,
                self.kind,
                self.available,
                button("Einlesen abbrechen", self.cancel_read.set),
            )
        )
        self.tree = QTreeWidget()
        self.tree.setColumnCount(5)
        self.tree.setHeaderLabels(
            ["Titel", "Serie / Folge", "Typ", "Status", "Serverdatei"]
        )
        self.tree.setColumnWidth(0, 300)
        layout.addWidget(self.tree, 1)
        self.status = QLabel(
            "Noch nicht eingelesen. Es werden nur bereits auf TeddyCloud vorhandene Dateien angeboten."
        )
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.destination = QLineEdit(settings.download_destination)
        self.format = QComboBox()
        self.format.addItems(["OGG/Opus", "Original-TAF", "MP3", "FLAC", "WAV"])
        self.keep = QCheckBox("Original-TAF zusätzlich behalten")
        self.keep.setChecked(True)
        self.local = QCheckBox("In lokale Sammlung übernehmen")
        self.local.setChecked(True)
        self.split = QCheckBox("MP3/FLAC/WAV nach Kapiteln aufteilen")
        form = QFormLayout()
        form.addRow("Lokaler Zielordner", path_control(self.destination, self))
        form.addRow("Ausgabe", row(self.format, self.keep, self.local))
        form.addRow(self.split)
        layout.addLayout(form)
        hint = QLabel(
            "TAF/OGG bewahren den vorhandenen Tonie-Inhalt. Bei Sammlungsübernahme aus TAF wird zusätzlich eine abspielbare OGG-Datei erzeugt. MP3 wird neu kodiert. Kein Abruf aus der offiziellen Toniecloud."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addWidget(
            row(
                button("Sichtbare Audios auswählen", lambda: self.select_visible(True)),
                button("Auswahl aufheben", lambda: self.select_visible(False)),
                button("Aus TeddyCloud lokal übernehmen …", self.create_jobs, True),
                button("Tonie / Tag zuordnen …", self.assign),
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
        self.status.setText("Bestand wird gelesen …")
        settings = type(self.settings)(**asdict(self.settings))
        overlay = self.overlay.text().strip()

        def work(progress):
            def report(message):
                if self.cancel_read.is_set():
                    raise ValueError(
                        "Einlesen abgebrochen; letzter vollständiger Bestand bleibt erhalten"
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
                self.status.setText("Bestand nicht aktualisiert: " + result["error"])
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
        task.signals.progress.connect(self.status.setText)

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
            files = "; ".join(x["path"] for x in entry["locations"])
            item = QTreeWidgetItem(
                [
                    entry["title"],
                    entry["series"] + (" · " + episode if episode else ""),
                    entry["kind"],
                    entry["status"],
                    files,
                ]
            )
            item.setData(0, Qt.ItemDataRole.UserRole, entry)
            item.setToolTip(
                0, json.dumps(entry.get("aliases", []), ensure_ascii=False, indent=2)
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
            " · Bestand unvollständig: " + "; ".join(self.snapshot["warnings"])
            if self.snapshot["warnings"]
            else ""
        )
        self.status.setText(
            (
                "Gespeicherter Stand – neu einlesen empfohlen · "
                if self.snapshot.get("cached")
                else ""
            )
            + f"{self.tree.topLevelItemCount()} Einträge · {self.snapshot['url']} · nur vorhandene Dateien"
            + warning
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
                "Bestand für die aktuelle Adresse / das aktuelle Overlay zuerst neu einlesen"
            )

    def create_jobs(self):
        try:
            self.validate_snapshot()
            entries = self.selected_entries()
            if not entries:
                raise ValueError("Audiodateien zuerst über die Kästchen auswählen")
            destination = Path(self.destination.text()).expanduser().resolve()
            formats = ["ogg", "taf", "mp3", "flac", "wav"]
            fmt = formats[self.format.currentIndex()]
            preview = (
                "\n".join(e["title"] for e in entries)
                + f"\n\nZiel: {destination}\nFormat: {self.format.currentText()}\nTeddyCloud bleibt unverändert."
            )
            if (
                QMessageBox.question(self, "Lokale Übernahme", preview)
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
                    "Aus TeddyCloud holen",
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
                QMessageBox.information(
                    self,
                    "Downloads",
                    "Für diese Auswahl bestehen bereits offene Aufträge.",
                )
        except Exception as exc:
            QMessageBox.warning(self, "Lokale Übernahme", str(exc))

    def assign(self):
        try:
            self.validate_snapshot()
            item = self.tree.currentItem()
            if not item:
                raise ValueError("Einen vorhandenen Tonie auswählen")
            self.assign_requested.emit(item.data(0, Qt.ItemDataRole.UserRole))
        except Exception as exc:
            QMessageBox.warning(self, "Tagzuordnung", str(exc))


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
                "Tonies / Tags",
                "Konkreten Tonie mit Modell und Audio verknüpfen – kein Beschreiben eines Chips",
            )
        )
        sections = QSplitter(Qt.Orientation.Vertical)
        sections.setChildrenCollapsible(False)
        sections.setHandleWidth(7)
        layout.addWidget(sections, 1)
        self.tag_tree = QTreeWidget()
        self.tag_tree.setColumnCount(4)
        self.tag_tree.setHeaderLabels(
            ["Tonie / zugeordnetes Modell", "RUID", "Modellkennung", "Audioquelle"]
        )
        self.tag_tree.setColumnWidth(0, 270)
        self.tag_tree.setColumnWidth(1, 170)
        self.tag_tree.setMinimumHeight(90)
        self.tag_tree.setToolTip(
            "Doppelklick lädt die bestehende Zuordnung dieses konkreten Tonies."
        )
        sections.addWidget(self.tag_tree)
        editor = QWidget()
        sections.addWidget(editor)
        layout = QVBoxLayout(editor)
        sections.setSizes([180, 500])
        sections.handle(1).setToolTip(
            "Tonie-Liste und Zuordnungseditor in der Höhe anpassen"
        )
        self.uid = QLineEdit()
        self.uid.setPlaceholderText("16 Hex-Zeichen, wie in TeddyCloud")
        self.uid_mode = QComboBox()
        self.uid_mode.addItems(
            ["TeddyCloud-RUID", "NFC-UID: Byte-Reihenfolge umkehren"]
        )
        self.overlay = QLineEdit(settings.cloud_overlay)
        self.overlay.setMaxLength(15)
        self.overlay.setPlaceholderText("Overlay / Box (optional)")
        layout.addWidget(
            row(
                self.uid,
                self.uid_mode,
                self.overlay,
                button("Tonie / Tag laden", self.load, True),
                button("Bekannten Tag auswählen", self.choose),
            )
        )
        self.source = QLineEdit()
        self.source.setPlaceholderText(
            "lib://… oder content://… – leer = eigener Tonie-Inhalt"
        )
        self.model = QLineEdit()
        self.model.setPlaceholderText("Modellkennung")
        self.live = QCheckBox("Live-Inhalt")
        self.nocloud = QCheckBox("Kein Abruf aus Toniecloud")
        self.hide = QCheckBox("In TeddyCloud ausblenden")
        form = QFormLayout()
        form.addRow("Audiodatei / Quelle", self.source)
        form.addRow("Zugeordnetes Modell", self.model)
        form.addRow(row(self.live, self.nocloud, self.hide))
        layout.addLayout(form)
        layout.addWidget(
            row(
                button("Aus Bestand auswählen …", self.choose_model),
                button("Modell im Katalog suchen …", self.search_model),
                button("Modell-/Dateizuordnung lösen", self.clear),
                button("Änderung prüfen und zuordnen …", self.apply, True),
            )
        )
        self.info = QLabel(
            "Zuerst UID/RUID und Overlay wählen und die bestehende Zuordnung laden. Es werden weder Chip-UID noch Authentifizierungsdaten verändert."
        )
        self.info.setWordWrap(True)
        layout.addWidget(self.info)
        self.preview = QPlainTextEdit()
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
            item = QTreeWidgetItem(
                [record["title"], record["uid"], record["model"], record["source"]]
            )
            item.setData(0, Qt.ItemDataRole.UserRole, record)
            self.tag_tree.addTopLevelItem(item)

    def open_tag(self, item, column=0):
        record = item.data(0, Qt.ItemDataRole.UserRole)
        self.uid_mode.setCurrentIndex(0)
        self.uid.setText(record["uid"])
        self.overlay.setText(record["overlay"])
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
            QMessageBox.information(
                self, "Tags", "Bestand zuerst einlesen oder UID/RUID direkt eingeben."
            )
            return
        value, ok = QInputDialog.getItem(
            self, "Tonie / Tag", "TeddyCloud-RUID", tags, editable=False
        )
        if ok:
            self.uid_mode.setCurrentIndex(0)
            self.uid.setText(value)
            self.load()

    def load(self):
        try:
            uid = normalize_uid(self.uid.text(), self.uid_mode.currentIndex() == 1)
        except ValueError as exc:
            QMessageBox.warning(self, "UID", str(exc))
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
            self.source.setText(value["values"]["source"])
            self.model.setText(value["values"]["tonie_model"])
            for key in ("live", "nocloud", "hide"):
                getattr(self, key).setChecked(value["values"][key])
            self.info.setText(
                f"RUID: {uid} · {settings.url} · Overlay: {overlay or 'Standard'} · "
                + (
                    "bestehender Tonie/Tag"
                    if value["exists"]
                    else "noch kein Eintrag; wird erst nach Vorschau angelegt"
                )
            )
            self.preview.setPlainText(
                json.dumps(value["values"], ensure_ascii=False, indent=2)
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
                raise ValueError("Bestand für dasselbe Overlay / dieselbe Box einlesen")
        except ValueError as exc:
            QMessageBox.information(self, "Bestand", str(exc))
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
        value, ok = QInputDialog.getItem(
            self,
            "Modell und Audiodatei",
            "Vorhandenen Inhalt wählen",
            choices,
            editable=False,
        )
        if ok:
            self.bind_entry(entries[choices.index(value)])

    def search_model(self):
        query, ok = QInputDialog.getText(
            self, "Modell suchen", "Modellkennung, Titel oder Serie"
        )
        if not ok or not query.strip():
            return
        settings = type(self.settings)(**asdict(self.settings))
        needle = query.strip().casefold()

        def work(_):
            with TeddyCloud(settings) as client:
                models = (
                    client.models()
                    + client.request("GET", "/api/toniesJson", timeout=(5, 30)).json()
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
                QMessageBox.information(
                    self, "Modellsuche", "Keine passende Modellkennung gefunden."
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
            value, accepted = QInputDialog.getItem(
                self,
                "Modellzuweisung",
                "Modell wählen – Audiodatei bleibt unverändert",
                labels,
                editable=False,
            )
            if accepted:
                self.model.setText(models[labels.index(value)]["model"])

        background(self, work, done)

    def bind_entry(self, entry):
        location = next(
            (p for p in entry["locations"] if p["special"] == "library"),
            next(iter(entry["locations"]), None),
        )
        if not location:
            self.model.setText(entry.get("model", {}).get("model", ""))
            self.preview.setPlainText(
                "Nur Modellzuweisung – vorhandene Audioquelle bleibt unverändert. Noch nicht gespeichert.\n"
                + entry["title"]
            )
            return
        self.source.setText(
            ("lib://" if location["special"] == "library" else "content://")
            + location["path"]
        )
        self.model.setText(entry.get("model", {}).get("model", ""))
        self.nocloud.setChecked(True)
        self.preview.setPlainText(
            "Vorgeschlagene Zuordnung – noch nicht gespeichert:\n"
            + entry["title"]
            + "\n"
            + self.source.text()
            + "\n"
            + self.model.text()
        )

    def clear(self):
        self.source.clear()
        self.model.clear()
        self.preview.setPlainText(
            "Zuordnung lösen – noch nicht gespeichert. Vorhandene Audiodateien werden nicht gelöscht."
        )

    def apply(self):
        try:
            if not self.loaded:
                raise ValueError("Tonie / Tag zuerst laden")
            uid = normalize_uid(self.uid.text(), self.uid_mode.currentIndex() == 1)
            if (uid, self.overlay.text().strip(), self.settings.url.rstrip("/")) != (
                self.loaded["uid"],
                self.loaded["overlay"],
                self.loaded["url"],
            ):
                raise ValueError("Adresse, UID oder Overlay geändert: zuerst neu laden")
            after = self.values()
            changes = [
                f"{key}: {self.loaded['before']['values'][key]} → {value}"
                for key, value in after.items()
                if self.loaded["before"]["values"][key] != value
            ]
            if not changes:
                QMessageBox.information(self, "Zuordnung", "Keine Änderungen.")
                return
            text = (
                f"Tonie/RUID: {uid}\nZiel: {self.loaded['url']}\nOverlay: {self.loaded['overlay'] or 'Standard'}\n\n"
                + "\n".join(changes)
                + "\n\nNur diese Tag-Zuordnung ändern?"
            )
            self.preview.setPlainText(text)
            if (
                QMessageBox.question(self, "Zuordnungsvorschau", text)
                != QMessageBox.StandardButton.Yes
            ):
                return
            payload = {
                "book": Book("tag-" + uid, "", "Tag " + uid).to_dict(),
                "settings": asdict(self.settings),
                "profile": asdict(self.settings.profile()),
                "tag": {
                    "uid": uid,
                    "overlay": self.loaded["overlay"],
                    "before": self.loaded["before"],
                    "after": after,
                },
            }
            self.store.add_job("Tonie / Tag zuordnen", payload)
            self.jobs_added()
        except Exception as exc:
            QMessageBox.warning(self, "Zuordnung", str(exc))
