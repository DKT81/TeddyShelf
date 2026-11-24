"""Run the standalone EXE without Python/FFmpeg on PATH, using isolated data."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import shutil


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("exe", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    executable = args.exe.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    temporary = tempfile.TemporaryDirectory(
        prefix="isolated-", dir=output, ignore_cleanup_errors=True
    )
    run_directory = Path(temporary.name)
    environment = dict(os.environ)
    windows = Path(environment.get("SystemRoot", "C:/Windows"))
    environment["PATH"] = str(windows / "System32") + os.pathsep + str(windows)
    for name in (
        "PYTHONHOME",
        "PYTHONPATH",
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "TEDDYSHELF_WEB_TEST_TOOLS",
    ):
        environment.pop(name, None)
    environment["TEDDYSHELF_DATA"] = str(run_directory / "data")
    environment["TEDDYSHELF_WEB_TEST_TOOLS"] = str(run_directory / "data" / "web-tools")
    environment["QT_QPA_PLATFORM"] = "offscreen"
    completed = subprocess.run(
        [str(executable), "--self-test", str(run_directory)],
        cwd=run_directory,
        env=environment,
        check=False,
        timeout=300,
    )
    for path in run_directory.glob("GUI-*.png"):
        shutil.copy2(path, output / path.name)
    report = run_directory / "selftest.json"
    if report.is_file():
        shutil.copy2(report, output / "selftest.json")
    if completed.returncode or not report.is_file():
        raise RuntimeError(f"Standalone EXE self-test failed ({completed.returncode}); inspect {output}")
    result = json.loads((output / "selftest.json").read_text("utf-8"))
    assert result["passed"] and result["frozen"]
    assert result["parallel_queue"]["peak_active"] == 2
    assert (
        result["web_download"]["passed"]
        and result["web_download"]["collection_handoff"]
    )
    assert result["web_download"]["ordered_audio_handoff"]
    assert result["languages"] == ["de", "en"]
    with executable.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    expected = executable.with_suffix(".exe.sha256").read_text("utf-8").split()[0]
    assert digest == expected
    temporary.cleanup()
    print(json.dumps({"passed": True, "version": result["version"], "sha256": digest}))


if __name__ == "__main__":
    main()
