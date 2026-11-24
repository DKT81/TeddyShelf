"""Web preview, per-entry queue controls and explicit ordered audio handoff."""

from pathlib import Path
import json
import threading

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QFormLayout,
    QLineEdit,
    QLabel,
    QComboBox,
    QTableWidget,
    QTableWidgetItem,
    QAbstractItemView,
    QHeaderView,
    QSizePolicy,
    QMessageBox,
)

from .i18n import text as tr_text, format_text as _f
from .i18n_widgets import call as _ui, create as _new
from .ui_common import background, button, row, heading, path_control, clock
from .web_tools import ToolManager
from . import web_downloads, media
from .domain import Book, Track


class WebPage(QWidget):
    def __init__(self, store, settings, worker, jobs_added, audio_handoff):
        super().__init__()
        self.store, self.settings, self.worker = store, settings, worker
        self.jobs_added, self.audio_handoff = jobs_added, audio_handoff
        self.tools = ToolManager()
        self.cancelled = threading.Event()
        self.snapshot = None
        self.busy = False
        self.job_ids = {}
        layout = QVBoxLayout(self)
        layout.addWidget(
            heading(
                tr_text("Web-Download"),
                tr_text("URL prüfen · Einträge wählen · lokal herunterladen"),
            )
        )
        form = QFormLayout()
        self.url = _new(QLineEdit)
        _ui(
            self.url,
            "setPlaceholderText",
            tr_text("Einzelvideo oder Playlist: https://…"),
        )
        self.preview_button = button(
            tr_text("URL-Vorschau laden"),
            self.inspect,
            True,
            tr_text(
                "Metadaten mit lokalem yt-dlp lesen; noch keine Medien herunterladen."
            ),
        )
        form.addRow(_new(QLabel, tr_text("URL")), row(self.url, self.preview_button))
        self.mode = _new(QComboBox)
        _ui(self.mode, "addItems", [tr_text("Audio (MP3)"), tr_text("Video (MP4)")])
        _ui(
            self.mode,
            "setToolTip",
            tr_text(
                "Audio wird vollständig als MP3 gespeichert; Video enthält Bild und Ton als MP4. Kein automatischer TAF-Upload."
            ),
        )
        form.addRow(_new(QLabel, tr_text("Download als")), self.mode)
        self.destination = _new(QLineEdit, settings.web_destination)
        form.addRow(
            _new(QLabel, tr_text("Zielordner")), path_control(self.destination, self)
        )
        layout.addLayout(form)
        self.batches = _new(QComboBox)
        self.batches.setProperty("i18nUserContent", True)
        self.batches.setMinimumWidth(300)
        self.batches.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        _ui(self.batches, "setPlaceholderText", tr_text("Noch keine Web-Aufträge"))
        self.batches.currentIndexChanged.connect(self.load_batch)
        layout.addWidget(
            row(_new(QLabel, tr_text("Bisherige Web-Aufträge")), self.batches)
        )
        self.table = QTableWidget(0, 4)
        _ui(
            self.table,
            "setHorizontalHeaderLabels",
            [
                tr_text("Nr."),
                tr_text("Titel / Auswahl"),
                tr_text("Dauer"),
                tr_text("Auftragsstatus"),
            ],
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(28)
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.table.setColumnWidth(0, 55)
        self.table.setColumnWidth(2, 100)
        self.table.setColumnWidth(3, 240)
        layout.addWidget(self.table, 1)
        layout.addWidget(
            row(
                button(tr_text("Alle auswählen"), lambda: self.check_all(True)),
                button(tr_text("Alle abwählen"), lambda: self.check_all(False)),
                button(tr_text("Auswahl herunterladen"), self.enqueue, True),
                button(tr_text("Ausgewählte abbrechen"), self.cancel_selected),
                button(tr_text("Fehler der Auswahl wiederholen"), self.retry_selected),
                button(
                    tr_text("Fertiges Audio an Audioansicht übergeben"), self.handoff
                ),
            )
        )
        self.status = _new(QLabel)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        tools_form = QFormLayout()
        self.versions = _new(QLabel)
        self.versions.setWordWrap(True)
        tools_form.addRow(_new(QLabel, tr_text("Download-Werkzeuge")), self.versions)
        self.channel = _new(QComboBox)
        _ui(self.channel, "addItems", [tr_text("Stable"), tr_text("Nightly")])
        self.update_button = button(
            tr_text("yt-dlp-Update prüfen …"), self.update_engine
        )
        self.runtime_button = button(
            tr_text("Deno einrichten / aktualisieren …"),
            lambda: self.update_engine("deno"),
        )
        tools_form.addRow(
            _new(QLabel, tr_text("Updatekanal")),
            row(self.channel, self.update_button, self.runtime_button),
        )
        tools_form.addRow(
            row(
                button(
                    tr_text("Vorheriges yt-dlp aktivieren"),
                    lambda: self.rollback("yt-dlp"),
                ),
                button(
                    tr_text("Vorheriges Deno aktivieren"), lambda: self.rollback("deno")
                ),
                button(
                    tr_text("Vorschau / Einrichtung abbrechen"), self.cancel_background
                ),
                button(tr_text("Warteschlange starten / fortsetzen"), worker.start),
                button(tr_text("Warteschlange pausieren"), worker.pause_all),
            )
        )
        layout.addLayout(tools_form)
        self.reload_batches()
        self.refresh_tools()
        self.timer = QTimer(self)
        self.timer.setInterval(750)
        self.timer.timeout.connect(
            lambda: self.refresh_status() if self.isVisible() else None
        )
        self.timer.start()

    def cancel_background(self):
        self.cancelled.set()

    def task(self, work, done):
        if self.busy:
            return
        self.busy = True
        self.cancelled.clear()
        for control in (self.preview_button, self.update_button, self.runtime_button):
            control.setEnabled(False)

        def finish():
            self.busy = False
            for control in (
                self.preview_button,
                self.update_button,
                self.runtime_button,
            ):
                control.setEnabled(True)

        def success(value):
            finish()
            done(value)

        task = background(self, work, success)
        task.signals.failed.connect(lambda _: finish())
        task.signals.progress.connect(
            lambda value: _ui(self.status, "setText", tr_text(value))
        )

    def refresh_tools(self):
        state = self.tools.state()
        values = []
        for component in ("yt-dlp", "deno"):
            tool = state.get(component)
            values.append(
                component
                + ": "
                + (
                    tool["version"] + " · " + tool["channel"]
                    if tool
                    else tr_text("Nicht eingerichtet")
                )
            )
        _ui(self.versions, "setText", tr_text("\n").join(values))

    def update_engine(self, component="yt-dlp"):
        # clicked(bool) must not be interpreted as the component name.
        component = component if isinstance(component, str) else "yt-dlp"
        channel = "nightly" if self.channel.currentIndex() else "stable"

        def found(release):
            if (
                _ui(
                    QMessageBox,
                    "question",
                    self,
                    tr_text("Web-Werkzeug aktualisieren"),
                    _f(
                        "{p0} {p1} aus {p2} herunterladen, prüfen und aktivieren? Die bisherige Version bleibt für eine Rückkehr erhalten.",
                        p0=component,
                        p1=release["tag"],
                        p2=release["repository"],
                    ),
                )
                != QMessageBox.StandardButton.Yes
            ):
                return

            def installed(tool):
                self.refresh_tools()
                _ui(
                    self.status,
                    "setText",
                    _f("Werkzeug aktiviert: {p0}", p0=tool["version"]),
                )

            self.task(
                lambda progress: self.tools.install(
                    release, progress, self.cancelled.is_set
                ),
                installed,
            )

        self.task(lambda _: self.tools.release(component, channel), found)

    def rollback(self, component):
        if self.busy:
            return
        try:
            self.tools.rollback(component)
            self.refresh_tools()
        except Exception as exc:
            _ui(QMessageBox, "warning", self, tr_text("Werkzeugwechsel"), str(exc))

    def inspect(self):
        try:
            url = web_downloads.web_url(self.url.text())
            tools = self.tools.snapshot()
        except Exception as exc:
            _ui(QMessageBox, "warning", self, tr_text("Web-Vorschau"), str(exc))
            return

        def loaded(snapshot):
            self.snapshot = snapshot
            self.job_ids = {}
            self.render_entries()
            _ui(
                self.status,
                "setText",
                tr_text(
                    "Alle verfügbaren Einträge sind ausgewählt. Einzelne Einträge können abgewählt werden."
                ),
            )

        self.task(
            lambda _: web_downloads.preview(
                url, tools, self.settings, self.cancelled.is_set
            ),
            loaded,
        )

    def render_entries(self):
        self.table.setRowCount(len(self.snapshot["entries"]))
        for row_index, entry in enumerate(self.snapshot["entries"]):
            duration = entry.get("duration")
            cells = [
                str(entry["index"]),
                entry["title"],
                (
                    clock(duration)
                    if isinstance(duration, (int, float)) and duration > 0
                    else "—"
                ),
                "",
            ]
            for column, value in enumerate(cells):
                cell = _new(QTableWidgetItem, value)
                if column == 1:
                    cell.setFlags(cell.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    cell.setCheckState(
                        Qt.CheckState.Unchecked
                        if entry.get("error")
                        else Qt.CheckState.Checked
                    )
                    if entry.get("error"):
                        cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEnabled)
                self.table.setItem(row_index, column, cell)
        self.refresh_status()

    def selected_entries(self):
        if not self.snapshot:
            return []
        return [
            entry
            for row_index, entry in enumerate(self.snapshot["entries"])
            if not entry.get("error")
            and self.table.item(row_index, 1).checkState() == Qt.CheckState.Checked
        ]

    def check_all(self, checked):
        if self.snapshot:
            for row_index, entry in enumerate(self.snapshot["entries"]):
                if not entry.get("error"):
                    self.table.item(row_index, 1).setCheckState(
                        Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
                    )

    def enqueue(self):
        if self.busy:
            return
        try:
            entries = self.selected_entries()
            if not entries:
                raise ValueError(tr_text("Bitte mindestens einen Eintrag auswählen"))
            destination = self.destination.text().strip()
            if not destination:
                raise ValueError(tr_text("Bitte einen Zielordner auswählen"))
            if self.job_ids:
                raise ValueError(
                    tr_text(
                        "Für diese Vorschau bestehen bereits Aufträge. Fehler gezielt wiederholen oder die URL erneut prüfen."
                    )
                )
            tools = self.tools.snapshot()
            self.settings.web_destination = str(Path(destination).resolve())
            self.settings.save()
            payloads = web_downloads.make_payloads(
                self.snapshot,
                entries,
                destination,
                "video" if self.mode.currentIndex() else "audio",
                tools,
                self.settings,
            )
            for payload in payloads:
                self.store.add_job(web_downloads.JOB_KIND, payload)
            self.reload_batches(self.snapshot["id"])
            self.refresh_status()
            self.jobs_added()
        except Exception as exc:
            _ui(QMessageBox, "warning", self, tr_text("Web-Download"), str(exc))

    def reload_batches(self, selected=None):
        grouped = {}
        for job in self.store.jobs():
            payload = json.loads(job["payload"])
            if job["kind"] == web_downloads.JOB_KIND:
                options = payload["web"]
                grouped.setdefault(options["batch"], options["playlist"])
        self.batches.blockSignals(True)
        self.batches.clear()
        for batch, title in grouped.items():
            self.batches.addItem(title + " · " + batch[:8], batch)
        if selected:
            self.batches.setCurrentIndex(self.batches.findData(selected))
        self.batches.blockSignals(False)
        if self.batches.count() and (selected or self.snapshot is None):
            self.load_batch()

    def load_batch(self):
        batch = self.batches.currentData()
        jobs = self.batch_jobs(batch)
        if not jobs:
            return
        options = json.loads(jobs[0]["payload"])["web"]
        self.snapshot = {
            "id": batch,
            "title": options["playlist"],
            "entries": sorted(
                [json.loads(job["payload"])["web"]["entry"] for job in jobs],
                key=lambda e: e["index"],
            ),
        }
        self.mode.setCurrentIndex(0 if options["mode"] == "audio" else 1)
        _ui(self.destination, "setText", options["destination"])
        self.render_entries()

    def batch_jobs(self, batch=None):
        batch = batch or (self.snapshot["id"] if self.snapshot else None)
        return [
            job
            for job in self.store.jobs()
            if job["kind"] == web_downloads.JOB_KIND
            and json.loads(job["payload"])["web"]["batch"] == batch
        ]

    def refresh_status(self):
        if not self.snapshot:
            return
        jobs = {
            json.loads(job["payload"])["web"]["entry"]["index"]: job
            for job in self.batch_jobs()
        }
        self.job_ids = {index: job["id"] for index, job in jobs.items()}
        for row_index, entry in enumerate(self.snapshot["entries"]):
            job = jobs.get(entry["index"])
            value = (
                _f("{p0} · {p1:.0%}", p0=tr_text(job["status"]), p1=job["progress"])
                if job
                else tr_text(entry.get("error") or tr_text("Noch kein Auftrag"))
            )
            cell = _new(QTableWidgetItem, value)
            _ui(cell, "setToolTip", tr_text(job["message"]) if job else entry["url"])
            self.table.setItem(row_index, 3, cell)

    def cancel_selected(self):
        self.worker.cancel(
            [
                self.job_ids[e["index"]]
                for e in self.selected_entries()
                if e["index"] in self.job_ids
            ]
        )

    def retry_selected(self):
        selected = {e["index"] for e in self.selected_entries()}
        for job in self.batch_jobs():
            entry = json.loads(job["payload"])["web"]["entry"]
            if entry["index"] in selected and job["status"] in {
                "Fehlgeschlagen",
                "Unterbrochen",
                "Abgebrochen",
                "Pausiert",
            }:
                self.store.update_job(job["id"], "Wartet")
        self.worker.start()

    def handoff(self):
        selected = {entry["index"] for entry in self.selected_entries()}
        options = [
            json.loads(job["payload"])["web"]
            for job in self.batch_jobs()
            if job["status"] == "Lokal gespeichert"
            and json.loads(job["payload"])["web"]["mode"] == "audio"
            and json.loads(job["payload"])["web"]["entry"]["index"] in selected
        ]
        if not options:
            _ui(
                QMessageBox,
                "information",
                self,
                tr_text("Audio übergeben"),
                tr_text("Bitte fertige Audio-Downloads auswählen."),
            )
            return
        title = self.snapshot["title"]

        def read(_):
            tracks = []
            for option in sorted(options, key=lambda value: value["entry"]["index"]):
                saved = web_downloads.saved_result(option)
                if saved is None:
                    raise ValueError(
                        tr_text("Download fehlt; Auftrag erneut ausführen")
                    )
                info = media.probe(saved["path"], self.settings.ffprobe)
                tracks.append(
                    Track(
                        saved["path"],
                        option["entry"]["title"],
                        str(option["entry"]["index"]),
                        info["duration"],
                    )
                )
            return tracks

        def ready(tracks):
            self.audio_handoff(title, tracks)

        self.task(read, ready)
