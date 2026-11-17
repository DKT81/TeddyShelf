"""Audio source ordering, preview player, waveform and real split-job creation."""

from dataclasses import asdict
from pathlib import Path
import uuid

from PySide6.QtCore import Qt, QTime, QUrl, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QListWidget,
    QAbstractItemView,
    QLineEdit,
    QTimeEdit,
    QComboBox,
    QTableWidget,
    QTableWidgetItem,
    QFileDialog,
    QFormLayout,
    QLabel,
    QMessageBox,
    QSlider,
    QSplitter,
    QHeaderView,
)

from .domain import Book, Track
from . import media
from .ui_common import background, button, heading, row, clock, path_control


class SourceList(QListWidget):
    """Accept Explorer files/folders and reorder the input playlist internally."""

    files_dropped = Signal(object)
    reordered = Signal()

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            paths = [
                u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()
            ]
            self.files_dropped.emit(paths)
            event.acceptProposedAction()
        else:
            super().dropEvent(event)
            self.reordered.emit()


class Waveform(QWidget):
    def __init__(self):
        super().__init__()
        self.values, self.duration, self.seconds = [], 0, 600
        self.boundaries = None
        self.setMinimumHeight(60)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#e5f1eb"))
        painter.setPen(QPen(QColor("#00877e"), 1))
        if self.values:
            for index, value in enumerate(self.values):
                x = round(index / len(self.values) * self.width())
                half = value * (self.height() / 2 - 10)
                painter.drawLine(
                    x,
                    round(self.height() / 2 - half),
                    x,
                    round(self.height() / 2 + half),
                )
        else:
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                (
                    "Wellenform laden, um das Audio anzuzeigen"
                    if self.duration
                    else "Audiodateien hinzufügen oder Auswahl aus Sammlung übernehmen"
                ),
            )
        if self.duration:
            painter.setPen(QPen(QColor("#d28c34"), 2))
            boundaries = (
                self.boundaries
                if self.boundaries is not None
                else [
                    index * self.seconds
                    for index in range(
                        1, min(2000, int(self.duration / self.seconds)) + 1
                    )
                ]
            )
            for boundary in boundaries:
                x = round(boundary / self.duration * self.width())
                painter.drawLine(x, 0, x, self.height())
        painter.end()


class AudioPage(QWidget):
    def __init__(self, store, settings, collection, jobs_added):
        super().__init__()
        self.store, self.settings, self.collection, self.jobs_added = (
            store,
            settings,
            collection,
            jobs_added,
        )
        self.sources = []
        self.book = None
        # Reading, editing and splitting must also work without an audio device.
        # Create the native playback backend only when preview is requested.
        self.output_audio = None
        self.player = None
        layout = QVBoxLayout(self)
        layout.addWidget(
            heading(
                "Audio aufteilen",
                "Dateien verbinden, vorhören und in frei wählbare Abschnitte schneiden",
            )
        )
        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(7)
        layout.addWidget(self.splitter, 1)
        input_panel = QWidget()
        input_layout = QVBoxLayout(input_panel)
        input_layout.setContentsMargins(0, 0, 0, 0)
        self.splitter.addWidget(input_panel)
        input_layout.addWidget(
            row(
                button("Audiodateien hinzufügen", self.add_files),
                button("Aus Sammlung übernehmen", self.from_collection),
                button("Liste leeren", self.clear),
            )
        )
        input_layout.addWidget(
            QLabel(
                "Eingabe-Playlist · Dateien in Wiedergabereihenfolge (Drag-and-drop möglich)"
            )
        )
        self.sources_list = SourceList()
        self.sources_list.files_dropped.connect(self.add_paths)
        self.sources_list.reordered.connect(self.sources_reordered)
        self.sources_list.setToolTip(
            "Hier Audiodateien oder Ordner aus dem Explorer ablegen. Einträge innerhalb der Liste ziehen, um ihre Reihenfolge zu ändern. Dateien werden nicht verschoben."
        )
        self.sources_list.setMinimumHeight(60)
        self.sources_list.setUniformItemSizes(True)
        # Ordering is applied through the arrow controls, keeping the source model in sync.
        input_layout.addWidget(self.sources_list)
        input_layout.addWidget(
            row(
                button("Quelle ↑", lambda: self.move(-1)),
                button("Quelle ↓", lambda: self.move(1)),
                button("Quelle entfernen", self.remove),
                button("Wellenform laden", self.load_waveform),
            )
        )
        wave_panel = QWidget()
        wave_layout = QVBoxLayout(wave_panel)
        wave_layout.setContentsMargins(0, 0, 0, 0)
        self.splitter.addWidget(wave_panel)
        self.wave = Waveform()
        self.wave.setToolTip(
            "Wellenform der gesamten Eingabe-Playlist. Orange Linien zeigen geplante Schnittgrenzen; mit Wellenform laden berechnen."
        )
        wave_layout.addWidget(self.wave)
        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.sliderMoved.connect(self.seek_to)
        wave_layout.addWidget(
            row(
                button("▶ Vorhören", self.play),
                button("Pause", self.pause),
                button("Stopp", self.stop),
                self.seek,
            )
        )
        output_panel = QWidget()
        output_layout = QVBoxLayout(output_panel)
        output_layout.setContentsMargins(0, 0, 0, 0)
        self.splitter.addWidget(output_panel)
        form = QFormLayout()
        output_layout.addLayout(form)
        self.title = QLineEdit("Mein Hörbuch")
        self.length = QTimeEdit(QTime(0, 10, 0))
        self.length.setDisplayFormat("HH:mm:ss")
        self.length.setToolTip(
            "Gewünschte Länge jedes Abschnitts in Stunden, Minuten und Sekunden. Der letzte Abschnitt kann kürzer sein."
        )
        self.mode = QComboBox()
        self.mode.addItems(media.AUDIO_MODES)
        self.mode.setCurrentText(settings.audio_mode)
        self.mode.setToolTip(
            "Separate Dateien: feste Zeitabschnitte. TAF mit Kapiteln: Kapitel nach Zeit. "
            "TAF als Playlist: jede Quelldatei vollständig als Kapitel in Listenreihenfolge, ohne Kürzung. "
            "Für TAF wird Audio konvertiert; die Quellen bleiben unverändert."
        )
        self.target = QComboBox()
        self.target.addItems(["Nur lokal", "Nur TeddyCloud", "Lokal und TeddyCloud"])
        self.format = QComboBox()
        self.format.addItems(["mp3", "opus", "flac", "wav"])
        self.format.setCurrentText(settings.audio_format)
        self.destination = QLineEdit(settings.export)
        form.addRow("Titel", self.title)
        form.addRow("Abschnittslänge", self.length)
        form.addRow("Verarbeitung", self.mode)
        form.addRow("Ziel", self.target)
        form.addRow("Audioformat", self.format)
        form.addRow("Exportordner", path_control(self.destination, self))
        output_layout.addWidget(
            QLabel("Ausgabevorschau · geplante Abschnitte (keine zweite Playlist)")
        )
        self.parts = QTableWidget(0, 5)
        self.parts.setToolTip(
            "Schnittplan für die zusammenhängende Eingabe-Playlist. Erst Auftrag erstellen und anschließend ausführen erzeugt Dateien."
        )
        self.parts.setHorizontalHeaderLabels(
            ["Teil", "Start", "Dauer", "Dateiname", "Enthaltene Quelldateien"]
        )
        self.parts.horizontalHeader().setStretchLastSection(False)
        self.parts.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Stretch
        )
        for column, width in ((0, 50), (1, 90), (2, 90), (4, 200)):
            self.parts.setColumnWidth(column, width)
        self.parts.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        output_layout.addWidget(self.parts, 1)
        self.splitter.setSizes([200, 130, 430])
        for index in (1, 2):
            self.splitter.handle(index).setToolTip(
                "Ziehen, um Playlist, Wellenform und Ausgabevorschau in der Höhe anzupassen."
            )
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(
            row(self.summary, button("Auftrag erstellen", self.create_job, True))
        )
        self.length.timeChanged.connect(self.update_plan)
        self.mode.currentIndexChanged.connect(self.update_plan)
        self.title.textChanged.connect(self.update_plan)
        self.format.currentTextChanged.connect(self.update_plan)
        self.length.setTime(QTime(0, 0).addSecs(min(86399, settings.split_seconds)))
        self.update_plan()

    def seconds(self):
        value = self.length.time()
        return value.hour() * 3600 + value.minute() * 60 + value.second()

    def add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Audiodateien",
            self.settings.source,
            "Audio (*.mp3 *.m4a *.m4b *.flac *.wav *.ogg *.opus *.aac *.wma)",
        )
        self.add_paths(paths)

    def sources_reordered(self):
        self.sources = [
            self.sources_list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.sources_list.count())
        ]
        self.update_plan()

    def add_paths(self, paths):
        from .domain import AUDIO_EXTENSIONS, natural_key

        expanded = []
        for name in paths:
            path = Path(name)
            expanded.extend(
                sorted(path.rglob("*"), key=natural_key) if path.is_dir() else [path]
            )
        existing = {str(Path(t.path).resolve()).casefold() for t in self.sources}
        paths = []
        for path in expanded:
            key = str(path.resolve()).casefold()
            if (
                path.is_file()
                and path.suffix.lower() in AUDIO_EXTENSIONS
                and key not in existing
            ):
                paths.append(str(path))
                existing.add(key)
        if not paths:
            return

        def inspect(_):
            return [
                Track(
                    p,
                    Path(p).stem,
                    duration=media.probe(p, self.settings.ffprobe)["duration"],
                )
                for p in paths
            ]

        def done(tracks):
            self.sources.extend(tracks)
            self.refresh_sources()
            self.book = None

        background(self, inspect, done)

    def from_collection(self):
        books = self.collection.selection()
        if not books:
            QMessageBox.information(
                self, "Audio", "Bitte zuerst Hörspiele in der Sammlung auswählen."
            )
            return
        self.sources = [track for book in books for track in book.tracks]
        self.book = Book.from_dict(books[0].to_dict()) if len(books) == 1 else None
        self.title.setText(books[0].title)
        self.refresh_sources()

    def refresh_sources(self):
        self.sources_list.clear()
        for track in self.sources:
            self.sources_list.addItem(
                f"{Path(track.path).name} · {clock(track.duration)}"
            )
            item = self.sources_list.item(self.sources_list.count() - 1)
            item.setData(Qt.ItemDataRole.UserRole, track)
            item.setToolTip(track.path)
        self.update_plan()

    def move(self, delta):
        index = self.sources_list.currentRow()
        target = index + delta
        if 0 <= target < len(self.sources):
            self.sources[index], self.sources[target] = (
                self.sources[target],
                self.sources[index],
            )
            self.refresh_sources()
            self.sources_list.setCurrentRow(target)

    def remove(self):
        index = self.sources_list.currentRow()
        if 0 <= index < len(self.sources):
            self.sources.pop(index)
            self.refresh_sources()

    def clear(self):
        self.stop()
        self.sources, self.book = [], None
        self.wave.values = []
        self.wave.duration = 0
        self.wave.update()
        self.refresh_sources()

    def working_book(self):
        book = (
            Book.from_dict(self.book.to_dict())
            if self.book
            else Book(
                uuid.uuid4().hex,
                str(Path(self.sources[0].path).parent) if self.sources else "",
                self.title.text(),
            )
        )
        book.title = self.title.text().strip()
        book.tracks = list(self.sources)
        return book

    def update_plan(self):
        playlist = self.mode.currentIndex() == 2
        self.length.setEnabled(not playlist)
        self.format.setEnabled(self.mode.currentIndex() == 0)
        duration = sum(t.duration for t in self.sources)
        self.wave.duration, self.wave.seconds = duration, max(1, self.seconds())
        self.wave.update()
        try:
            plan = media.audio_plan(
                [t.duration for t in self.sources],
                self.seconds(),
                self.mode.currentText(),
            )
            self.wave.boundaries = (
                [start for start, _ in plan[1:]] if playlist else None
            )
            book = self.working_book()
            self.parts.setRowCount(len(plan))
            ranges = []
            cursor = 0.0
            for source_index, track in enumerate(self.sources, 1):
                ranges.append((cursor, cursor + track.duration, source_index, track))
                cursor += track.duration
            for index, (start, length) in enumerate(plan):
                name = self.settings.profile().render(book, index + 1)["split_filename"]
                contributors = [
                    (a, b, n, t)
                    for a, b, n, t in ranges
                    if b > start and a < start + length
                ]
                source_text = ", ".join(str(n) for a, b, n, t in contributors)
                source_tip = "\n".join(
                    f"{n}. {Path(t.path).name}: {clock(max(0, start-a))} – {clock(min(t.duration, start+length-a))}"
                    for a, b, n, t in contributors
                )
                for column, text in enumerate(
                    [
                        str(index + 1),
                        clock(start),
                        clock(length),
                        (
                            self.settings.profile().render(book)["filename"]
                            if self.mode.currentIndex()
                            else name
                        )
                        + (
                            ".taf"
                            if self.mode.currentIndex()
                            else "." + self.format.currentText()
                        ),
                        source_text,
                    ]
                ):
                    self.parts.setItem(index, column, QTableWidgetItem(text))
                    self.parts.item(index, column).setToolTip(text)
                self.parts.item(index, 4).setToolTip(source_tip)
            self.summary.setText(
                f"{len(self.sources)} Quelldateien · {clock(duration)} gesamt → "
                + (
                    f"1 TAF mit {len(plan)} Kapiteln"
                    if self.mode.currentIndex()
                    else f"{len(plan)} Ausgabedateien"
                )
                + (
                    " · Jede Quelldatei vollständig als Kapitel · kein Zeitschnitt, keine Kürzung"
                    if playlist
                    else f" · Schnitt alle {clock(self.seconds())} · Dateien werden zuvor verbunden, nichts entfällt"
                )
            )
        except Exception as exc:
            self.parts.setRowCount(0)
            self.summary.setText(str(exc))

    def load_waveform(self):
        if not self.sources:
            return
        paths = [t.path for t in self.sources]

        def done(values):
            self.wave.values = values
            self.wave.update()

        background(self, lambda _: media.waveform(paths, self.settings.ffmpeg), done)

    def play(self):
        if not self.sources:
            return
        self.ensure_player()
        index = max(0, self.sources_list.currentRow())
        source = QUrl.fromLocalFile(self.sources[index].path)
        if self.player.source() != source:
            self.player.setSource(source)
        self.player.play()

    def ensure_player(self):
        if self.player is None:
            self.output_audio = QAudioOutput(self)
            self.player = QMediaPlayer(self)
            self.player.setAudioOutput(self.output_audio)
            self.output_audio.setVolume(0.6)
            self.player.durationChanged.connect(self.seek.setMaximum)
            self.player.positionChanged.connect(self.seek.setValue)
            self.player.errorOccurred.connect(
                lambda error, message: self.summary.setText("Vorhören: " + message)
            )

    def pause(self):
        if self.player is not None:
            self.player.pause()

    def stop(self):
        if self.player is not None:
            self.player.stop()

    def seek_to(self, position):
        if self.player is not None:
            self.player.setPosition(position)

    def create_job(self):
        if not self.sources or (self.mode.currentIndex() != 2 and self.seconds() <= 0):
            QMessageBox.warning(
                self,
                "Audio",
                "Audiodateien und eine Abschnittslänge größer als null wählen.",
            )
            return
        book = self.working_book()
        if not book.title:
            QMessageBox.warning(self, "Audio", "Bitte einen Titel angeben.")
            return
        payload = {
            "book": book.to_dict(),
            "settings": asdict(self.settings),
            "profile": asdict(self.settings.profile()),
            "audio": {
                "seconds": self.seconds(),
                "mode": self.mode.currentText(),
                "target": self.target.currentText(),
                "format": self.format.currentText(),
                "destination": self.destination.text(),
            },
        }
        self.store.add_job("Audio aufteilen", payload)
        self.jobs_added()
