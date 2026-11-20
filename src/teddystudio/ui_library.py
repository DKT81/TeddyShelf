"""Cover-card collection, folder/series grouping and explicit metadata editing."""

from .i18n import text as tr_text, format_text as _f
from .i18n_widgets import call as _ui, create as _new

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
    "title": tr_text("Titel"),
    "series": tr_text("Serie"),
    "episode": tr_text("Folge (leer = unbekannt)"),
    "kind": tr_text("Werktyp"),
    "album": tr_text("Album"),
    "albumartist": tr_text("Albuminterpret"),
    "artist": tr_text("Interpret / Sprecher"),
    "genre": tr_text("Genre"),
    "date": tr_text("Datum"),
    "disc": tr_text("Disk"),
    "language": tr_text("Sprache"),
    "category": tr_text("Kategorie"),
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
        self.remote_message = tr_text("Noch nicht abgeglichen")
        layout = QVBoxLayout(self)
        layout.addWidget(
            heading(
                tr_text("Deine Hörspielsammlung"),
                tr_text("Ordner einlesen · Titel auswählen · Daten prüfen"),
            )
        )
        self.search = _new(QLineEdit)
        _ui(self.search, "setPlaceholderText", tr_text("Titel oder Serie suchen …"))
        self.grouping = _new(QComboBox)
        _ui(
            self.grouping, "addItems", [tr_text("Nach Serien"), tr_text("Nach Ordnern")]
        )
        self.filter = _new(QComboBox)
        _ui(
            self.filter,
            "addItems",
            [
                tr_text("Alle Titel"),
                tr_text("Ohne Cover"),
                tr_text("Prüfen"),
                tr_text("Neu"),
                tr_text("Bestätigt"),
            ],
        )
        self.cloud_filter = _new(QComboBox)
        _ui(
            self.cloud_filter,
            "addItems",
            [
                tr_text("Cloud-Status: alle"),
                tr_text("Cloud"),
                tr_text("Lokal"),
                tr_text("Prüfen"),
            ],
        )
        _ui(
            self.cloud_filter,
            "setToolTip",
            tr_text(
                "Cloud: passendes Modell mit Audioreferenzen. Lokal: keine passende Modellkennung. Prüfen: ungeprüft, Verbindung fehlgeschlagen oder abweichende/mehrdeutige Daten."
            ),
        )
        layout.addWidget(
            row(
                button(tr_text("Ordner einlesen"), self.choose_scan, True),
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
        _ui(self.tree, "setHeaderLabel", tr_text("Serien / Ordner"))
        self.tree.setMinimumWidth(120)
        _ui(
            self.tree,
            "setToolTip",
            tr_text(
                "Ordner über die Pfeile auf- und zuklappen. Die rechte Bereichsgrenze ist verschiebbar."
            ),
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
        self.detail_title = _new(QLabel, tr_text("Custom-Tonie · Details"))
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
        _ui(tabs, "addTab", inspector, tr_text("Custom-Tonie & Metadaten"))
        self.edits = {}
        for index, (key, label) in enumerate(FIELDS.items()):
            edit = _new(QLineEdit)
            self.edits[key] = edit
            form.addWidget(_new(QLabel, label), index // 3, index % 3 * 2)
            form.addWidget(edit, index // 3, index % 3 * 2 + 1)
        self.preview = _new(QLabel)
        self.preview.setWordWrap(True)
        form.addWidget(self.preview, 4, 0, 1, 6)
        form.addWidget(
            row(
                button(tr_text("Änderungsvorschau"), self.metadata_preview),
                button(tr_text("Daten übernehmen"), self.save_details, True),
            ),
            5,
            0,
            1,
            6,
        )
        self.tracks = TrackTable()
        self.tracks.moved.connect(self.reorder_tracks)
        _ui(
            self.tracks,
            "setHorizontalHeaderLabels",
            [tr_text("Track"), tr_text("Titel"), tr_text("Dauer")],
        )
        self.tracks.horizontalHeader().setStretchLastSection(True)
        self.tracks.setColumnWidth(1, 560)
        self.tracks.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        _ui(
            self.tracks,
            "setToolTip",
            tr_text(
                "Spur auswählen und mit ↑ / ↓ verschieben. Titel und Tracknummer sind editierbar. Mit Reihenfolge bestätigen speichern."
            ),
        )
        track_panel = QWidget()
        track_layout = QVBoxLayout(track_panel)
        self.order_hint = _new(
            QLabel,
            tr_text("Automatisch erkannte Reihenfolgen benötigen keine Bestätigung."),
        )
        self.order_hint.setWordWrap(True)
        track_layout.addWidget(self.order_hint)
        track_layout.addWidget(self.tracks)
        self.confirm_order_button = button(
            tr_text("Reihenfolge bestätigen"), self.confirm_order
        )
        track_layout.addWidget(
            row(
                button(tr_text("Spur ↑"), lambda: self.move_track(-1)),
                button(tr_text("Spur ↓"), lambda: self.move_track(1)),
                self.confirm_order_button,
            )
        )
        _ui(tabs, "addTab", track_panel, tr_text("Spuren & Reihenfolge"))
        form.addWidget(
            button(tr_text("Tags in Quelldateien schreiben …"), self.write_tags),
            6,
            0,
            1,
            6,
        )
        # B layout: cover library above, detailed inspector below.
        self.vertical_split.addWidget(details)
        self.vertical_split.setSizes([480, 330])
        for splitter in (split, self.vertical_split):
            splitter.setChildrenCollapsible(False)
            splitter.setHandleWidth(7)
            _ui(
                splitter.handle(1),
                "setToolTip",
                tr_text("Ziehen, um die Bereiche zu vergrößern oder zu verkleinern."),
            )
        split.setSizes([210, 1000])
        self.status = _new(QLabel)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        layout.addWidget(
            row(
                button(tr_text("Alle sichtbaren auswählen"), self.cards.selectAll),
                button(tr_text("Serienzuordnung ändern …"), self.regroup),
                button(tr_text("Sammlung exportieren"), self.export_collection),
                button(
                    tr_text("TeddyCloud abgleichen"),
                    self.sync_remote,
                    tooltip=tr_text(
                        "Custom-Tonie-Datenbank nur lesen und Modellbestand vergleichen; keine Uploads oder RFID-Zuordnungen."
                    ),
                ),
                button(
                    tr_text("In TeddyCloud übertragen"),
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
            self.remote_message = tr_text(
                "Bitte zuerst die TeddyCloud-Adresse in den Einstellungen eintragen"
            )
            self.refresh_cards()
            return
        if getattr(self, "syncing", False):
            return
        self.syncing = True
        self.remote_message = tr_text("TeddyCloud wird gelesen …")
        self.refresh_cards()

        def read(_):
            from .client import TeddyCloud

            try:
                with TeddyCloud(self.settings) as client:
                    return (
                        client.models(),
                        tr_text("Modellbestand gelesen; Audio nicht byteweise geprüft"),
                    )
            except Exception as exc:
                return None, tr_text("Abgleich fehlgeschlagen: ") + str(exc)

        def done(result):
            self.remote_models, self.remote_message = result
            self.syncing = False
            self.refresh_cards()

        background(self, read, done)

    def remote_label(self, book):
        if self.remote_models is None:
            return tr_text("? Nicht geprüft")
        try:
            model = book.remote_model or self.settings.profile().render(book)["model"]
        except Exception:
            return tr_text("? Benennung prüfen")
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
                        tr_text("? Bestand unvollständig")
                        if snapshot["warnings"]
                        else tr_text("⚠ Audiodatei im Bestand fehlt")
                    )
        matches = [m for m in models if m.get("model") == model]
        if not matches:
            if book.remote_model:
                return tr_text("? Modell-/Dateibestand prüfen")
            return tr_text("○ Kein passendes Online-Modell")
        if len(matches) != 1:
            return tr_text("⚠ Modellkennung mehrfach")
        entry = matches[0]
        naming = self.settings.profile().render(book)
        if any(
            str(entry.get(key, "")) != str(naming.get(key, ""))
            for key in ("title", "series", "episodes")
        ):
            return tr_text("⚠ Online-Metadaten abweichend")
        return (
            tr_text("● Modell online")
            if entry.get("audio_id") and entry.get("hash")
            else tr_text("⚠ Modell ohne Audioreferenz")
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
        all_item = _new(QTreeWidgetItem, [tr_text("Alle")])
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
            item = _new(QTreeWidgetItem, [path.name or key])
            item.setData(0, Qt.ItemDataRole.UserRole, key)
            _ui(item, "setToolTip", 0, key)
            (parent.addChild(item) if parent else self.tree.addTopLevelItem(item))
            nodes[key] = item
            return item

        for group in sorted({self.group(b) for b in self.books}, key=str.casefold):
            if self.grouping.currentIndex() == 1:
                item = folder_node(Path(group))
            else:
                item = _new(QTreeWidgetItem, [group])
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
            (book.series or tr_text("Einzelhörspiele"))
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
            if filter_value == tr_text("Ohne Cover") and book.cover:
                continue
            if (
                filter_value not in {tr_text("Alle Titel"), tr_text("Ohne Cover")}
                and book.status != filter_value
            ):
                continue
            suffix = (
                _f(" · Folge {p0:03}", p0=book.episode)
                if book.episode is not None
                else ""
            )
            item = _new(
                QListWidgetItem,
                self.cloud_icon(book),
                _f(
                    "{p0}\n{p1}{p2}\n{p3} · {p4}\n{p5}",
                    p0=book.title,
                    p1=book.series,
                    p2=suffix,
                    p3=clock(book.duration),
                    p4=tr_text(book.status),
                    p5=self.remote_label(book),
                ),
            )
            item.setData(Qt.ItemDataRole.UserRole, book.id)
            _ui(
                item,
                "setToolTip",
                book.folder
                + "\n"
                + book.origin
                + "\n"
                + self.remote_label(book)
                + "\n"
                + self.remote_message,
            )
            _ui(self.cards, "addItem", item)
            if book.id == current_id:
                self.cards.setCurrentItem(item)
            item.setSelected(book.id in selected_ids)
        self.cards.blockSignals(False)
        _ui(
            self.status,
            "setText",
            _f(
                "{p0} sichtbare Hörspiele · {p1} insgesamt · {p2}",
                p0=self.cards.count(),
                p1=len(self.books),
                p2=tr_text(self.remote_message),
            ),
        )

    def cloud_state(self, book):
        label = self.remote_label(book)
        return (
            tr_text("Cloud")
            if label.startswith("●")
            else tr_text("Lokal") if label.startswith("○") else tr_text("Prüfen")
        )

    def cloud_icon(self, book):
        """Always show a high-contrast badge, including the unknown/error state."""
        label = self.remote_label(book)
        if "●" in label:
            text, color = tr_text("CLOUD"), "#087a44"
        elif "○" in label:
            text, color = tr_text("LOKAL"), "#536779"
        elif "⚠" in label:
            text, color = tr_text("PRÜFEN"), "#a85b00"
        else:
            text, color = tr_text("CLOUD ?"), "#536779"
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
        _ui(painter, "drawText", 62, 6, 92, 25, Qt.AlignmentFlag.AlignCenter, text)
        painter.end()
        return QIcon(square)

    def show_book(self, item, previous=None):
        if not item:
            return
        self.current = next(
            b for b in self.books if b.id == item.data(Qt.ItemDataRole.UserRole)
        )
        needs_confirmation = self.current.order_pending or any(
            t.error.startswith(tr_text("Playlist und Audiodateien"))
            for t in self.current.tracks
        )
        self.confirm_order_button.setEnabled(needs_confirmation)
        _ui(
            self.order_hint,
            "setText",
            (
                tr_text(
                    "Reihenfolge geändert oder uneindeutig: bitte prüfen und bestätigen."
                )
                if needs_confirmation
                else tr_text(
                    "Reihenfolge unverändert / erkannt – direkt importierbar, keine Bestätigung nötig."
                )
            ),
        )
        self.loading = True
        for key, edit in self.edits.items():
            value = getattr(self.current, key)
            _ui(edit, "setText", "" if value is None else str(value))
        self.tracks.setRowCount(len(self.current.tracks))
        for index, track in enumerate(self.current.tracks):
            for column, text in enumerate(
                [track.track, track.title, clock(track.duration)]
            ):
                cell = _new(QTableWidgetItem, text)
                if column == 2:
                    cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
                _ui(
                    cell,
                    "setToolTip",
                    track.path + ("\n" + track.error if track.error else ""),
                )
                self.tracks.setItem(index, column, cell)
        self.loading = False
        self.update_preview()
        self.selected.emit(self.current)

    def edited_book(self):
        if not self.current:
            raise ValueError(tr_text("Bitte ein Hörspiel auswählen."))
        book = Book.from_dict(self.current.to_dict())
        for key, edit in self.edits.items():
            value = edit.text().strip()
            if key == "episode":
                value = int(value) if value else None
                if value is not None and value < 0:
                    raise ValueError(tr_text("Folge muss positiv sein."))
            setattr(book, key, value)
        if not book.title:
            raise ValueError(tr_text("Titel darf nicht leer sein."))
        for index, track in enumerate(book.tracks):
            track.track = self.tracks.item(index, 0).text()
            track.title = self.tracks.item(index, 1).text()
        return book

    def update_preview(self):
        if self.loading or not self.current:
            return
        try:
            rendered = self.settings.profile().render(self.edited_book())
            _ui(
                self.preview,
                "setText",
                _f(
                    "Modell: {p0} · {p1}",
                    p0=rendered["model"],
                    p1=tr_text(self.current.origin),
                ),
            )
            _ui(
                self.preview,
                "setToolTip",
                _f(
                    "Audio: {p0}/{p1}.taf",
                    p0=rendered["audio_folder"],
                    p1=rendered["filename"],
                ),
            )
        except Exception as exc:
            _ui(self.preview, "setText", str(exc))

    def metadata_preview(self):
        try:
            edited = self.edited_book()
            changes = [
                _f(
                    "{p0}: {p1!s} → {p2!s}",
                    p0=label,
                    p1=getattr(self.current, key),
                    p2=getattr(edited, key),
                )
                for key, label in FIELDS.items()
                if getattr(self.current, key) != getattr(edited, key)
            ]
            _ui(
                QMessageBox,
                "information",
                self,
                tr_text("Änderungsvorschau"),
                tr_text("\n").join(changes) or tr_text("Keine Änderungen."),
            )
        except Exception as exc:
            _ui(QMessageBox, "warning", self, tr_text("Metadaten"), str(exc))

    def save_details(self):
        try:
            book = self.edited_book()
            self.settings.profile().render(book)
            book.origin = tr_text("Manuell bearbeitete Metadaten")
            self.books[self.books.index(self.current)] = book
            self.current = book
            self.store.save_book(book)
            self.refresh()
            self.changed.emit()
        except Exception as exc:
            _ui(QMessageBox, "warning", self, tr_text("Metadaten"), str(exc))

    def confirm_order(self):
        try:
            book = self.edited_book()
            for track in book.tracks:
                if track.error.startswith(tr_text("Playlist und Audiodateien")):
                    track.error = ""
            book.origin = tr_text("Manuell bestätigte Spurenreihenfolge")
            book.order_pending = False
            book.confirmed_order = [t.path for t in book.tracks]
            book.status = (
                tr_text("Prüfen")
                if any(track.error for track in book.tracks)
                else tr_text("Neu")
            )
            self.books[self.books.index(self.current)] = book
            self.current = book
            self.store.save_book(book)
            self.refresh()
            self.changed.emit()
        except Exception as exc:
            _ui(QMessageBox, "warning", self, tr_text("Reihenfolge"), str(exc))

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
        series, ok = _ui(
            QInputDialog,
            "getText",
            self,
            tr_text("Serienzuordnung"),
            _f(
                "Serie für {p0} Hörspiele (Folgenummern bleiben unverändert):",
                p0=len(books),
            ),
        )
        if ok:
            _ui(
                QMessageBox,
                "information",
                self,
                tr_text("Vorschau"),
                tr_text("\n").join(
                    _f(
                        "{p0}: {p1} → {p2}",
                        p0=b.title,
                        p1=b.series or tr_text("Einzeln"),
                        p2=series or tr_text("Einzeln"),
                    )
                    for b in books
                ),
            )
            for book in books:
                book.series, book.origin = series.strip(), tr_text(
                    "Manuelle Serienzuordnung"
                )
                self.store.save_book(book)
            self.refresh()
            self.changed.emit()

    def write_tags(self):
        try:
            book = self.edited_book()
        except Exception as exc:
            _ui(QMessageBox, "warning", self, tr_text("Tags"), str(exc))
            return
        preview = tr_text("\n").join(
            _f("{p0} · Track {p1}", p0=t.title, p1=t.track or tr_text("leer"))
            for t in book.tracks
        )
        preview = (
            tr_text("\n").join(
                _f(
                    "{p0}: {p1}",
                    p0=FIELDS[key],
                    p1=getattr(book, key) or tr_text("(leer)"),
                )
                for key in ["album", "albumartist", "artist", "genre", "date", "disc"]
            )
            + "\n\n"
            + preview
        )
        if (
            _ui(
                QMessageBox,
                "question",
                self,
                tr_text("Quelldateien ändern"),
                _f(
                    "Tags für {p0} Dateien schreiben? Vor jeder Datei wird eine Sicherung angelegt.\nFolge bleibt getrennt von Track.\n\n{p1}",
                    p0=len(book.tracks),
                    p1=preview,
                ),
            )
            != QMessageBox.StandardButton.Yes
        ):
            return

        def saved(paths):
            book.origin = tr_text("Manuell bearbeitete und geschriebene Audiotags")
            for index, previous in enumerate(self.books):
                if previous.id == book.id:
                    self.books[index] = book
                    break
            self.store.save_book(book)
            if self.current and self.current.id == book.id:
                self.current = book
            self.refresh()
            self.changed.emit()
            _ui(
                QMessageBox,
                "information",
                self,
                tr_text("Tags gespeichert"),
                _f(
                    "{p0} Dateien aktualisiert. Sicherungen liegen neben den Quellen.",
                    p0=len(paths),
                ),
            )

        background(
            self, lambda _: library.write_tags(book, self.settings.embed_cover), saved
        )

    def choose_scan(self):
        path = _ui(
            QFileDialog,
            "getExistingDirectory",
            self,
            tr_text("Hörspielordner mit Unterordnern"),
            self.settings.source,
        )
        if path:
            self.start_scan(path)

    def start_scan(self, path):
        _ui(self.status, "setText", tr_text("Sammlung wird eingelesen …"))
        task = background(
            self,
            lambda progress: library.scan(
                path, self.settings, data_directory() / "covers", progress
            ),
            self.scanned,
        )
        task.signals.progress.connect(
            lambda message: _ui(self.status, "setText", tr_text(message))
        )
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
                    if tr_text("bestätigte Spurenreihenfolge") in previous.origin
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
                    tr_text("Manuell") in previous.origin
                    or previous.origin.startswith(tr_text("Aus TeddyCloud"))
                    or previous.order_pending
                    or confirmed
                ):
                    refreshed = {t.path: t for t in book.tracks}
                    for track in previous.tracks:
                        fresh = refreshed.pop(track.path, None)
                        if fresh:
                            if keep_confirmation and fresh.error.startswith(
                                tr_text("Playlist und Audiodateien")
                            ):
                                fresh.error = ""
                            track.duration, track.error, track.tags = (
                                fresh.duration,
                                fresh.error,
                                fresh.tags,
                            )
                        else:
                            track.error = tr_text("Quelldatei nicht gefunden")
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
        path, _ = _ui(
            QFileDialog,
            "getSaveFileName",
            self,
            tr_text("Sammlung exportieren"),
            "Sammlung.json",
            tr_text("JSON (*.json)"),
        )
        if path:
            atomic_json(Path(path), [b.to_dict() for b in self.books])
