from pathlib import Path

from teddystudio.storage import Settings, Store
from teddystudio.ui_library import LibraryPage

# Save before the app fixture replaces sync_remote for deterministic GUI tests.
original_sync = LibraryPage.sync_remote


def test_fresh_install_has_no_server_and_separate_user_folders():
    settings = Settings()
    assert settings.url == ""
    assert Path(settings.source) == Path.home() / "Music" / "Hoerspiele"
    assert Path(settings.source) not in Path(settings.export).parents


def test_saved_configuration_is_preserved(tmp_path):
    path = tmp_path / "settings.json"
    configured = Settings(url="https://teddycloud.example", source="D:/Audio")
    configured.save(path)
    loaded = Settings.load(path)
    assert loaded.url == configured.url and loaded.source == configured.source


def test_unconfigured_collection_does_not_start_network_task(
    app, tmp_path, monkeypatch
):
    import teddystudio.ui_library as library

    def unexpected(*args, **kwargs):
        raise AssertionError("Unconfigured collection must not launch network work")

    monkeypatch.setattr(library, "background", unexpected)
    page = LibraryPage(Store(tmp_path / "library.sqlite"), Settings())
    original_sync(page)
    assert page.remote_models is None
    assert "Adresse" in page.remote_message
