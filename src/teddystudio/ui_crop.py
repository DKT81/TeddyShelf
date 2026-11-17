"""Direct image crop selection in original image coordinates, independent of scaling."""

import math
from PySide6.QtCore import Qt, QRectF, QPointF, Signal
from PySide6.QtGui import QImage, QPainter, QColor, QPen, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QWidget,
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QCheckBox,
    QDialogButtonBox,
)
from .ui_common import button


class CropCanvas(QWidget):
    changed = Signal(object)

    def __init__(self, image):
        super().__init__()
        self.image_width, self.image_height = image.size
        self.image = QImage(
            image.tobytes(),
            image.width,
            image.height,
            image.width * 3,
            QImage.Format.Format_RGB888,
        ).copy()
        self.square = True
        self.selection = QRectF()
        self.mode = None
        self.setMinimumSize(320, 240)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setToolTip(
            "Im Bild einen Rahmen aufziehen. Innen ziehen zum Verschieben; Ecken und Kanten ziehen zum Vergrößern. Pfeiltasten verschieben, Umschalt + Pfeil um zehn Bildpunkte."
        )
        self.reset()

    def reset(self):
        w, h = self.image_width, self.image_height
        side = min(w, h)
        self.selection = (
            QRectF((w - side) / 2, (h - side) / 2, side, side)
            if self.square
            else QRectF(0, 0, w, h)
        )
        self.notify()

    def set_square(self, checked):
        self.square = checked
        if checked:
            side = min(self.selection.width(), self.selection.height())
            center = self.selection.center()
            self.selection = QRectF(
                center.x() - side / 2, center.y() - side / 2, side, side
            )
        self.notify()

    def image_rect(self):
        scale = min(
            max(1, self.width() - 24) / self.image_width,
            max(1, self.height() - 24) / self.image_height,
        )
        w, h = self.image_width * scale, self.image_height * scale
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    def to_view(self, point):
        rect = self.image_rect()
        scale = rect.width() / self.image_width
        return QPointF(rect.x() + point.x() * scale, rect.y() + point.y() * scale)

    def to_image(self, point):
        rect = self.image_rect()
        scale = rect.width() / self.image_width
        return QPointF(
            max(0, min(self.image_width, (point.x() - rect.x()) / scale)),
            max(0, min(self.image_height, (point.y() - rect.y()) / scale)),
        )

    def view_selection(self):
        return QRectF(
            self.to_view(self.selection.topLeft()),
            self.to_view(self.selection.bottomRight()),
        )

    def handles(self):
        r = self.view_selection()
        c = r.center()
        return {
            "nw": r.topLeft(),
            "n": QPointF(c.x(), r.top()),
            "ne": r.topRight(),
            "e": QPointF(r.right(), c.y()),
            "se": r.bottomRight(),
            "s": QPointF(c.x(), r.bottom()),
            "sw": r.bottomLeft(),
            "w": QPointF(r.left(), c.y()),
        }

    def hit(self, point):
        for name, pos in self.handles().items():
            if abs(point.x() - pos.x()) <= 9 and abs(point.y() - pos.y()) <= 9:
                return name
        return "move" if self.view_selection().contains(point) else "new"

    def crop_box(self):
        r = self.selection
        left = max(0, min(self.image_width - 1, math.floor(r.left())))
        top = max(0, min(self.image_height - 1, math.floor(r.top())))
        return (
            left,
            top,
            max(left + 1, min(self.image_width, math.ceil(r.right()))),
            max(top + 1, min(self.image_height, math.ceil(r.bottom()))),
        )

    def notify(self):
        self.update()
        self.changed.emit(self.crop_box())

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#243331"))
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        image_rect = self.image_rect()
        painter.drawImage(image_rect, self.image)
        selected = self.view_selection()
        shade = QPainterPath()
        shade.setFillRule(Qt.FillRule.OddEvenFill)
        shade.addRect(image_rect)
        shade.addRect(selected)
        painter.fillPath(shade, QColor(0, 0, 0, 145))
        painter.setPen(QPen(QColor("white"), 2))
        painter.drawRect(selected)
        painter.setPen(QPen(QColor(255, 255, 255, 100), 1, Qt.PenStyle.DashLine))
        for n in (1, 2):
            x = selected.left() + selected.width() * n / 3
            y = selected.top() + selected.height() * n / 3
            painter.drawLine(QPointF(x, selected.top()), QPointF(x, selected.bottom()))
            painter.drawLine(QPointF(selected.left(), y), QPointF(selected.right(), y))
        painter.setPen(QPen(QColor("#00877e"), 2))
        painter.setBrush(QColor("white"))
        for point in self.handles().values():
            painter.drawRect(QRectF(point.x() - 4, point.y() - 4, 8, 8))

    def mousePressEvent(self, event):
        if (
            event.button() != Qt.MouseButton.LeftButton
            or not self.image_rect().contains(event.position())
        ):
            return
        self.setFocus()
        self.mode = self.hit(event.position())
        self.anchor = self.to_image(event.position())
        self.start = QRectF(self.selection)
        if self.mode == "new":
            self.selection = QRectF(
                min(self.image_width - 1, self.anchor.x()),
                min(self.image_height - 1, self.anchor.y()),
                1,
                1,
            )
        self.notify()
        event.accept()

    def mouseMoveEvent(self, event):
        if not self.mode:
            mode = self.hit(event.position())
            cursor = {
                "nw": Qt.CursorShape.SizeFDiagCursor,
                "se": Qt.CursorShape.SizeFDiagCursor,
                "ne": Qt.CursorShape.SizeBDiagCursor,
                "sw": Qt.CursorShape.SizeBDiagCursor,
                "n": Qt.CursorShape.SizeVerCursor,
                "s": Qt.CursorShape.SizeVerCursor,
                "e": Qt.CursorShape.SizeHorCursor,
                "w": Qt.CursorShape.SizeHorCursor,
                "move": Qt.CursorShape.SizeAllCursor,
            }.get(mode, Qt.CursorShape.CrossCursor)
            self.setCursor(cursor)
            return
        point = self.to_image(event.position())
        r = QRectF(self.start)
        if self.mode == "move":
            r.moveTo(
                max(
                    0,
                    min(
                        self.image_width - r.width(),
                        r.x() + point.x() - self.anchor.x(),
                    ),
                ),
                max(
                    0,
                    min(
                        self.image_height - r.height(),
                        r.y() + point.y() - self.anchor.y(),
                    ),
                ),
            )
        elif self.mode == "new":
            r = (
                self.square_rect(self.anchor, point)
                if self.square
                else QRectF(self.anchor, point).normalized()
            )
        elif self.square:
            opposite = {
                "nw": r.bottomRight(),
                "ne": r.bottomLeft(),
                "se": r.topLeft(),
                "sw": r.topRight(),
                "e": r.topLeft(),
                "w": r.topRight(),
                "n": r.bottomLeft(),
                "s": r.topLeft(),
            }[self.mode]
            dx = -1 if "w" in self.mode else 1
            dy = -1 if "n" in self.mode else 1
            side = (
                abs(point.y() - opposite.y())
                if self.mode in {"n", "s"}
                else (
                    abs(point.x() - opposite.x())
                    if self.mode in {"e", "w"}
                    else max(
                        abs(point.x() - opposite.x()), abs(point.y() - opposite.y())
                    )
                )
            )
            r = self.square_rect(
                opposite, QPointF(opposite.x() + dx * side, opposite.y() + dy * side)
            )
        else:
            if "w" in self.mode:
                r.setLeft(min(point.x(), r.right() - 1))
            if "e" in self.mode:
                r.setRight(max(point.x(), r.left() + 1))
            if "n" in self.mode:
                r.setTop(min(point.y(), r.bottom() - 1))
            if "s" in self.mode:
                r.setBottom(max(point.y(), r.top() + 1))
        if r.width() >= 1 and r.height() >= 1:
            self.selection = r
        self.notify()

    def square_rect(self, anchor, point):
        dx = 1 if point.x() >= anchor.x() else -1
        dy = 1 if point.y() >= anchor.y() else -1
        if anchor.x() >= self.image_width:
            dx = -1
        if anchor.y() >= self.image_height:
            dy = -1
        side = min(
            max(1, max(abs(point.x() - anchor.x()), abs(point.y() - anchor.y()))),
            self.image_width - anchor.x() if dx > 0 else anchor.x(),
            self.image_height - anchor.y() if dy > 0 else anchor.y(),
        )
        return QRectF(
            anchor.x() if dx > 0 else anchor.x() - side,
            anchor.y() if dy > 0 else anchor.y() - side,
            side,
            side,
        )

    def mouseReleaseEvent(self, event):
        self.mode = None
        event.accept()

    def keyPressEvent(self, event):
        moves = {
            Qt.Key.Key_Left: (-1, 0),
            Qt.Key.Key_Right: (1, 0),
            Qt.Key.Key_Up: (0, -1),
            Qt.Key.Key_Down: (0, 1),
        }
        if event.key() not in moves:
            return super().keyPressEvent(event)
        dx, dy = moves[event.key()]
        step = 10 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
        r = self.selection
        r.moveTo(
            max(0, min(self.image_width - r.width(), r.x() + dx * step)),
            max(0, min(self.image_height - r.height(), r.y() + dy * step)),
        )
        self.notify()
        event.accept()


class CropDialog(QDialog):
    def __init__(self, image, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Cover zuschneiden – Rahmen im Bild ziehen")
        self.resize(980, 680)
        layout = QVBoxLayout(self)
        help = QLabel(
            "Rahmen im Bild aufziehen, innen verschieben oder an den Griffen verändern. Die Quelldatei bleibt erhalten."
        )
        help.setWordWrap(True)
        layout.addWidget(help)
        body = QHBoxLayout()
        layout.addLayout(body, 1)
        self.canvas = CropCanvas(image)
        body.addWidget(self.canvas, 1)
        side = QVBoxLayout()
        body.addLayout(side)
        side.addWidget(QLabel("Vorschau"))
        self.preview = QLabel()
        self.preview.setFixedSize(200, 200)
        side.addWidget(self.preview)
        square = QCheckBox("Quadratischer Ausschnitt")
        square.setChecked(True)
        square.toggled.connect(self.canvas.set_square)
        side.addWidget(square)
        side.addWidget(button("Rahmen zurücksetzen", self.canvas.reset))
        side.addStretch()
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
            "Zuschnitt übernehmen"
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.canvas.changed.connect(self.update_preview)
        self.update_preview(self.canvas.crop_box())

    def update_preview(self, box):
        left, top, right, bottom = box
        w, h = right - left, bottom - top
        preview = QPixmap(200, 200)
        preview.fill(QColor("#e6ece9"))
        scale = min(200 / w, 200 / h)
        target = QRectF(
            (200 - w * scale) / 2, (200 - h * scale) / 2, w * scale, h * scale
        )
        painter = QPainter(preview)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawImage(target, self.canvas.image, QRectF(left, top, w, h))
        painter.end()
        self.preview.setPixmap(preview)
