"""Per-work cover confirmation, manifest exchange and printable label PDFs."""

from pathlib import Path
import json
import math
import webbrowser

from PIL import Image
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QPixmap, QPainter, QPdfWriter, QPageSize
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QListWidget,
    QLabel,
    QFileDialog,
    QInputDialog,
    QMessageBox,
    QComboBox,
    QSpinBox,
    QFormLayout,
    QDialog,
    QDialogButtonBox,
    QCheckBox,
    QSplitter,
)

from . import covers
from .domain import slug
from .storage import data_directory, atomic_json
from .ui_common import background, button, cover_pixmap, heading, row


class CoversPage(QWidget):
    def __init__(self, collection, settings_page):
        super().__init__()
        self.collection, self.settings_page = collection, settings_page
        self.book, self.pending = None, ""
        layout = QVBoxLayout(self)
        layout.addWidget(
            heading("Cover verwalten", "Bilder prüfen, zuordnen und bearbeiten")
        )
        self.filter = QComboBox()
        self.filter.addItems(["Alle Cover", "Nur fehlende Cover", "Manuell bestätigt"])
        layout.addWidget(
            row(self.filter, button("Cover-Vorgaben öffnen", self.open_defaults))
        )
        body = QSplitter()
        body.setChildrenCollapsible(False)
        body.setHandleWidth(7)
        layout.addWidget(body, 1)
        self.list = QListWidget()
        self.list.setMinimumWidth(160)
        self.list.setUniformItemSizes(True)
        body.addWidget(self.list)
        preview_panel = QWidget()
        preview_box = QVBoxLayout(preview_panel)
        body.addWidget(preview_panel)
        body.setSizes([300, 900])
        body.handle(1).setToolTip("Ziehen, um Titelliste und Coverbereich anzupassen.")
        self.preview = QLabel("Hörspiel auswählen")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(300, 300)
        preview_box.addWidget(self.preview, 1)
        self.source_label = QLabel()
        self.source_label.setWordWrap(True)
        preview_box.addWidget(self.source_label)
        preview_box.addWidget(
            row(
                button("Einpassen", self.fit),
                button("Zuschneiden", self.crop),
                button("Drehen", self.rotate),
            )
        )
        preview_box.addWidget(
            row(
                button("Ordnerbild", self.folder_image),
                button("Eingebettetes Bild", self.embedded_image),
                button("Bilddatei wählen", self.choose_image),
            )
        )
        preview_box.addWidget(
            row(
                button("Vorschläge suchen", self.search_online),
                button("Bild-URL laden", self.download_image),
                button("Cover entfernen", self.clear_cover),
            )
        )
        self.assignment = QLabel("Änderungen gelten nur für den ausgewählten Titel.")
        layout.addWidget(self.assignment)
        layout.addWidget(
            row(
                button("Manifest importieren", self.import_manifest),
                button("Manifest exportieren", self.export_manifest),
                button("Etiketten als PDF …", self.labels),
                button("Verwerfen", self.discard),
                button("Diesem Titel zuordnen und speichern", self.save, True),
            )
        )
        self.list.currentRowChanged.connect(self.select)
        self.filter.currentTextChanged.connect(self.refresh)
        collection.changed.connect(self.refresh)
        self.refresh()

    def refresh(self):
        identifier = self.book.id if self.book else ""
        self.list.blockSignals(True)
        self.list.clear()
        self.visible = [
            b
            for b in self.collection.books
            if self.filter.currentIndex() == 0
            or (self.filter.currentIndex() == 1 and not b.cover)
            or (self.filter.currentIndex() == 2 and b.cover_confirmed)
        ]
        for index, book in enumerate(self.visible):
            self.list.addItem(
                f"{book.title}\n{book.series} · {'Bestätigt' if book.cover_confirmed else 'Vorhanden' if book.cover else 'Fehlt'}"
            )
            if book.id == identifier:
                self.list.setCurrentRow(index)
        self.list.blockSignals(False)

    def select(self, index):
        if not 0 <= index < len(self.visible):
            return
        self.book = self.visible[index]
        self.pending = self.book.cover
        self.pending_source = self.book.cover_source
        self.show_image()

    def show_image(self):
        self.preview.setPixmap(cover_pixmap(self.pending, 400))
        self.source_label.setText(
            f"Quelle: {getattr(self, 'pending_source', '')}\n{self.pending}"
        )
        if self.book:
            self.assignment.setText(
                f"Zuordnung: {self.book.series} · {self.book.title} — Ausgabe und Titel vor Übernahme prüfen."
            )

    def open_defaults(self):
        self.settings_page.navigation.setCurrentRow(3)
        self.window().navigation.setCurrentRow(5)

    def set_pending(self, path, source):
        if not self.book:
            return
        covers.checked_image(path)
        self.pending, self.pending_source = str(path), source
        self.show_image()

    def choose_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Cover auswählen",
            self.book.folder if self.book else "",
            "Bilder (*.jpg *.jpeg *.png *.webp)",
        )
        if path:
            try:
                self.set_pending(path, "Manuelle Bilddatei")
            except Exception as exc:
                QMessageBox.warning(self, "Cover", str(exc))

    def folder_image(self):
        if not self.book:
            return
        paths = [
            str(p)
            for p in Path(self.book.folder).iterdir()
            if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
        ]
        if not paths:
            QMessageBox.information(
                self, "Ordnerbild", "Keine Bilddatei im Hörspielordner gefunden."
            )
            return
        path, ok = QInputDialog.getItem(
            self, "Ordnerbild", "Bilddatei dieses Hörspiels", paths, editable=False
        )
        if ok:
            self.set_pending(path, "Ordnerbild")

    def embedded_image(self):
        if not self.book:
            return
        from io import BytesIO

        for track in self.book.tracks:
            data = covers.embedded(track.path)
            if data:
                path = self.temp_path()
                covers.checked_image(BytesIO(data)).save(path, "JPEG")
                self.set_pending(path, "Eingebettetes Bild: " + Path(track.path).name)
                return
        QMessageBox.information(self, "Cover", "Kein eingebettetes Cover gefunden.")

    def temp_path(self):
        import uuid

        directory = data_directory() / "covers"
        directory.mkdir(exist_ok=True)
        return directory / (uuid.uuid4().hex + ".jpg")

    def rotate(self):
        if not self.pending:
            return
        image = covers.checked_image(self.pending).rotate(-90, expand=True)
        path = self.temp_path()
        image.save(path, "JPEG", quality=95)
        self.set_pending(path, "Manuell gedreht")

    def fit(self):
        self.show_image()

    def crop(self):
        if not self.pending:
            return
        from .ui_crop import CropDialog

        try:
            image = covers.checked_image(self.pending)
            dialog = CropDialog(image, self)
            if dialog.exec():
                path = self.temp_path()
                image.crop(dialog.canvas.crop_box()).save(path, "JPEG", quality=95)
                self.set_pending(path, "Manuell zugeschnitten")
        except Exception as exc:
            QMessageBox.warning(self, "Zuschnitt", str(exc))

    def search_online(self):
        if not self.book:
            return
        from urllib.parse import urlencode

        webbrowser.open(
            "https://www.google.com/search?"
            + urlencode(
                {
                    "q": self.book.series + " " + self.book.title + " Hörspiel Cover",
                    "tbm": "isch",
                }
            )
        )
        self.source_label.setText(
            "Bildsuche im Browser geöffnet. Passende Ausgabe prüfen, Bild speichern oder direkte Bild-URL laden; keine automatische Zuordnung."
        )

    def download_image(self):
        if not self.book:
            return
        url, ok = QInputDialog.getText(
            self,
            "Cover-Vorschlag laden",
            "Direkte Bildadresse (wird erst nach Zuordnung verwendet):",
        )
        if ok and url:
            background(
                self,
                lambda _: covers.download(url, self.temp_path()),
                lambda path: self.set_pending(
                    path, "Online-Vorschlag, noch nicht bestätigt: " + url
                ),
            )

    def clear_cover(self):
        self.pending, self.pending_source = "", "Manuell leer gelassen"
        self.show_image()

    def discard(self):
        if self.book:
            self.pending, self.pending_source = self.book.cover, self.book.cover_source
            self.show_image()

    def save(self):
        if not self.book:
            return
        self.book.cover, self.book.cover_source = self.pending, self.pending_source
        self.book.cover_confirmed = True
        self.collection.store.save_book(self.book)
        self.collection.refresh_cards()
        self.refresh()

    def export_manifest(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Cover-Manifest", "Cover-Manifest.json", "JSON (*.json)"
        )
        if path:
            atomic_json(
                Path(path),
                [
                    {
                        "id": b.id,
                        "folder": b.folder,
                        "title": b.title,
                        "series": b.series,
                        "cover": b.cover,
                        "source": b.cover_source,
                    }
                    for b in self.collection.books
                ],
            )

    def import_manifest(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Cover-Manifest", "", "JSON (*.json)"
        )
        if not path:
            return
        try:
            rows = json.loads(Path(path).read_text(encoding="utf-8-sig"))
            matches = []
            for entry in rows:
                candidates = [
                    b
                    for b in self.collection.books
                    if b.id == entry.get("id")
                    or str(Path(b.folder))
                    == str(Path(entry.get("folder", "__missing__")))
                ]
                if len(candidates) == 1 and entry.get("cover"):
                    matches.append((candidates[0], entry))
            if not matches:
                raise ValueError(
                    "Keine eindeutigen Ordner-/ID-Zuordnungen im Manifest gefunden."
                )
            preview = "\n".join(
                f"{book.title} ← {entry['cover']}" for book, entry in matches
            )
            if (
                QMessageBox.question(
                    self,
                    "Zuordnungen prüfen",
                    preview + "\n\nDiese Zuordnungen übernehmen?",
                )
                != QMessageBox.StandardButton.Yes
            ):
                return

            def download_all(_):
                result = []
                for book, entry in matches:
                    value = entry["cover"]
                    image = (
                        covers.download(value, self.temp_path())
                        if value.startswith(("http://", "https://"))
                        else (
                            str((Path(path).parent / value).resolve())
                            if not Path(value).is_absolute()
                            else value
                        )
                    )
                    covers.checked_image(image)
                    result.append((book, image, entry.get("source", "Manifest")))
                return result

            def apply(result):
                for book, image, source in result:
                    book.cover, book.cover_source, book.cover_confirmed = (
                        image,
                        source,
                        True,
                    )
                    self.collection.store.save_book(book)
                self.collection.refresh_cards()
                self.refresh()

            background(self, download_all, apply)
        except Exception as exc:
            QMessageBox.warning(self, "Manifest", str(exc))

    def labels(self):
        books = self.collection.selection() or ([self.book] if self.book else [])
        if not books:
            QMessageBox.information(
                self,
                "Etiketten",
                "Bitte Hörspiele in der Sammlung oder hier auswählen.",
            )
            return
        width, ok = QInputDialog.getDouble(
            self, "Etiketten", "Quadratische Etikettengröße in mm", 35, 10, 100, 1
        )
        if not ok:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Etiketten-PDF", "Etiketten.pdf", "PDF (*.pdf)"
        )
        if not path:
            return
        writer = QPdfWriter(path)
        writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
        writer.setResolution(300)
        painter = QPainter(writer)
        unit = writer.resolution() / 25.4
        side, margin, gap = width * unit, 10 * unit, 3 * unit
        columns = max(1, int((writer.width() - 2 * margin) // (side + gap)))
        rows = max(1, int((writer.height() - 2 * margin) // (side + gap + 8 * unit)))
        for index, book in enumerate(books):
            if index and index % (columns * rows) == 0:
                writer.newPage()
            local = index % (columns * rows)
            x, y = margin + local % columns * (
                side + gap
            ), margin + local // columns * (side + gap + 8 * unit)
            image = cover_pixmap(book.cover, round(side))
            painter.drawPixmap(round(x + (side - image.width()) / 2), round(y), image)
            painter.drawText(
                QRectF(x, y + side, side, 8 * unit),
                Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                book.title,
            )
        painter.end()
        QMessageBox.information(
            self,
            "Etiketten",
            "PDF gespeichert. Beim Drucken tatsächliche Größe / 100 % wählen.",
        )
