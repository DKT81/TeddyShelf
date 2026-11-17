"""Cover-card collection, folder/series grouping and explicit metadata editing."""

from dataclasses import asdict
from pathlib import Path
import json

from PySide6.QtCore import Qt, QSize, Signal, QTimer
from PySide6.QtGui import QIcon, QPainter, QColor, QFont
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QSplitter,
    QLineEdit,
    QComboBox,
    QLabel,
    QFormLayout,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QFileDialog,
    QMessageBox,
    QInputDialog,
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QPlainTextEdit,
    QGridLayout,
    QTabWidget,
    QScrollArea,
    QLayout,
)

from .domain import Book
from . import library
from .storage import data_directory, atomic_json
from .ui_common import background, button, cover_pixmap, clock, heading, row

FIELDS = {
    "title": "Titel",
    "series": "Serie",
    "episode": "Folge (leer = unbekannt)",
    "kind": "Werktyp",
    "album": "Album",
    "albumartist": "Albuminterpret",
    "artist": "Interpret / Sprecher",
    "genre": "Genre",
    "date": "Datum",
    "disc": "Disk",
    "language": "Sprache",
    "category": "Kategorie",
}


class TrackTable(QTableWidget):
    """Reorder whole rows; Qt's default table drop can overwrite individual cells."""

    moved = Signal(int, int)

    def __init__(self):
        super().__init__(0, 3)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)

    def dropEvent(self, event):
        source = self.currentRow()
        target = self.rowAt(int(event.position().y()))
        if target < 0:
            target = self.rowCount() - 1
        if event.source() is self and source >= 0 and target >= 0:
            self.moved.emit(source, target)
            event.acceptProposedAction()
        else:
            event.ignore()


class LibraryPage(QWidget):
    selected = Signal(object)
    changed = Signal()
    import_requested = Signal(object)

    def __init__(self, store, settings):
        super().__init__()
        self.store, self.settings = store, settings
        self.books = store.books()
        self.current = None
        self._expanded_folders = set()
        self.loading = False
        self.remote_models = None
        self.server_inventory = None
        self.remote_message = "Noch nicht abgeglichen"
        layout = QVBoxLayout(self)
        layout.addWidget(
            heading(
                "Deine Hörspielsammlung",
                "Ordner einlesen · Titel auswählen · Daten prüfen",
            )
        )
        self.search = QLineEdit()
        self.search.setPlaceholderText("Titel oder Serie suchen …")
        self.grouping = QComboBox()
        self.grouping.addItems(["Nach Serien", "Nach Ordnern"])
        self.filter = QComboBox()
        self.filter.addItems(["Alle Titel", "Ohne Cover", "Prüfen", "Neu", "Bestätigt"])
        self.cloud_filter = QComboBox()
        self.cloud_filter.addItems(["Cloud-Status: alle", "Cloud", "Lokal", "Prüfen"])
        self.cloud_filter.setToolTip(
            "Cloud: passendes Modell mit Audioreferenzen. Lokal: keine passende Modellkennung. Prüfen: ungeprüft, Verbindung fehlgeschlagen oder abweichende/mehrdeutige Daten."
        )
        layout.addWidget(
            row(
                button("Ordner einlesen", self.choose_scan, True),
                self.search,
                self.grouping,
                self.filter,
                self.cloud_filter,
            )
        )
        self.vertical_split = QSplitter(Qt.Orientation.Vertical)
        layout.addWidget(self.vertical_split, 1)
        split = QSplitter()
        self.vertical_split.addWidget(split)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabel("Serien / Ordner")
        self.tree.setMinimumWidth(120)
        self.tree.setToolTip(
            "Ordner über die Pfeile auf- und zuklappen. Die rechte Bereichsgrenze ist verschiebbar."
        )
        self.cards = QListWidget()
        self.cards.setObjectName("coverCards")
        self.cards.setViewMode(QListWidget.ViewMode.IconMode)
        self.cards.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.cards.setIconSize(QSize(150, 150))
        self.cards.setGridSize(QSize(205, 255))
        self.cards.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.cards.setWordWrap(True)
        split.addWidget(self.tree)
        split.addWidget(self.cards)
        details = QWidget()
        details.setMinimumHeight(180)
        details_layout = QVBoxLayout(details)
        self.detail_title = QLabel("Custom-Tonie · Details")
        details_layout.addWidget(self.detail_title)
        tabs = QTabWidget()
        details_layout.addWidget(tabs)
        metadata = QWidget()
        form = QGridLayout(metadata)
        form.setAlignment(Qt.AlignmentFlag.AlignTop)
        form.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        inspector = QScrollArea()
        inspector.setWidgetResizable(True)
        inspector.setWidget(metadata)
        tabs.addTab(inspector, "Custom-Tonie & Metadaten")
        self.edits = {}
        for index, (key, label) in enumerate(FIELDS.items()):
            edit = QLineEdit()
            self.edits[key] = edit
            form.addWidget(QLabel(label), index // 3, index % 3 * 2)
            form.addWidget(edit, index // 3, index % 3 * 2 + 1)
        self.preview = QLabel()
        self.preview.setWordWrap(True)
        form.addWidget(self.preview, 4, 0, 1, 6)
        form.addWidget(
            row(
                button("Änderungsvorschau", self.metadata_preview),
                button("Daten übernehmen", self.save_details, True),
            ),
            5,
            0,
            1,
            6,
        )
        self.tracks = TrackTable()
        self.tracks.moved.connect(self.reorder_tracks)
        self.tracks.setHorizontalHeaderLabels(["Track", "Titel", "Dauer"])
        self.tracks.horizontalHeader().setStretchLastSection(True)
        self.tracks.setColumnWidth(1, 560)
        self.tracks.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tracks.setToolTip(
            "Spur auswählen und mit ↑ / ↓ verschieben. Titel und Tracknummer sind editierbar. Mit Reihenfolge bestätigen speichern."
        )
        track_panel = QWidget()
        track_layout = QVBoxLayout(track_panel)
        self.order_hint = QLabel(
            "Automatisch erkannte Reihenfolgen benötigen keine Bestätigung."
        )
        self.order_hint.setWordWrap(True)
        track_layout.addWidget(self.order_hint)
        track_layout.addWidget(self.tracks)
        self.confirm_order_button = button("Reihenfolge bestätigen", self.confirm_order)
        track_layout.addWidget(
            row(
                button("Spur ↑", lambda: self.move_track(-1)),
                button("Spur ↓", lambda: self.move_track(1)),
                self.confirm_order_button,
            )
        )
        tabs.addTab(track_panel, "Spuren & Reihenfolge")
        form.addWidget(
            button("Tags in Quelldateien schreiben …", self.write_tags), 6, 0, 1, 6
        )
        # B layout: cover library above, detailed inspector below.
        self.vertical_split.addWidget(details)
        self.vertical_split.setSizes([480, 330])
        for splitter in (split, self.vertical_split):
            splitter.setChildrenCollapsible(False)
            splitter.setHandleWidth(7)
            splitter.handle(1).setToolTip(
                "Ziehen, um die Bereiche zu vergrößern oder zu verkleinern."
            )
        split.setSizes([210, 1000])
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        layout.addWidget(
            row(
                button("Alle sichtbaren auswählen", self.cards.selectAll),
                button("Serienzuordnung ändern …", self.regroup),
                button("Sammlung exportieren", self.export_collection),
                button(
                    "TeddyCloud abgleichen",
                    self.sync_remote,
                    tooltip="Custom-Tonie-Datenbank nur lesen und Modellbestand vergleichen; keine Uploads oder RFID-Zuordnungen.",
                ),
                button(
                    "In TeddyCloud übertragen",
                    lambda: self.import_requested.emit(self.selection()),
                    True,
                ),
            )
        )
        self.search.textChanged.connect(self.refresh_cards)
        self.filter.currentTextChanged.connect(self.refresh_cards)
        self.cloud_filter.currentTextChanged.connect(self.refresh_cards)
        self.grouping.currentTextChanged.connect(self.refresh)
        self.tree.currentItemChanged.connect(self.refresh_cards)
        self.tree.itemExpanded.connect(
            lambda item: self._expanded_folders.add(
                item.data(0, Qt.ItemDataRole.UserRole)
            )
        )
        self.tree.itemCollapsed.connect(
            lambda item: self._expanded_folders.discard(
                item.data(0, Qt.ItemDataRole.UserRole)
            )
        )
        self.cards.currentItemChanged.connect(self.show_book)
        for edit in self.edits.values():
            edit.textChanged.connect(self.update_preview)
        self.refresh()
        if self.books:
            QTimer.singleShot(0, self.sync_remote)

    def sync_remote(self):
        if not self.settings.url.strip():
            self.remote_models = None
            self.remote_message = (
                "Bitte zuerst die TeddyCloud-Adresse in den Einstellungen eintragen"
            )
            self.refresh_cards()
            return
        if getattr(self, "syncing", False):
            return
        self.syncing = True
        self.remote_message = "TeddyCloud wird gelesen …"
        self.refresh_cards()

        def read(_):
            from .client import TeddyCloud

            try:
                with TeddyCloud(self.settings) as client:
                    return (
                        client.models(),
                        "Modellbestand gelesen; Audio nicht byteweise geprüft",
                    )
            except Exception as exc:
                return None, "Abgleich fehlgeschlagen: " + str(exc)

        def done(result):
            self.remote_models, self.remote_message = result
            self.syncing = False
            self.refresh_cards()

        background(self, read, done)

    def remote_label(self, book):
        if self.remote_models is None:
            return "? Nicht geprüft"
        try:
            model = book.remote_model or self.settings.profile().render(book)["model"]
        except Exception:
            return "? Benennung prüfen"
        models = list(self.remote_models)
        snapshot = self.server_inventory
        if (
            snapshot
            and not snapshot.get("cached")
            and snapshot["url"] == self.settings.url.rstrip("/")
        ):
            for entry in snapshot["entries"]:
                if entry["available"]:
                    for candidate in entry.get("aliases", []):
                        if candidate.get("model") == model and not any(
                            m.get("model") == model for m in models
                        ):
                            models.append(candidate)
                elif entry.get("model", {}).get("model") == model:
                    return (
                        "? Bestand unvollständig"
                        if snapshot["warnings"]
                        else "⚠ Audiodatei im Bestand fehlt"
                    )
        matches = [m for m in models if m.get("model") == model]
        if not matches:
            if book.remote_model:
                return "? Modell-/Dateibestand prüfen"
            return "○ Kein passendes Online-Modell"
        if len(matches) != 1:
            return "⚠ Modellkennung mehrfach"
        entry = matches[0]
        naming = self.settings.profile().render(book)
        if any(
            str(entry.get(key, "")) != str(naming.get(key, ""))
            for key in ("title", "series", "episodes")
        ):
            return "⚠ Online-Metadaten abweichend"
        return (
            "● Modell online"
            if entry.get("audio_id") and entry.get("hash")
            else "⚠ Modell ohne Audioreferenz"
        )

    def selection(self):
        identifiers = {
            item.data(Qt.ItemDataRole.UserRole) for item in self.cards.selectedItems()
        }
        return [b for b in self.books if b.id in identifiers]

    def reorder_tracks(self, source, target):
        if not self.current or source == target:
            return
        book = self.edited_book()
        book.tracks.insert(target, book.tracks.pop(source))
        book.order_pending = True
        self.current = book
        self.books = [book if old.id == book.id else old for old in self.books]
        self.show_book(self.cards.currentItem())
        self.tracks.selectRow(target)

    def refresh(self):
        current = self.tree.currentItem()
        selected_group = current.data(0, Qt.ItemDataRole.UserRole) if current else None
        self.tree.blockSignals(True)
        self.tree.clear()
        all_item = QTreeWidgetItem(["Alle"])
        self.tree.addTopLevelItem(all_item)
        nodes = {}

        def folder_node(path):
            key = str(path)
            if key in nodes:
                return nodes[key]
            parent = (
                folder_node(path.parent)
                if path.parent != path and len(path.parts) > 1
                else None
            )
            item = QTreeWidgetItem([path.name or key])
            item.setData(0, Qt.ItemDataRole.UserRole, key)
            item.setToolTip(0, key)
            (parent.addChild(item) if parent else self.tree.addTopLevelItem(item))
            nodes[key] = item
            return item

        for group in sorted({self.group(b) for b in self.books}, key=str.casefold):
            if self.grouping.currentIndex() == 1:
                item = folder_node(Path(group))
            else:
                item = QTreeWidgetItem([group])
                item.setData(0, Qt.ItemDataRole.UserRole, group)
                self.tree.addTopLevelItem(item)
                nodes[group] = item
        self.tree.setCurrentItem(nodes.get(selected_group, all_item))
        for key, node in nodes.items():
            node.setExpanded(key in self._expanded_folders)
        self.tree.blockSignals(False)
        self.refresh_cards()

    def group(self, book):
        return (
            (book.series or "Einzelhörspiele")
            if self.grouping.currentIndex() == 0
            else book.folder
        )

    def refresh_cards(self, *args):
        current_id = self.current.id if self.current else ""
        selected_ids = {
            item.data(Qt.ItemDataRole.UserRole) for item in self.cards.selectedItems()
        }
        self.cards.blockSignals(True)
        self.cards.clear()
        group = (
            self.tree.currentItem().data(0, Qt.ItemDataRole.UserRole)
            if self.tree.currentItem()
            else None
        )
        query, filter_value = self.search.text().casefold(), self.filter.currentText()
        for book in self.books:
            if (
                self.cloud_filter.currentIndex()
                and self.cloud_state(book) != self.cloud_filter.currentText()
            ):
                continue
            if group and self.group(book) != group:
                if (
                    self.grouping.currentIndex() == 0
                    or Path(group) not in Path(book.folder).parents
                ):
                    continue
            if query not in (book.title + " " + book.series).casefold():
                continue
            if filter_value == "Ohne Cover" and book.cover:
                continue
            if (
                filter_value not in {"Alle Titel", "Ohne Cover"}
                and book.status != filter_value
            ):
                continue
            suffix = f" · Folge {book.episode:03}" if book.episode is not None else ""
            item = QListWidgetItem(
                self.cloud_icon(book),
                f"{book.title}\n{book.series}{suffix}\n{clock(book.duration)} · {book.status}\n{self.remote_label(book)}",
            )
            item.setData(Qt.ItemDataRole.UserRole, book.id)
            item.setToolTip(
                book.folder
                + "\n"
                + book.origin
                + "\n"
                + self.remote_label(book)
                + "\n"
                + self.remote_message
            )
            self.cards.addItem(item)
            if book.id == current_id:
                self.cards.setCurrentItem(item)
            item.setSelected(book.id in selected_ids)
        self.cards.blockSignals(False)
        self.status.setText(
            f"{self.cards.count()} sichtbare Hörspiele · {len(self.books)} insgesamt · {self.remote_message}"
        )

    def cloud_state(self, book):
        label = self.remote_label(book)
        return (
            "Cloud"
            if label.startswith("●")
            else "Lokal" if label.startswith("○") else "Prüfen"
        )

    def cloud_icon(self, book):
        """Always show a high-contrast badge, including the unknown/error state."""
        label = self.remote_label(book)
        if "●" in label:
            text, color = "CLOUD", "#087a44"
        elif "○" in label:
            text, color = "LOKAL", "#536779"
        elif "⚠" in label:
            text, color = "PRÜFEN", "#a85b00"
        else:
            text, color = "CLOUD ?", "#536779"
        pixmap = cover_pixmap(book.cover, 160)
        # Non-square covers still receive a fixed square backing for a legible badge.
        from PySide6.QtGui import QPixmap

        square = QPixmap(160, 160)
        square.fill(QColor("#e6ece9"))
        painter = QPainter(square)
        painter.drawPixmap(
            (160 - pixmap.width()) // 2, (160 - pixmap.height()) // 2, pixmap
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        painter.drawRoundedRect(62, 6, 92, 25, 5, 5)
        painter.setPen(QColor("white"))
        font = QFont()
        font.setPixelSize(12)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(62, 6, 92, 25, Qt.AlignmentFlag.AlignCenter, text)
        painter.end()
        return QIcon(square)

    def show_book(self, item, previous=None):
        if not item:
            return
        self.current = next(
            b for b in self.books if b.id == item.data(Qt.ItemDataRole.UserRole)
        )
        needs_confirmation = self.current.order_pending or any(
            t.error.startswith("Playlist und Audiodateien") for t in self.current.tracks
        )
        self.confirm_order_button.setEnabled(needs_confirmation)
        self.order_hint.setText(
            "Reihenfolge geändert oder uneindeutig: bitte prüfen und bestätigen."
            if needs_confirmation
            else "Reihenfolge unverändert / erkannt – direkt importierbar, keine Bestätigung nötig."
        )
        self.loading = True
        for key, edit in self.edits.items():
            value = getattr(self.current, key)
            edit.setText("" if value is None else str(value))
        self.tracks.setRowCount(len(self.current.tracks))
        for index, track in enumerate(self.current.tracks):
            for column, text in enumerate(
                [track.track, track.title, clock(track.duration)]
            ):
                cell = QTableWidgetItem(text)
                if column == 2:
                    cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
                cell.setToolTip(
                    track.path + ("\n" + track.error if track.error else "")
                )
                self.tracks.setItem(index, column, cell)
        self.loading = False
        self.update_preview()
        self.selected.emit(self.current)

    def edited_book(self):
        if not self.current:
            raise ValueError("Bitte ein Hörspiel auswählen.")
        book = Book.from_dict(self.current.to_dict())
        for key, edit in self.edits.items():
            value = edit.text().strip()
            if key == "episode":
                value = int(value) if value else None
                if value is not None and value < 0:
                    raise ValueError("Folge muss positiv sein.")
            setattr(book, key, value)
        if not book.title:
            raise ValueError("Titel darf nicht leer sein.")
        for index, track in enumerate(book.tracks):
            track.track = self.tracks.item(index, 0).text()
            track.title = self.tracks.item(index, 1).text()
        return book

    def update_preview(self):
        if self.loading or not self.current:
            return
        try:
            rendered = self.settings.profile().render(self.edited_book())
            self.preview.setText(f"Modell: {rendered['model']} · {self.current.origin}")
            self.preview.setToolTip(
                f"Audio: {rendered['audio_folder']}/{rendered['filename']}.taf"
            )
        except Exception as exc:
            self.preview.setText(str(exc))

    def metadata_preview(self):
        try:
            edited = self.edited_book()
            changes = [
                f"{label}: {getattr(self.current, key)!s} → {getattr(edited, key)!s}"
                for key, label in FIELDS.items()
                if getattr(self.current, key) != getattr(edited, key)
            ]
            QMessageBox.information(
                self, "Änderungsvorschau", "\n".join(changes) or "Keine Änderungen."
            )
        except Exception as exc:
            QMessageBox.warning(self, "Metadaten", str(exc))

    def save_details(self):
        try:
            book = self.edited_book()
            self.settings.profile().render(book)
            book.origin = "Manuell bearbeitete Metadaten"
            self.books[self.books.index(self.current)] = book
            self.current = book
            self.store.save_book(book)
            self.refresh()
            self.changed.emit()
        except Exception as exc:
            QMessageBox.warning(self, "Metadaten", str(exc))

    def confirm_order(self):
        try:
            book = self.edited_book()
            for track in book.tracks:
                if track.error.startswith("Playlist und Audiodateien"):
                    track.error = ""
            book.origin = "Manuell bestätigte Spurenreihenfolge"
            book.order_pending = False
            book.confirmed_order = [t.path for t in book.tracks]
            book.status = (
                "Prüfen" if any(track.error for track in book.tracks) else "Neu"
            )
            self.books[self.books.index(self.current)] = book
            self.current = book
            self.store.save_book(book)
            self.refresh()
            self.changed.emit()
        except Exception as exc:
            QMessageBox.warning(self, "Reihenfolge", str(exc))

    def move_track(self, delta):
        if not self.current:
            return
        index = self.tracks.currentRow()
        target = index + delta
        if 0 <= target < len(self.current.tracks):
            book = self.edited_book()
            book.tracks[index], book.tracks[target] = (
                book.tracks[target],
                book.tracks[index],
            )
            book.order_pending = True
            self.books[self.books.index(self.current)] = book
            self.current = book
            item = self.cards.currentItem()
            self.show_book(item)
            self.tracks.setCurrentCell(target, 0)

    def regroup(self):
        books = self.selection()
        if not books:
            return
        series, ok = QInputDialog.getText(
            self,
            "Serienzuordnung",
            f"Serie für {len(books)} Hörspiele (Folgenummern bleiben unverändert):",
        )
        if ok:
            QMessageBox.information(
                self,
                "Vorschau",
                "\n".join(
                    f"{b.title}: {b.series or 'Einzeln'} → {series or 'Einzeln'}"
                    for b in books
                ),
            )
            for book in books:
                book.series, book.origin = series.strip(), "Manuelle Serienzuordnung"
                self.store.save_book(book)
            self.refresh()
            self.changed.emit()

    def write_tags(self):
        try:
            book = self.edited_book()
        except Exception as exc:
            QMessageBox.warning(self, "Tags", str(exc))
            return
        preview = "\n".join(
            f"{t.title} · Track {t.track or 'leer'}" for t in book.tracks
        )
        preview = (
            "\n".join(
                f"{FIELDS[key]}: {getattr(book, key) or '(leer)'}"
                for key in ["album", "albumartist", "artist", "genre", "date", "disc"]
            )
            + "\n\n"
            + preview
        )
        if (
            QMessageBox.question(
                self,
                "Quelldateien ändern",
                f"Tags für {len(book.tracks)} Dateien schreiben? Vor jeder Datei wird eine Sicherung angelegt.\nFolge bleibt getrennt von Track.\n\n{preview}",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return

        def saved(paths):
            book.origin = "Manuell bearbeitete und geschriebene Audiotags"
            for index, previous in enumerate(self.books):
                if previous.id == book.id:
                    self.books[index] = book
                    break
            self.store.save_book(book)
            if self.current and self.current.id == book.id:
                self.current = book
            self.refresh()
            self.changed.emit()
            QMessageBox.information(
                self,
                "Tags gespeichert",
                f"{len(paths)} Dateien aktualisiert. Sicherungen liegen neben den Quellen.",
            )

        background(
            self, lambda _: library.write_tags(book, self.settings.embed_cover), saved
        )

    def choose_scan(self):
        path = QFileDialog.getExistingDirectory(
            self, "Hörspielordner mit Unterordnern", self.settings.source
        )
        if path:
            self.start_scan(path)

    def start_scan(self, path):
        self.status.setText("Sammlung wird eingelesen …")
        task = background(
            self,
            lambda progress: library.scan(
                path, self.settings, data_directory() / "covers", progress
            ),
            self.scanned,
        )
        task.signals.progress.connect(self.status.setText)
        self.settings.last_collection = path
        self.settings.save()

    def scanned(self, books):
        old = {b.id: b for b in self.books}
        by_folder = {
            str(Path(b.folder).resolve()).casefold(): b for b in self.books if b.folder
        }
        for book in books:
            match = by_folder.get(str(Path(book.folder).resolve()).casefold())
            if match:
                book.id = match.id
            if book.id in old:
                previous = old[book.id]
                old_paths = [t.path for t in previous.tracks]
                new_paths = [t.path for t in book.tracks]
                confirmed = previous.confirmed_order or (
                    old_paths
                    if "bestätigte Spurenreihenfolge" in previous.origin
                    else []
                )
                same_sources = set(old_paths) == set(new_paths)
                keep_confirmation = (
                    same_sources
                    and confirmed == old_paths
                    and not previous.order_pending
                )
                # Keep deliberate edits and ordering while refreshing file availability.
                if (
                    "Manuell" in previous.origin
                    or previous.origin.startswith("Aus TeddyCloud")
                    or previous.order_pending
                    or confirmed
                ):
                    refreshed = {t.path: t for t in book.tracks}
                    for track in previous.tracks:
                        fresh = refreshed.pop(track.path, None)
                        if fresh:
                            if keep_confirmation and fresh.error.startswith(
                                "Playlist und Audiodateien"
                            ):
                                fresh.error = ""
                            track.duration, track.error, track.tags = (
                                fresh.duration,
                                fresh.error,
                                fresh.tags,
                            )
                        else:
                            track.error = "Quelldatei nicht gefunden"
                    previous.tracks += list(refreshed.values())
                    previous.confirmed_order = confirmed if keep_confirmation else []
                    previous.order_pending = previous.order_pending or bool(
                        confirmed and not same_sources
                    )
                    book = previous
                if self.settings.preserve_manual_cover and previous.cover_confirmed:
                    book.cover, book.cover_source, book.cover_confirmed = (
                        previous.cover,
                        previous.cover_source,
                        True,
                    )
            old[book.id] = book
        self.books = list(old.values())
        self.store.save_books(self.books)
        self.refresh()
        self.changed.emit()
        self.sync_remote()

    def export_collection(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Sammlung exportieren", "Sammlung.json", "JSON (*.json)"
        )
        if path:
            atomic_json(Path(path), [b.to_dict() for b in self.books])
