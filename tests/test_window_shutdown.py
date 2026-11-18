"""Exercise the actual close dialog and asynchronous shutdown through Qt events."""

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from teddystudio import __version__
from teddystudio.app import MainWindow
from teddystudio.storage import Settings, Store


class PendingWorker:
    def __init__(self):
        self.active = True
        self.cancel_calls = 0

    @property
    def running(self):
        return self.active

    def cancel_all(self):
        self.cancel_calls += 1


def choose_button(text):
    dialog = QApplication.activeModalWidget()
    assert isinstance(dialog, QMessageBox)
    assert dialog.defaultButton().text() == "Programm weiter ausführen"
    next(b for b in dialog.buttons() if b.text() == text).click()


def make_window(tmp_path, monkeypatch):
    monkeypatch.setenv("TEDDYSTUDIO_DATA", str(tmp_path / "data"))
    window = MainWindow(Settings(), Store(tmp_path / "library.sqlite"))
    window.worker = PendingWorker()
    window.show()
    return window


def test_continue_keeps_window_and_worker_running(app, tmp_path, monkeypatch):
    window = make_window(tmp_path, monkeypatch)
    try:
        QTimer.singleShot(0, lambda: choose_button("Programm weiter ausführen"))
        window.close()
        assert window.isVisible() and window.centralWidget().isEnabled()
        assert window.worker.running and window.worker.cancel_calls == 0
        assert not window._shutdown_timer.isActive()
    finally:
        window.worker.active = False
        window.close()


def test_abort_closes_only_after_workers_and_background_finish(
    app, tmp_path, monkeypatch
):
    window = make_window(tmp_path, monkeypatch)
    QTimer.singleShot(0, lambda: choose_button("Aufträge abbrechen und beenden"))
    window.close()
    assert window.isVisible() and not window.centralWidget().isEnabled()
    assert window.worker.cancel_calls == 1 and window._shutdown_timer.isActive()
    monkeypatch.setattr(
        QMessageBox,
        "exec",
        lambda *_: (_ for _ in ()).throw(AssertionError("Repeated close prompt")),
    )
    window.close()
    assert window.worker.cancel_calls == 1
    window.worker.active = False
    window.collection._tasks = [object()]
    window._finish_shutdown()
    assert window.isVisible()
    window.collection._tasks.clear()
    window._finish_shutdown()
    assert not window.isVisible() and not window._shutdown_timer.isActive()


def test_version_is_permanent_even_during_status_messages(app, tmp_path, monkeypatch):
    window = make_window(tmp_path, monkeypatch)
    try:
        window.statusBar().showMessage("Konvertierung läuft")
        app.processEvents()
        assert window.version_label.text() == f"V {__version__}"
        assert window.version_label.isVisible()
        assert window.version_label.parent() == window.statusBar()
    finally:
        window.worker.active = False
        window.close()
