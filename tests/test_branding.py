from teddystudio.storage import data_directory


def prepare(monkeypatch, tmp_path):
    monkeypatch.delenv("TEDDYSHELF_DATA", raising=False)
    monkeypatch.delenv("TEDDYSTUDIO_DATA", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))


def test_fresh_install_uses_new_name(monkeypatch, tmp_path):
    prepare(monkeypatch, tmp_path)
    assert data_directory() == tmp_path / "TeddyShelf"


def test_existing_library_keeps_its_journal_and_lock_directory(monkeypatch, tmp_path):
    prepare(monkeypatch, tmp_path)
    legacy = tmp_path / "TeddyStudio"
    legacy.mkdir()
    (legacy / "library.sqlite").write_bytes(b"existing journal")
    assert data_directory() == legacy
    assert (legacy / "library.sqlite").read_bytes() == b"existing journal"


def test_existing_new_configuration_wins_without_moving_old_data(monkeypatch, tmp_path):
    prepare(monkeypatch, tmp_path)
    for name in ("TeddyStudio", "TeddyShelf"):
        folder = tmp_path / name
        folder.mkdir()
        (folder / "settings.json").write_text("{}")
    assert data_directory() == tmp_path / "TeddyShelf"


def test_environment_overrides_remain_compatible(monkeypatch, tmp_path):
    prepare(monkeypatch, tmp_path)
    monkeypatch.setenv("TEDDYSTUDIO_DATA", str(tmp_path / "legacy-override"))
    assert data_directory() == tmp_path / "legacy-override"
    monkeypatch.setenv("TEDDYSHELF_DATA", str(tmp_path / "new-override"))
    assert data_directory() == tmp_path / "new-override"
