from pathlib import Path
import os
import pytest
from teddystudio import media

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def isolated_application_data(tmp_path, monkeypatch):
    # Running the test suite must never read/write a real user's configuration.
    monkeypatch.delenv("TEDDYSHELF_DATA", raising=False)
    monkeypatch.setenv("TEDDYSTUDIO_DATA", str(tmp_path / "isolated-data"))


@pytest.fixture
def sample(tmp_path):
    path = tmp_path / "source.wav"
    media.run(
        [
            media.binary("ffmpeg"),
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=4.2",
            "-ac",
            "2",
            path,
        ]
    )
    return path


@pytest.fixture
def app(monkeypatch):
    from PySide6.QtWidgets import QApplication
    from teddystudio.ui_library import LibraryPage

    # GUI tests stay deterministic and never contact a real configured server.
    monkeypatch.setattr(LibraryPage, "sync_remote", lambda self: None)

    return QApplication.instance() or QApplication([])
