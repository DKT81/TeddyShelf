"""Playlist chapters must follow whole sources, independently of a split interval."""

from dataclasses import asdict
import json

from PySide6.QtCore import QTime
import pytest

from teddystudio import media, taf
from teddystudio.domain import Book, Track
from teddystudio.jobs import execute
from teddystudio.storage import Settings, Store


def test_playlist_plan_keeps_source_boundaries_and_order():
    assert media.audio_plan([12, 5, 30], 0, media.AUDIO_MODES[2]) == [
        (0, 12),
        (12, 5),
        (17, 30),
    ]
    with pytest.raises(ValueError):
        media.audio_plan([12, 0], 10, media.AUDIO_MODES[2])


def test_real_playlist_taf_contains_full_sources(sample, tmp_path):
    second = tmp_path / "second.wav"
    media.run(
        [
            media.binary("ffmpeg"),
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880:duration=2.3",
            "-ac",
            "2",
            second,
        ]
    )
    settings = Settings(source=str(tmp_path / "unused-source"))
    book = Book(
        "playlist",
        str(tmp_path),
        "Complete playlist",
        "Demo",
        1,
        tracks=[
            Track(str(sample), "First", "1", 4.2),
            Track(str(second), "Second", "2", 2.3),
        ],
    )
    payload = {
        "book": book.to_dict(),
        "settings": asdict(settings),
        "profile": asdict(settings.profile()),
        "audio": {
            "seconds": 0,
            "mode": media.AUDIO_MODES[2],
            "target": "Nur lokal",
            "format": "mp3",
            "destination": str(tmp_path.parent / (tmp_path.name + "-export")),
        },
    }
    result = execute("Audio aufteilen", payload, tmp_path / "work", lambda *_: None)
    assert result["status"] == "Lokal gespeichert"
    from pathlib import Path

    target = next(Path(payload["audio"]["destination"]).glob("*.taf"))
    info = taf.validate(target, expected=6.5, chapters=2)
    pages = list(taf.pages(target.read_bytes()[4096:]))
    assert abs(pages[info["chapterPages"][1] - 1].granule / 48000 - 4.2) < 0.15
    assert not list((tmp_path / "work").rglob("*.pcm"))
    # A retry still produces the same complete file without source truncation.
    original = target.read_bytes()
    execute("Audio aufteilen", payload, tmp_path / "work", lambda *_: None)
    assert target.read_bytes() == original


def test_playlist_ui_preview_and_job_ignore_time_interval(app, tmp_path, monkeypatch):
    from teddystudio.ui_audio import AudioPage

    monkeypatch.setenv("TEDDYSTUDIO_DATA", str(tmp_path / "data"))
    settings = Settings(audio_mode=media.AUDIO_MODES[2])
    store = Store(tmp_path / "test.sqlite")
    page = AudioPage(store, settings, None, lambda: None)
    assert not page.length.isEnabled() and not page.format.isEnabled()
    page.sources = [Track("a.mp3", duration=12), Track("b.mp3", duration=30)]
    page.length.setTime(QTime(0, 0, 0))
    page.refresh_sources()
    assert not page.length.isEnabled() and not page.format.isEnabled()
    assert page.parts.rowCount() == 2
    assert page.wave.boundaries == [12]
    assert "keine Kürzung" in page.summary.text()
    page.create_job()
    assert (
        json.loads(store.jobs()[0]["payload"])["audio"]["mode"] == media.AUDIO_MODES[2]
    )
    page.mode.setCurrentIndex(0)
    page.length.setTime(QTime(0, 0, 10))
    assert page.length.isEnabled() and page.format.isEnabled()
    assert page.parts.rowCount() == 5


def test_playlist_default_settings_roundtrip(tmp_path):
    path = tmp_path / "settings.json"
    Settings(audio_mode=media.AUDIO_MODES[2]).save(path)
    assert Settings.load(path).audio_mode == media.AUDIO_MODES[2]
