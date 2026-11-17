"""Remove disposable decoded audio, never sources or finished audio exports."""

from pathlib import Path
import os


def clean_pcm(work):
    """Caller owns this job directory and must have stopped its native processes."""
    work = Path(work)
    removed, size, errors = 0, 0, []
    if not work.exists() or work.is_symlink() or work.is_junction():
        return removed, size, errors
    root = work.resolve()
    for directory, folders, files in os.walk(root, followlinks=False):
        folders[:] = [
            name
            for name in folders
            if not (Path(directory) / name).is_symlink()
            and not (Path(directory) / name).is_junction()
        ]
        for name in files:
            path = Path(directory) / name
            if path.suffix.lower() != ".pcm" or path.is_symlink():
                continue
            try:
                if not path.resolve().is_relative_to(root):
                    continue
                length = path.stat().st_size
                path.unlink()
                removed += 1
                size += length
            except OSError as exc:
                errors.append(f"{path.name}: {exc}")
    return removed, size, errors


def clean_inactive(root):
    """Only scan the application's work root; a per-job OS lock protects live jobs."""
    from .jobs import exclusive_lock

    root = Path(root)
    count, size, errors = 0, 0, []
    if not root.exists() or root.is_symlink() or root.is_junction():
        return count, size, errors
    resolved = root.resolve()
    for work in root.iterdir():
        if (
            not work.is_dir()
            or work.is_symlink()
            or work.is_junction()
            or work.resolve().parent != resolved
        ):
            continue
        try:
            with exclusive_lock(work / "audio-work.lock"):
                removed, length, failures = clean_pcm(work)
                count += removed
                size += length
                errors.extend(failures)
        except OSError:
            # Another process owns this job. Never remove its working audio.
            continue
    return count, size, errors
