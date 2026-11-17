from dataclasses import asdict
from pathlib import Path
import hashlib
import json
import shutil
import pytest
from PIL import Image
from teddystudio import covers, library, media
from teddystudio.domain import Book, Track
from teddystudio.storage import Settings
from teddystudio.jobs import execute


def test_recursion_sharky_missing_cover_and_corrupt_audio(sample, tmp_path):
    root = tmp_path / "collection"
    folder = root / "Käptn Sharky - Bei den Wikingern"
    folder.mkdir(parents=True)
    shutil.copyfile(sample, folder / "01.wav")
    other = root / "Serie" / "Folge 001 - Der Anfang"
    other.mkdir(parents=True)
    (other / "bad.mp3").write_bytes(b"not audio")
    books = library.scan(
        root, Settings(export=str(tmp_path / "export")), tmp_path / "cache"
    )
    sharky = next(b for b in books if b.series == "Käptn Sharky")
    assert sharky.episode is None and not sharky.cover
    assert next(b for b in books if b.episode == 1).status == "Prüfen"


def test_cover_source_validation_and_fallback(tmp_path):
    output = tmp_path / "fallback.jpg"
    assert covers.prepare("", output, Settings()) is None
    assert (
        covers.prepare("", output, Settings(cover_missing="Neutrales Symbol")) == output
    )
    assert Image.open(output).size == (800, 800)
    source = tmp_path / "wide.png"
    Image.new("RGB", (1200, 600)).save(source)
    covers.prepare(source, output, Settings(cover_size=400))
    assert Image.open(output).size == (400, 200)


def test_empty_legacy_playlist_keeps_valid_audio_order(sample, tmp_path):
    root = tmp_path / "collection"
    folder = root / "Hörspiel"
    folder.mkdir(parents=True)
    shutil.copyfile(sample, folder / "01.wav")
    (folder / "playlist.m3u").write_text("#EXTM3U\n", encoding="utf-8")
    book = library.scan(
        root, Settings(export=str(tmp_path / "export")), tmp_path / "cache"
    )[0]
    assert len(book.tracks) == 1 and not book.tracks[0].error


@pytest.mark.parametrize(
    "extension,codec", [("mp3", "libmp3lame"), ("flac", "flac"), ("opus", "libopus")]
)
def test_explicit_tag_write_and_cover_preserve_track_number(
    sample, tmp_path, extension, codec
):
    import mutagen

    source = tmp_path / ("tagged." + extension)
    media.run(
        [
            media.binary("ffmpeg"),
            "-v",
            "error",
            "-y",
            "-i",
            sample,
            "-c:a",
            codec,
            source,
        ]
    )
    image = tmp_path / "cover.jpg"
    Image.new("RGB", (50, 50), "green").save(image)
    book = Book(
        "tag",
        str(tmp_path),
        "Folge zwei",
        "Serie",
        2,
        album="Album",
        artist="Sprecher",
        cover=str(image),
        tracks=[Track(str(source), "Spur sieben", "07")],
    )
    library.write_tags(book, include_cover=True)
    audio = mutagen.File(source, easy=True)
    assert audio["tracknumber"][0] == "07"
    assert audio["title"][0] == "Spur sieben"
    assert covers.embedded(source)
    assert list(tmp_path.glob(source.name + ".tags-*.bak"))
    book.album = ""
    book.genre = ""
    library.write_tags(book)
    cleared = mutagen.File(source, easy=True)
    assert "album" not in cleared and "genre" not in cleared
    assert cleared["tracknumber"][0] == "07"


def test_local_job_real_split_restart_and_changed_source_rejected(sample, tmp_path):
    settings = Settings(export=str(tmp_path / "export"))
    book = Book(
        "1", str(sample.parent), "Test", tracks=[Track(str(sample), duration=4.2)]
    )
    payload = {
        "book": book.to_dict(),
        "settings": asdict(settings),
        "profile": asdict(settings.profile()),
        "audio": {
            "seconds": 2,
            "mode": "Separate Dateien / Custom-Tonies",
            "target": "Nur lokal",
            "format": "wav",
            "destination": str(tmp_path / "separate"),
        },
    }
    # Exports must be outside sources; use an isolated source folder.
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    shutil.copyfile(sample, source_dir / "audio.wav")
    payload["book"]["tracks"][0]["path"] = str(source_dir / "audio.wav")
    result = execute("Audio aufteilen", payload, tmp_path / "work", lambda *_: None)
    assert result["status"] == "Lokal gespeichert"
    files = list((tmp_path / "separate").glob("*.wav"))
    assert len(files) == 3
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    execute("Audio aufteilen", payload, tmp_path / "work", lambda *_: None)
    assert before == {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    with (source_dir / "audio.wav").open("ab") as stream:
        stream.write(b"changed")
    with pytest.raises(ValueError, match="verändert"):
        execute("Audio aufteilen", payload, tmp_path / "work", lambda *_: None)
