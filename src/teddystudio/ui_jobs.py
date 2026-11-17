"""Persistent job status, boundary pause, targeted retry and readable diagnostics."""

from pathlib import Path
import json
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QComboBox,
    QTableWidget,
    QTableWidgetItem,
    QPlainTextEdit,
    QLabel,
    QFileDialog,
    QMessageBox,
    QCheckBox,
)
from .storage import atomic_json
from .ui_common import button, heading, row


class JobsPage(QWidget):
    def __init__(self, store, worker):
        super().__init__()
        self.store, self.worker = store, worker
        layout = QVBoxLayout(self)
        layout.addWidget(heading("Aufträge", "Importe, Audioschnitte und Prüfungen"))
        self.filter = QComboBox()
        self.filter.addItems(
            [
                "Alle",
                "Aktiv",
                "Wartet",
                "Abgeschlossen",
                "Fehler",
                "Unterbrochen",
                "Abgebrochen",
                "Pausiert",
            ]
        )
        self.counts = QLabel()
        self.counts.setWordWrap(True)
        self.hide_finished = QCheckBox("Fertige ausblenden")
        self.hide_finished.setToolTip(
            "Bestätigte und lokal gespeicherte Aufträge ausblenden; Protokolle bleiben erhalten."
        )
        layout.addWidget(
            row(
                self.filter,
                self.hide_finished,
                self.counts,
            )
        )
        self.table = QTableWidget(0, 5)
        layout.addWidget(
            row(
                button("Warteschlange starten / fortsetzen", self.start_queue, True),
                button(
                    "Warteschlange pausieren",
                    worker.pause_all,
                    tooltip="Auch laufende Arbeitsprozesse anhalten. Arbeitsstand bleibt erhalten; Start setzt nach erneuter Prüfung fort.",
                ),
                button(
                    "Warteschlange abbrechen",
                    worker.cancel_all,
                    tooltip="Alle offenen Aufträge und laufenden Konverter stoppen. Bereits gespeicherte Medien bleiben erhalten.",
                ),
                button(
                    "Warteschlange leeren …",
                    self.delete_queue,
                    tooltip="Alle Aufträge einschließlich laufender und fertiger Einträge aus dem Journal entfernen. Laufende Prozesse werden zuerst beendet.",
                ),
            )
        )
        self.table.setHorizontalHeaderLabels(
            ["Auftrag", "Typ", "Status", "Fortschritt", "Meldung"]
        )
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, 1)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.log, 1)
        layout.addWidget(
            row(
                button("Nur diesen Auftrag wiederholen", self.retry_one),
                button("Fehlgeschlagene wiederholen", self.retry_failed),
                button("Unterbrochene fortsetzen", self.resume),
                button("Ergebnis erneut prüfen", self.verify),
                button("Protokoll exportieren", self.export_log),
                button("Verlauf exportieren", self.export_history),
                button(
                    "Ausgewählte abbrechen",
                    self.cancel_selected,
                    tooltip="Ausgewählte laufende Arbeitsprozesse samt Konvertern stoppen; wartende Aufträge aus der Warteschlange nehmen.",
                ),
                button(
                    "Ausgewählte löschen …",
                    self.delete_selected,
                    tooltip="Ausgewählte Aufträge und deren Protokolle löschen. Audiodateien und TeddyCloud bleiben unverändert.",
                ),
                button(
                    "Verlauf bereinigen …",
                    self.clear_history,
                    tooltip="Abgeschlossene und fehlgeschlagene Aufträge samt Protokollen entfernen. Offene Aufträge behalten.",
                ),
            )
        )
        self.filter.currentTextChanged.connect(self.refresh)
        self.hide_finished.toggled.connect(self.refresh)
        self.table.currentCellChanged.connect(self.show_log)
        self.refresh()

    def selected(self):
        index = self.table.currentRow()
        return self.rows[index] if 0 <= index < len(self.rows) else None

    def refresh(self):
        previous = (
            self.selected()["id"] if hasattr(self, "rows") and self.selected() else ""
        )
        selected_ids = (
            {
                self.rows[i.row()]["id"]
                for i in self.table.selectionModel().selectedRows()
            }
            if hasattr(self, "rows")
            else set()
        )
        all_rows = self.store.jobs()
        choice = self.filter.currentText()
        self.rows = [
            r
            for r in all_rows
            if choice == "Alle"
            or r["status"] == choice
            or choice == "Abgeschlossen"
            and r["status"] in {"Bestätigt", "Lokal gespeichert"}
            or choice == "Fehler"
            and r["status"] == "Fehlgeschlagen"
            or choice == "Aktiv"
            and r["status"] in {"Abbruch angefordert", "Wird gelöscht", "Wird pausiert"}
        ]
        if self.hide_finished.isChecked():
            self.rows = [
                r
                for r in self.rows
                if r["status"] not in {"Bestätigt", "Lokal gespeichert"}
            ]
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.rows))
        for index, entry in enumerate(self.rows):
            payload = json.loads(entry["payload"])
            for column, value in enumerate(
                [
                    payload["book"]["title"],
                    entry["kind"],
                    {
                        "Bestätigt": "Fertig · Cloud geprüft",
                        "Lokal gespeichert": "Fertig · lokal",
                        "Aktiv": "Läuft",
                    }.get(entry["status"], entry["status"]),
                    f"{entry['progress']:.0%}",
                    entry["message"],
                ]
            ):
                self.table.setItem(index, column, QTableWidgetItem(value))
            if entry["id"] == previous:
                self.table.setCurrentCell(index, 0)
        self.table.blockSignals(False)
        self.table.clearSelection()
        for index, entry in enumerate(self.rows):
            if entry["id"] in selected_ids:
                for column in range(self.table.columnCount()):
                    self.table.item(index, column).setSelected(True)
        finished = sum(
            r["status"] in {"Bestätigt", "Lokal gespeichert"} for r in all_rows
        )
        self.counts.setText(
            f"{len(all_rows)} insgesamt · {sum(r['status']=='Aktiv' for r in all_rows)} läuft · {sum(r['status']=='Wartet' for r in all_rows)} wartet · {finished} fertig · {sum(r['status']=='Fehlgeschlagen' for r in all_rows)} Fehler"
        )
        self.show_log()

    def show_all(self):
        self.filter.setCurrentText("Alle")
        self.hide_finished.setChecked(False)
        self.refresh()

    def start_queue(self):
        self.show_all()
        self.worker.start()

    def delete_queue(self):
        if (
            QMessageBox.question(
                self,
                "Warteschlange leeren",
                "Gesamte Warteschlange samt Verlauf leeren? Laufende Prozesse werden gestoppt. Vorhandene Audio-/Exportdateien und TeddyCloud-Inhalte bleiben erhalten.",
            )
            == QMessageBox.StandardButton.Yes
        ):
            self.worker.delete_all()
            self.refresh()

    def show_log(self, *args):
        entry = self.selected()
        if not entry:
            self.log.clear()
            return
        payload = json.loads(entry["payload"])
        text = [
            f"Profil: {payload['profile']['name']}",
            f"Ziel: {payload['settings']['url']}",
            "",
        ]
        from datetime import datetime

        for event in self.store.events(entry["id"]):
            text.append(
                f"{datetime.fromtimestamp(event['timestamp']).isoformat(timespec='seconds')} · {event['stage']}\n{event['message']}"
            )
        self.log.setPlainText("\n".join(text))

    def retry_one(self):
        entry = self.selected()
        if entry and entry["status"] in {
            "Fehlgeschlagen",
            "Unterbrochen",
            "Abgebrochen",
            "Pausiert",
        }:
            payload = json.loads(entry["payload"])
            current = next(
                (b for b in self.store.books() if b.id == payload["book"]["id"]), None
            )
            if current:
                from .library import resolve_legacy_order

                try:
                    if resolve_legacy_order(current):
                        self.store.save_book(current)
                except OSError as exc:
                    QMessageBox.warning(self, "Quelle nicht erreichbar", str(exc))
                    return
            if (
                current
                and self.import_data(current.to_dict())
                != self.import_data(payload["book"])
                and entry["kind"] == "Import"
            ):
                if (
                    current.order_pending
                    or any(t.error for t in current.tracks)
                    or not current.tracks
                ):
                    QMessageBox.information(
                        self,
                        "Quelle prüfen",
                        "In der Sammlung sind noch Audio- oder Reihenfolgefehler vorhanden. Unter Spuren & Reihenfolge prüfen und bestätigen.",
                    )
                    return
                if (
                    QMessageBox.question(
                        self,
                        "Aktualisierte Sammlung",
                        "Die Sammlungsdaten wurden seit diesem Auftrag geändert. Einen neuen Auftrag mit den aktuellen Daten und der bestätigten Reihenfolge anlegen? Ziel und Namensprofil bleiben wie im alten Auftrag. Der alte Verlauf bleibt erhalten.",
                    )
                    != QMessageBox.StandardButton.Yes
                ):
                    return
                payload["book"] = current.to_dict()
                self.store.add_job(entry["kind"], payload)
                self.start_queue()
                return
            self.store.update_job(entry["id"], "Wartet")
            self.start_queue()

    @staticmethod
    def import_data(value):
        """Journal/UI status and schema defaults must not force a fresh conversion."""
        from .domain import Book

        data = Book.from_dict(value).to_dict()
        for key in ("status", "remote_model", "origin", "confirmed_order"):
            data.pop(key, None)
        return data

    def delete_selected(self):
        identifiers = [
            self.rows[index.row()]["id"]
            for index in self.table.selectionModel().selectedRows()
        ]
        self.delete_entries(identifiers)

    def cancel_selected(self):
        identifiers = [
            self.rows[i.row()]["id"] for i in self.table.selectionModel().selectedRows()
        ]
        self.worker.cancel(identifiers)
        self.refresh()

    def clear_history(self):
        self.delete_entries(
            [
                r["id"]
                for r in self.store.jobs()
                if r["status"]
                in {"Bestätigt", "Lokal gespeichert", "Fehlgeschlagen", "Abgebrochen"}
            ]
        )

    def delete_entries(self, identifiers):
        if not identifiers:
            return
        if (
            QMessageBox.question(
                self,
                "Aufträge löschen",
                f"{len(identifiers)} Aufträge löschen? Laufende Aufträge werden zuerst samt Konvertern abgebrochen. Danach werden die Einträge und ihre Journalprotokolle entfernt. Bereits gespeicherte Dateien und TeddyCloud bleiben erhalten.",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        try:
            self.worker.delete(identifiers)
            self.refresh()
        except Exception as exc:
            QMessageBox.warning(self, "Löschen nicht möglich", str(exc))

    def retry_failed(self):
        for entry in self.store.jobs():
            if entry["status"] == "Fehlgeschlagen":
                self.store.update_job(entry["id"], "Wartet")
        self.start_queue()

    def resume(self):
        for entry in self.store.jobs():
            if entry["status"] == "Unterbrochen":
                self.store.update_job(entry["id"], "Wartet")
        self.start_queue()

    def export_log(self):
        entry = self.selected()
        if not entry:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Auftragsprotokoll speichern", "Auftrag.json", "JSON (*.json)"
        )
        if path:
            atomic_json(
                Path(path), {"job": entry, "events": self.store.events(entry["id"])}
            )

    def verify(self):
        entry = self.selected()
        if not entry or entry["kind"] != "Import" or entry["status"] != "Bestätigt":
            QMessageBox.information(
                self, "Prüfung", "Bitte einen bestätigten Importauftrag auswählen."
            )
            return
        from .storage import data_directory
        from .domain import Book, NamingProfile

        payload = json.loads(entry["payload"])
        naming = NamingProfile(**payload["profile"]).render(
            Book.from_dict(payload["book"])
        )
        payload["taf"] = str(
            data_directory() / "work" / entry["id"] / (naming["filename"] + ".taf")
        )
        result_path = data_directory() / "work" / entry["id"] / "result.json"
        if result_path.is_file():
            result = json.loads(result_path.read_text(encoding="utf-8"))
            payload["expected_taf_hash"] = result["validation"]["sha256"]
        self.store.add_job("Prüfung", payload)
        self.start_queue()

    def export_history(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Verlauf speichern", "Auftragsverlauf.json", "JSON (*.json)"
        )
        if path:
            atomic_json(
                Path(path), {"jobs": self.store.jobs(), "events": self.store.events()}
            )
