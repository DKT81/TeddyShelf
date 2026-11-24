"""Build policy checks; the workflow additionally executes the actual EXE."""

from pathlib import Path
import json
import hashlib
import shutil
import subprocess
import sys

import pytest

from teddystudio.web_tools import ToolManager

ROOT = Path(__file__).resolve().parents[1]


def test_powershell_forwards_python_switches_without_binding_common_parameters(
    tmp_path,
):
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if not shell:
        pytest.skip("PowerShell is required for the Windows build wrapper")
    source = (ROOT / "scripts/build-windows.ps1").read_text("utf-8")
    function = source.split("    function Invoke-BuildPython {", 1)[1].split(
        "    $script:buildPython = $PythonExe", 1
    )[0]
    recorder = tmp_path / "record arguments.py"
    recorder.write_text(
        "import sys,json; print(json.dumps(sys.argv[1:]))", encoding="utf-8"
    )

    def literal(value):
        return "'" + str(value).replace("'", "''") + "'"

    script = tmp_path / "check.ps1"
    script.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        + "function Invoke-BuildPython {"
        + function
        + "$script:buildPython = "
        + literal(sys.executable)
        + "\n"
        + "Invoke-BuildPython -Arguments @("
        + literal(recorder)
        + ", '-m', 'pip', 'install', '--no-deps', '-e', '.', 'path with spaces')\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-File", str(script)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout.strip()) == [
        "-m",
        "pip",
        "install",
        "--no-deps",
        "-e",
        ".",
        "path with spaces",
    ]


def test_bundled_tools_bootstrap_preserves_independent_updates(tmp_path):
    bundled = tmp_path / "bundled"
    bundled.mkdir()
    entries = {}
    for component in ("yt-dlp", "deno"):
        content = ("bundled-" + component).encode()
        filename = component + ".exe"
        (bundled / filename).write_bytes(content)
        entries[component] = {
            "filename": filename,
            "sha256": hashlib.sha256(content).hexdigest(),
            "version": "bundled",
            "channel": "stable",
        }
    (bundled / "manifest.json").write_text(json.dumps(entries), encoding="utf-8")
    manager = ToolManager(tmp_path / "user-data")
    manager.seed_bundled(bundled)
    state = manager.snapshot()
    assert all(
        Path(tool["path"]).is_relative_to(tmp_path / "user-data")
        for tool in state.values()
    )
    # Bundled binaries changing on an app update must not overwrite active tools.
    (bundled / "yt-dlp.exe").write_bytes(b"different app release")
    manager.seed_bundled(bundled)
    assert manager.snapshot() == state


def test_bootstrap_rejects_corrupt_bundled_executable(tmp_path):
    (tmp_path / "yt-dlp.exe").write_bytes(b"tampered")
    (tmp_path / "manifest.json").write_text(
        json.dumps({"yt-dlp": {"filename": "yt-dlp.exe", "sha256": "0" * 64}}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        ToolManager(tmp_path / "data").seed_bundled(tmp_path)


def test_windows_release_has_gated_publication_and_artifact():
    workflow = (ROOT / ".github/workflows/windows-build.yml").read_text("utf-8")
    assert "name: Windows Build & Release" in workflow
    assert "workflow_dispatch:" in workflow and "tags: ['v*']" in workflow
    assert workflow.count("contents: write") == 1
    build, publish = workflow.split("  publish:", 1)
    assert "contents: write" not in build
    assert "needs: build" in publish and "needs.build.result == 'success'" in publish
    assert "--generate-notes" in publish and "--prerelease" in publish
    assert "--draft" in publish and "sha256sum --check" in publish
    assert "actions/upload-artifact@" in build


def test_onefile_builder_keeps_application_name_and_writes_checksum(
    tmp_path, monkeypatch
):
    import importlib.util
    import sys
    from teddystudio import __version__

    specification = importlib.util.spec_from_file_location(
        "windows_builder", ROOT / "scripts/build_windows.py"
    )
    builder = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(builder)
    monkeypatch.setattr(builder, "ROOT", tmp_path)
    (tmp_path / "tools/web-tools").mkdir(parents=True)
    (tmp_path / "artifacts/licenses").mkdir(parents=True)
    for name in ("ffmpeg.exe", "ffprobe.exe"):
        (tmp_path / "tools" / name).write_bytes(b"tool")
    (tmp_path / "pyproject.toml").write_text(
        f'[project]\nversion = "{__version__}"\n', encoding="utf-8"
    )
    output = tmp_path / "release"
    monkeypatch.setattr(
        sys, "argv", ["build_windows.py", "--onefile", "--output-dir", str(output)]
    )
    expected = f"TeddyShelf-{__version__}-Windows-x64"

    def package(command, **kwargs):
        assert command[command.index("--name") + 1] == expected
        output.mkdir()
        (output / (expected + ".exe")).write_bytes(b"MZstandalone-test")

    monkeypatch.setattr(builder.subprocess, "run", package)
    builder.main()
    assert (output / (expected + ".exe")).is_file()
    assert (output / (expected + ".exe.sha256")).read_text("utf-8").split()[
        0
    ] == hashlib.sha256(b"MZstandalone-test").hexdigest()
