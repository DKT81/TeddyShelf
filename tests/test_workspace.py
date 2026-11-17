from pathlib import Path

import pytest

from teddystudio import jobs
from teddystudio.workspace import clean_inactive, clean_pcm


@pytest.mark.parametrize("fails", [False, True])
def test_execute_removes_pcm_on_success_and_error(tmp_path, monkeypatch, fails):
    def operation(kind, payload, work, log):
        (work / "source.pcm").write_bytes(b"decoded")
        (work / "source.partial.pcm").write_bytes(b"partial")
        (work / "finished.taf").write_bytes(b"keep")
        if fails:
            raise ValueError("conversion failed")
        return {"status": "done"}

    monkeypatch.setattr(jobs, "_execute", operation)
    if fails:
        with pytest.raises(ValueError, match="conversion failed"):
            jobs.execute("test", {}, tmp_path, lambda *args: None)
    else:
        jobs.execute("test", {}, tmp_path, lambda *args: None)
    assert not list(tmp_path.glob("*.pcm"))
    assert (tmp_path / "finished.taf").read_bytes() == b"keep"


def test_sweep_protects_active_work_and_outputs(tmp_path):
    root = tmp_path / "work"
    active, stopped = root / "active", root / "stopped"
    for folder in (active, stopped):
        folder.mkdir(parents=True)
        (folder / "source.pcm").write_bytes(b"1234")
        (folder / "result.mp3").write_bytes(b"keep")
    source = tmp_path / "original.pcm"
    source.write_bytes(b"source")
    with jobs.exclusive_lock(active / "audio-work.lock"):
        assert clean_inactive(root)[:2] == (1, 4)
        assert (active / "source.pcm").exists()
    assert source.read_bytes() == b"source"
    assert (stopped / "result.mp3").read_bytes() == b"keep"
    assert clean_inactive(root)[:2] == (1, 4)
    assert clean_inactive(root)[:2] == (0, 0)


def test_forced_stop_parent_cleanup(tmp_path):
    from teddystudio.queue import Worker

    (tmp_path / "part-1.pcm").write_bytes(b"partial")
    nested = tmp_path / "native-temp"
    nested.mkdir()
    (nested / "part.pcm").write_bytes(b"native")
    Worker._clean_temp({"request": {"work": str(tmp_path)}})
    assert not list(tmp_path.rglob("*.pcm"))
