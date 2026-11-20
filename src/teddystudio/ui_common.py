"""Small reusable Qt controls and background-task delivery on the GUI thread."""

from __future__ import annotations

from .i18n import text as tr_text, format_text as _f
from .i18n_widgets import call as _ui, create as _new

from PySide6.QtCore import (
    QObject,
    QRunnable,
    QThreadPool,
    Signal,
    Qt,
    QRect,
    QSize,
    QPoint,
)
from PySide6.QtGui import QPixmap, QPainter, QColor, QPen
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QFileDialog,
    QMessageBox,
    QLineEdit,
    QSizePolicy,
    QLayout,
)


class Signals(QObject):
    done = Signal(object)
    failed = Signal(str)
    progress = Signal(str)


class Task(QRunnable):
    def __init__(self, function):
        super().__init__()
        self.function = function
        self.signals = Signals()

    def run(self):
        try:
            self.signals.done.emit(self.function(self.signals.progress.emit))
        except Exception as exc:
            self.signals.failed.emit(str(exc))


def background(parent, function, done):
    task = Task(function)
    if not hasattr(parent, "_tasks"):
        parent._tasks = []
    parent._tasks.append(task)

    def finish():
        if task in parent._tasks:
            parent._tasks.remove(task)

    task.signals.done.connect(done)
    task.signals.done.connect(lambda _: finish())
    task.signals.failed.connect(
        lambda message: _ui(
            QMessageBox, "warning", parent, tr_text("Vorgang fehlgeschlagen"), message
        )
    )
    task.signals.failed.connect(lambda _: finish())
    QThreadPool.globalInstance().start(task)
    return task


def button(text, function, primary=False, tooltip=""):
    control = _new(QPushButton, text)
    control.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
    _ui(
        control,
        "setToolTip",
        tooltip
        or {
            tr_text("Spur ↑"): tr_text(
                "Markierte Spur in der Wiedergabereihenfolge nach oben verschieben."
            ),
            tr_text("Spur ↓"): tr_text(
                "Markierte Spur in der Wiedergabereihenfolge nach unten verschieben."
            ),
            tr_text("Daten übernehmen"): tr_text(
                "Änderungen in der lokalen Sammlung speichern. Quelldateien bleiben unverändert."
            ),
            tr_text("Tags in Quelldateien schreiben …"): tr_text(
                "Nach Vorschau die Originaldateien ändern; vorher werden Sicherungen erstellt."
            ),
            tr_text("Reihenfolge bestätigen"): tr_text(
                "Aktuelle Reihenfolge speichern und eine Playlist-Warnung bestätigen."
            ),
            tr_text("Wellenform laden"): tr_text(
                "Wellenform der Eingabedateien berechnen; orange Linien markieren Schnittgrenzen."
            ),
            tr_text("Auftrag erstellen"): tr_text(
                "Den angezeigten Schnittplan als ausführbaren Auftrag in die Warteschlange aufnehmen."
            ),
            tr_text("Ausgewählte importieren …"): tr_text(
                "Importvorschau für die ausgewählten Hörspiele öffnen."
            ),
            tr_text("Ordner einlesen"): tr_text(
                "Ordner einschließlich Unterordnern einlesen. Unveränderte Audiodaten werden zwischengespeichert."
            ),
            tr_text("Quelle entfernen"): tr_text(
                "Markierten Eintrag nur aus der Eingabeliste entfernen. Die Datei bleibt erhalten."
            ),
            tr_text("Liste leeren"): tr_text(
                "Alle Einträge aus der Eingabeliste entfernen; keine Dateien löschen."
            ),
            "…": tr_text("Datei oder Ordner auswählen."),
        }.get(
            text,
            text + tr_text(" – Aktion für die aktuelle Ansicht oder Auswahl."),
        ),
    )
    control.clicked.connect(function)
    if primary:
        control.setProperty("primary", True)
    return control


class FlowLayout(QLayout):
    """Keep related actions together and wrap them when the window gets narrower."""

    def __init__(self, parent):
        super().__init__(parent)
        self.items = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(6)

    def addItem(self, item):
        self.items.append(item)

    def count(self):
        return len(self.items)

    def itemAt(self, index):
        return self.items[index] if 0 <= index < len(self.items) else None

    def takeAt(self, index):
        return self.items.pop(index) if 0 <= index < len(self.items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self.arrange(QRect(0, 0, width, 0), False)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self.arrange(rect, True)

    def sizeHint(self):
        return QSize(
            sum(i.sizeHint().width() + self.spacing() for i in self.items),
            max((i.sizeHint().height() for i in self.items), default=0),
        )

    def minimumSize(self):
        return QSize(
            max((i.minimumSize().width() for i in self.items), default=0),
            max((i.minimumSize().height() for i in self.items), default=0),
        )

    def arrange(self, rect, apply):
        x, y, line_height = rect.x(), rect.y(), 0
        for item in self.items:
            size = item.sizeHint()
            if x > rect.x() and x + size.width() > rect.right() + 1:
                x, y = rect.x(), y + line_height + self.spacing()
                line_height = 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), size))
            x += size.width() + self.spacing()
            line_height = max(line_height, size.height())
        return y - rect.y() + line_height


class ActionRow(QWidget):
    """Reflowing action bars must reserve their wrapped height in parent layouts."""

    def resizeEvent(self, event):
        height = self.layout().heightForWidth(event.size().width())
        if self.minimumHeight() != height or self.maximumHeight() != height:
            self.setFixedHeight(height)
            self.updateGeometry()
        super().resizeEvent(event)


def row(*widgets):
    # An all-action row wraps; mixed rows give spare space to their input/label.
    actions_only = all(isinstance(widget, QPushButton) for widget in widgets)
    container = ActionRow() if actions_only else QWidget()
    layout = FlowLayout(container) if actions_only else QHBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)
    container.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
    expanding = False
    for widget in widgets:
        if actions_only:
            layout.addWidget(widget)
        else:
            stretch = (
                isinstance(widget, (QLineEdit, QLabel))
                or widget.sizePolicy().horizontalPolicy()
                == QSizePolicy.Policy.Expanding
            )
            expanding = expanding or stretch
            layout.addWidget(widget, 1 if stretch else 0)
    if not actions_only and not expanding:
        layout.addStretch(1)
    return container


def heading(title, subtitle=""):
    container = QWidget()
    layout = QVBoxLayout(container)
    label = _new(QLabel, title)
    label.setObjectName("heading")
    layout.addWidget(label)
    if subtitle:
        layout.addWidget(_new(QLabel, subtitle))
    return container


def path_control(edit, parent, directory=True, save=False, filter="Alle Dateien (*)"):
    def choose():
        if directory:
            value = _ui(
                QFileDialog,
                "getExistingDirectory",
                parent,
                tr_text("Ordner auswählen"),
                edit.text(),
            )
        elif save:
            value, _ = _ui(
                QFileDialog,
                "getSaveFileName",
                parent,
                tr_text("Datei speichern"),
                edit.text(),
                filter,
            )
        else:
            value, _ = _ui(
                QFileDialog,
                "getOpenFileName",
                parent,
                tr_text("Datei auswählen"),
                edit.text(),
                filter,
            )
        if value:
            _ui(edit, "setText", value)

    return row(edit, button("…", choose))


def cover_pixmap(path, size=160):
    pixmap = QPixmap(path) if path else QPixmap()
    if pixmap.isNull():
        pixmap = QPixmap(size, size)
        pixmap.fill(QColor("#e6ece9"))
        painter = QPainter(pixmap)
        painter.setPen(QPen(QColor("#718783"), 3))
        painter.drawRoundedRect(size // 4, size // 4, size // 2, size // 2, 10, 10)
        _ui(painter, "drawText", pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "♪")
        painter.end()
    return pixmap.scaled(
        size,
        size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def clock(seconds):
    seconds = round(seconds)
    return f"{seconds // 3600:02}:{seconds // 60 % 60:02}:{seconds % 60:02}"


LIGHT = """
QWidget { font-family: 'Noto Sans'; font-size: 13px; color: #243d3b; background: #faf9f6; }
QMainWindow { background: #faf9f6; }
QLabel#heading { font-size: 25px; font-weight: 700; color: #075b58; padding: 4px 0; }
QPushButton { background: #fff; border: 1px solid #b9ccc6; border-radius: 7px; padding: 4px 9px; }
QPushButton:hover { background: #dff3ed; }
QPushButton[primary='true'] { background: #00877e; color: white; border-color: #00877e; font-weight: 600; }
QPushButton:disabled { color: #99aaa5; background: #ebefec; }
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QTimeEdit, QDoubleSpinBox { background: white; border: 1px solid #bdcbc5; border-radius: 5px; padding: 6px; }
QLineEdit, QComboBox, QSpinBox, QTimeEdit, QDoubleSpinBox { min-height: 1.35em; padding: 3px 6px; }
QListWidget, QTreeWidget, QTableWidget { background: #fff; border: 1px solid #d4ded8; border-radius: 8px; alternate-background-color: #f1f6f2; }
QListWidget::item { padding: 2px 4px; }
QListWidget#coverCards::item { padding: 8px; }
QListWidget::item:selected, QTreeWidget::item:selected, QTableWidget::item:selected { background: #cef0e6; color: #064f49; }
QGroupBox { border: 1px solid #d0dcd5; border-radius: 9px; margin-top: 14px; padding: 14px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 14px; }
QHeaderView::section { background: #eaf2ed; border: 0; padding: 5px; }
QTabBar::tab { padding: 6px 12px; border-bottom: 2px solid #e0e9e3; }
QTabBar::tab:selected { background: #d9f1e9; border-bottom-color: #00877e; }
QProgressBar { border: 0; background: #e4eae6; border-radius: 6px; text-align: center; min-height: 20px; }
QProgressBar::chunk { background: #00877e; border-radius: 6px; }
QSplitter::handle { background: #e0e7e2; }
"""

DARK = LIGHT + """
QWidget { background: #202d2b; color: #e0eeea; }
QLabel#heading { color: #77dfca; }
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QTimeEdit, QDoubleSpinBox, QListWidget, QTreeWidget, QTableWidget, QPushButton { background: #2d3d39; color: #e0eeea; border-color: #536b64; }
QHeaderView::section { background: #344c43; }
"""
