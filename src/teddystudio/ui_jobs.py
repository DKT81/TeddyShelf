"""Persistent job status, boundary pause, targeted retry and readable diagnostics."""

from .i18n import text as tr_text, format_text as _f
from .i18n_widgets import call as _ui, create as _new

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
        layout.addWidget(
            heading(
                tr_text("Aufträge"), tr_text("Importe, Audioschnitte und Prüfungen")
            )
        )
        self.filter = _new(QComboBox)
        _ui(
            self.filter,
            "addItems",
            [
                tr_text("Alle"),
                tr_text("Aktiv"),
                tr_text("Wartet"),
                tr_text("Abgeschlossen"),
                tr_text("Fehler"),
                tr_text("Unterbrochen"),
                tr_text("Abgebrochen"),
                tr_text("Pausiert"),
            ],
        )
        self.counts = _new(QLabel)
        self.counts.setWordWrap(True)
        self.hide_finished = _new(QCheckBox, tr_text("Fertige ausblenden"))
        _ui(
            self.hide_finished,
            "setToolTip",
            tr_text(
                "Bestätigte und lokal gespeicherte Aufträge ausblenden; Protokolle bleiben erhalten."
            ),
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
                button(
                    tr_text("Warteschlange starten / fortsetzen"),
                    self.start_queue,
                    True,
                ),
                button(
                    tr_text("Warteschlange pausieren"),
                    worker.pause_all,
                    tooltip=tr_text(
                        "Auch laufende Arbeitsprozesse anhalten. Arbeitsstand bleibt erhalten; Start setzt nach erneuter Prüfung fort."
                    ),
                ),
                button(
                    tr_text("Warteschlange abbrechen"),
                    worker.cancel_all,
                    tooltip=tr_text(
                        "Alle offenen Aufträge und laufenden Konverter stoppen. Bereits gespeicherte Medien bleiben erhalten."
                    ),
                ),
                button(
                    tr_text("Warteschlange leeren …"),
                    self.delete_queue,
                    tooltip=tr_text(
                        "Alle Aufträge einschließlich laufender und fertiger Einträge aus dem Journal entfernen. Laufende Prozesse werden zuerst beendet."
                    ),
                ),
            )
        )
        _ui(
            self.table,
            "setHorizontalHeaderLabels",
            [
                tr_text("Auftrag"),
                tr_text("Typ"),
                tr_text("Status"),
                tr_text("Fortschritt"),
                tr_text("Meldung"),
            ],
        )
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, 1)
        self.log = _new(QPlainTextEdit)
        self.log.setReadOnly(True)
        layout.addWidget(self.log, 1)
        layout.addWidget(
            row(
                button(tr_text("Nur diesen Auftrag wiederholen"), self.retry_one),
                button(tr_text("Fehlgeschlagene wiederholen"), self.retry_failed),
                button(tr_text("Unterbrochene fortsetzen"), self.resume),
                button(tr_text("Ergebnis erneut prüfen"), self.verify),
                button(tr_text("Protokoll exportieren"), self.export_log),
                button(tr_text("Verlauf exportieren"), self.export_history),
                button(
                    tr_text("Ausgewählte abbrechen"),
                    self.cancel_selected,
                    tooltip=tr_text(
                        "Ausgewählte laufende Arbeitsprozesse samt Konvertern stoppen; wartende Aufträge aus der Warteschlange nehmen."
                    ),
                ),
                button(
                    tr_text("Ausgewählte löschen …"),
                    self.delete_selected,
                    tooltip=tr_text(
                        "Ausgewählte Aufträge und deren Protokolle löschen. Audiodateien und TeddyCloud bleiben unverändert."
                    ),
                ),
                button(
                    tr_text("Verlauf bereinigen …"),
                    self.clear_history,
                    tooltip=tr_text(
                        "Abgeschlossene und fehlgeschlagene Aufträge samt Protokollen entfernen. Offene Aufträge behalten."
                    ),
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
                        tr_text("Bestätigt"): tr_text("Fertig · Cloud geprüft"),
                        tr_text("Lokal gespeichert"): tr_text("Fertig · lokal"),
                        tr_text("Aktiv"): tr_text("Läuft"),
                    }.get(entry["status"], entry["status"]),
                    f"{entry['progress']:.0%}",
                    entry["message"],
                ]
            ):
                if column in {1, 2, 4}:
                    value = tr_text(value)
                self.table.setItem(index, column, _new(QTableWidgetItem, value))
            if entry["id"] == previous:
                self.table.setCurrentCell(index, 0)
        self.table.blockSignals(False)
        self.table.clearSelection()
        for index, entry in enumerate(self.rows):
            if entry["id"] in selected_ids:
                for column in range(self.table.columnCount()):
                    self.table.item(index, column).setSelected(True)
        finished = sum(
            r["status"] in {tr_text("Bestätigt"), tr_text("Lokal gespeichert")}
            for r in all_rows
        )
        _ui(
            self.counts,
            "setText",
            _f(
                "{p0} insgesamt · {p1} läuft · {p2} wartet · {p3} fertig · {p4} Fehler",
                p0=len(all_rows),
                p1=sum(r["status"] == tr_text("Aktiv") for r in all_rows),
                p2=sum(r["status"] == tr_text("Wartet") for r in all_rows),
                p3=finished,
                p4=sum(r["status"] == tr_text("Fehlgeschlagen") for r in all_rows),
            ),
        )
        self.show_log()

    def show_all(self):
        self.filter.setCurrentText(tr_text("Alle"))
        self.hide_finished.setChecked(False)
        self.refresh()

    def start_queue(self):
        self.show_all()
        self.worker.start()

    def delete_queue(self):
        if (
            _ui(
                QMessageBox,
                "question",
                self,
                tr_text("Warteschlange leeren"),
                tr_text(
                    "Gesamte Warteschlange samt Verlauf leeren? Laufende Prozesse werden gestoppt. Vorhandene Audio-/Exportdateien und TeddyCloud-Inhalte bleiben erhalten."
                ),
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
            _f("Profil: {p0}", p0=payload["profile"]["name"]),
            _f("Ziel: {p0}", p0=payload["settings"]["url"]),
            "",
        ]
        from datetime import datetime

        for event in self.store.events(entry["id"]):
            text.append(
                _f(
                    "{p0} · {p1}\n{p2}",
                    p0=datetime.fromtimestamp(event["timestamp"]).isoformat(
                        timespec="seconds"
                    ),
                    p1=tr_text(event["stage"]),
                    p2=tr_text(event["message"]),
                )
            )
        _ui(self.log, "setPlainText", tr_text("\n").join(text))

    def retry_one(self):
        entry = self.selected()
        if entry and entry["status"] in {
            tr_text("Fehlgeschlagen"),
            tr_text("Unterbrochen"),
            tr_text("Abgebrochen"),
            tr_text("Pausiert"),
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
                    _ui(
                        QMessageBox,
                        "warning",
                        self,
                        tr_text("Quelle nicht erreichbar"),
                        str(exc),
                    )
                    return
            if (
                current
                and self.import_data(current.to_dict())
                != self.import_data(payload["book"])
                and entry["kind"] == tr_text("Import")
            ):
                if (
                    current.order_pending
                    or any(t.error for t in current.tracks)
                    or not current.tracks
                ):
                    _ui(
                        QMessageBox,
                        "information",
                        self,
                        tr_text("Quelle prüfen"),
                        tr_text(
                            "In der Sammlung sind noch Audio- oder Reihenfolgefehler vorhanden. Unter Spuren & Reihenfolge prüfen und bestätigen."
                        ),
                    )
                    return
                if (
                    _ui(
                        QMessageBox,
                        "question",
                        self,
                        tr_text("Aktualisierte Sammlung"),
                        tr_text(
                            "Die Sammlungsdaten wurden seit diesem Auftrag geändert. Einen neuen Auftrag mit den aktuellen Daten und der bestätigten Reihenfolge anlegen? Ziel und Namensprofil bleiben wie im alten Auftrag. Der alte Verlauf bleibt erhalten."
                        ),
                    )
                    != QMessageBox.StandardButton.Yes
                ):
                    return
                payload["book"] = current.to_dict()
                self.store.add_job(entry["kind"], payload)
                self.start_queue()
                return
            self.store.update_job(entry["id"], tr_text("Wartet"))
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
            _ui(
                QMessageBox,
                "question",
                self,
                tr_text("Aufträge löschen"),
                _f(
                    "{p0} Aufträge löschen? Laufende Aufträge werden zuerst samt Konvertern abgebrochen. Danach werden die Einträge und ihre Journalprotokolle entfernt. Bereits gespeicherte Dateien und TeddyCloud bleiben erhalten.",
                    p0=len(identifiers),
                ),
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        try:
            self.worker.delete(identifiers)
            self.refresh()
        except Exception as exc:
            _ui(
                QMessageBox, "warning", self, tr_text("Löschen nicht möglich"), str(exc)
            )

    def retry_failed(self):
        for entry in self.store.jobs():
            if entry["status"] == tr_text("Fehlgeschlagen"):
                self.store.update_job(entry["id"], tr_text("Wartet"))
        self.start_queue()

    def resume(self):
        for entry in self.store.jobs():
            if entry["status"] == tr_text("Unterbrochen"):
                self.store.update_job(entry["id"], tr_text("Wartet"))
        self.start_queue()

    def export_log(self):
        entry = self.selected()
        if not entry:
            return
        path, _ = _ui(
            QFileDialog,
            "getSaveFileName",
            self,
            tr_text("Auftragsprotokoll speichern"),
            "Auftrag.json",
            tr_text("JSON (*.json)"),
        )
        if path:
            atomic_json(
                Path(path), {"job": entry, "events": self.store.events(entry["id"])}
            )

    def verify(self):
        entry = self.selected()
        if (
            not entry
            or entry["kind"] != tr_text("Import")
            or entry["status"] != tr_text("Bestätigt")
        ):
            _ui(
                QMessageBox,
                "information",
                self,
                tr_text("Prüfung"),
                tr_text("Bitte einen bestätigten Importauftrag auswählen."),
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
        self.store.add_job(tr_text("Prüfung"), payload)
        self.start_queue()

    def export_history(self):
        path, _ = _ui(
            QFileDialog,
            "getSaveFileName",
            self,
            tr_text("Verlauf speichern"),
            "Auftragsverlauf.json",
            tr_text("JSON (*.json)"),
        )
        if path:
            atomic_json(
                Path(path), {"jobs": self.store.jobs(), "events": self.store.events()}
            )
