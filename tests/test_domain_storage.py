from dataclasses import asdict
import json
import pytest
from teddystudio.domain import Book, Track, NamingProfile, ascii_name, safe_relative
from teddystudio.storage import Settings, Store


def test_german_names_and_separate_unknown_episode():
    book = Book(
        "1", "", "Bei den Wikingern", "Käptn Sharky", tracks=[Track("a", track="07")]
    )
    name = NamingProfile().render(book)
    assert name["model"] == "kaeptn-sharky-bei-den-wikingern"
    assert book.episode is None and book.tracks[0].track == "07"
    assert name["audio_folder"] == "library/Kaeptn Sharky"


def test_bibi_naming():
    names = NamingProfile().render(Book("1", "", "Das Fohlen", "Bibi & Tina", 1))
    assert names["model"] == "bibi-tina-001"
    assert names["audio_folder"] == "library/Bibi-Tina"
    assert names["image_folder"] == "custom_img/Bibi-Tina"


def test_configurable_data_sources_and_track_placeholder():
    book = Book(
        "1",
        "Serie/Ordner",
        "Erkannt",
        "Serie",
        7,
        album="Albumtitel",
        tracks=[
            Track("Folge 012 - Spur.mp3", track="03/10", tags={"title": "Tagtitel"})
        ],
    )
    profile = NamingProfile(
        title_source="Audiotag Titel",
        series_source="Album",
        episode_source="Nummer aus Dateiname",
        filename="{modell}-track-{track:02}",
    )
    result = profile.render(book)
    assert result["title"] == "Tagtitel" and result["series"] == "Albumtitel"
    assert result["model"] == "albumtitel-012"
    assert result["filename"].endswith("track-03")
    assert book.episode == 7 and book.tracks[0].track == "03/10"


@pytest.mark.parametrize("name", ["CON", "nul.txt", "COM1", "..", ""])
def test_windows_reserved_names(name):
    with pytest.raises(ValueError):
        ascii_name(name)


@pytest.mark.parametrize("path", ["library/../outside", "C:/a", "library/CON"])
def test_unsafe_targets(path):
    with pytest.raises(ValueError):
        safe_relative(path)


def test_profiles_and_settings_roundtrip(tmp_path):
    settings = Settings(profiles=[asdict(NamingProfile(name="Eigene Regeln"))])
    settings.save(tmp_path / "settings.json")
    assert Settings.load(tmp_path / "settings.json").profile().name == "Eigene Regeln"


def test_job_snapshot_crash_recovery_and_association(tmp_path):
    path = tmp_path / "store.sqlite"
    store = Store(path)
    book = Book("1", "", "Titel")
    store.save_book(book)
    payload = {"book": book.to_dict(), "settings": asdict(Settings())}
    identifier = store.add_job("Import", payload)
    payload["book"]["title"] = "Später geändert"
    assert json.loads(store.jobs()[0]["payload"])["book"]["title"] == "Titel"
    store.update_job(identifier, "Aktiv", 0.5)
    recovered = Store(path)
    assert recovered.jobs()[0]["status"] == "Unterbrochen"
    assert recovered.books()[0].title == "Titel"
    recovered.associate("1", "http://server", "stable", "fingerprint")
