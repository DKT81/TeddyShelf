"""Qt presentation adapters. No translated labels become persisted option values."""

from PySide6 import QtWidgets as W
from .i18n import Text, manager, render, text


def marked(value):
    return (
        isinstance(value, Text)
        or isinstance(value, (list, tuple))
        and any(map(marked, value))
    )


def remember(target, method, args):
    if not hasattr(target, "_translations"):
        target._translations = {}
    key = (method, args[0] if method in {"setTabText", "setItemText"} else None)
    target._translations[key] = (method, args)
    manager.register(target, refresh)


def apply_bindings(target):
    for method, args in list(target._translations.values()):
        if isinstance(target, CanonicalListItem) and method == "setText":
            W.QListWidgetItem.setText(target, render(args[0]))
        else:
            getattr(target, method)(*render(args))


def refresh(target):
    if isinstance(target, CanonicalCombo):
        target.retranslate()
        return
    for column, source in getattr(target, "_tree_translations", {}).items():
        target.setText(column, render(source))
    if hasattr(target, "_translations"):
        apply_bindings(target)


class CanonicalCombo(W.QComboBox):
    def __init__(self, *args):
        super().__init__(*args)
        self._sources = []
        manager.register(self, refresh)

    def addItem(self, *args, **kwargs):
        # The application's combos use text-first items; preserve optional userData.
        source = args[0] if self.property("i18nUserContent") else text(args[0])
        self._sources.append(source)
        super().addItem(render(source), *args[1:], **kwargs)

    def addItems(self, values):
        for value in values:
            self.addItem(value)

    def currentText(self):
        index = self.currentIndex()
        return (
            str(self._sources[index])
            if 0 <= index < len(self._sources)
            else super().currentText()
        )

    def setCurrentText(self, value):
        for index, source in enumerate(self._sources):
            if str(source) == str(value):
                self.setCurrentIndex(index)
                return
        super().setCurrentText(value)

    def setItemText(self, index, value):
        self._sources[index] = value
        super().setItemText(index, render(value))

    def removeItem(self, index):
        if 0 <= index < len(self._sources):
            self._sources.pop(index)
        super().removeItem(index)

    def clear(self):
        self._sources.clear()
        super().clear()

    def retranslate(self):
        blocked = self.blockSignals(True)
        try:
            for index, source in enumerate(self._sources):
                super().setItemText(index, render(source))
            if hasattr(self, "_translations"):
                apply_bindings(self)
        finally:
            self.blockSignals(blocked)


class CanonicalListItem(W.QListWidgetItem):
    def setText(self, value):
        self._canonical = value
        super().setText(render(value))

    def text(self):
        return str(self._canonical) if hasattr(self, "_canonical") else super().text()


class CanonicalTreeItem(W.QTreeWidgetItem):
    def text(self, column):
        source = getattr(self, "_canonical_columns", {}).get(column)
        return str(source) if source is not None else super().text(column)


def create(cls, *args, **kwargs):
    if cls is W.QComboBox:
        return CanonicalCombo(*args, **kwargs)
    if cls is W.QListWidgetItem:
        item = CanonicalListItem(*render(args), **kwargs)
        source = next((a for a in args if isinstance(a, str)), None)
        if source is not None:
            item._canonical = source
            if marked(source):
                remember(item, "setText", (source,))
        return item
    if cls is W.QTreeWidgetItem:
        item = CanonicalTreeItem(*render(args), **kwargs)
        values = next((a for a in args if isinstance(a, (list, tuple))), [])
        item._canonical_columns = dict(enumerate(values))
        for column, value in enumerate(values):
            if marked(value):
                # Tree item binding keys need their own column identity.
                if not hasattr(item, "_tree_translations"):
                    item._tree_translations = {}
                item._tree_translations[column] = value
        if hasattr(item, "_tree_translations"):
            manager.register(item, refresh)
        return item
    value = cls(*render(args), **kwargs)
    constructor_methods = {
        W.QLabel: "setText",
        W.QPushButton: "setText",
        W.QCheckBox: "setText",
        W.QPlainTextEdit: "setPlainText",
        W.QGroupBox: "setTitle",
        W.QTableWidgetItem: "setText",
    }
    if cls in constructor_methods and args and marked(args[0]):
        remember(value, constructor_methods[cls], (args[0],))
    return value


def call(target, method, *args, **kwargs):
    # Exceptions arrive as already formatted source strings. Translate only the
    # dialog message, never paths, titles or editable user content.
    if (
        target is W.QMessageBox
        and method in {"warning", "critical", "information", "question"}
        and len(args) >= 3
    ):
        args = (*args[:2], text(args[2]), *args[3:])
    # A later user value must replace an earlier translation binding, otherwise
    # changing language could restore stale text over the current value.
    if hasattr(target, "_translations") and not marked(args):
        key = (method, args[0] if method in {"setTabText", "setItemText"} else None)
        target._translations.pop(key, None)
    if isinstance(target, CanonicalCombo) and method in {
        "addItems",
        "addItem",
        "setItemText",
    }:
        return getattr(target, method)(*args, **kwargs)
    if isinstance(target, W.QListWidget) and method in {"addItem", "insertItem"}:
        values = list(args)
        position = 1 if method == "insertItem" else 0
        if isinstance(values[position], str):
            values[position] = create(W.QListWidgetItem, values[position])
        return getattr(target, method)(*values, **kwargs)
    if isinstance(target, W.QListWidget) and method == "addItems":
        for value in args[0]:
            call(target, "addItem", value)
        return
    if (
        isinstance(target, W.QFormLayout)
        and method == "addRow"
        and args
        and isinstance(args[0], str)
    ):
        return target.addRow(create(W.QLabel, args[0]), *args[1:])
    if target is W.QInputDialog and method == "getItem":
        result, accepted = target.getItem(
            *render(args), **{k: render(v) for k, v in kwargs.items()}
        )
        choices = args[3]
        for source in choices:
            if render(source) == result:
                return str(source), accepted
        return result, accepted
    result = getattr(target, method)(
        *render(args), **{k: render(v) for k, v in kwargs.items()}
    )
    if method == "addTab" and marked(args[-1]):
        remember(target, "setTabText", (result, args[-1]))
    elif method in {
        "setText",
        "setPlainText",
        "setWindowTitle",
        "setToolTip",
        "setPlaceholderText",
        "setTitle",
        "setHeaderLabel",
        "setHeaderLabels",
        "setHorizontalHeaderLabels",
        "setVerticalHeaderLabels",
        "setTabText",
    } and marked(args):
        remember(target, method, args)
    elif method == "showMessage" and marked(args):
        remember(target, method, args)
    return result
