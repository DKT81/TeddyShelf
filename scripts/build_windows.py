"""Build a portable, dynamically linked Qt application with audio tools included."""

from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
import argparse
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--onefile", action="store_true")
    parser.add_argument("--output-dir", type=Path)
    options = parser.parse_args()
    version = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"][
        "version"
    ]
    sys.path.insert(0, str(ROOT / "src"))
    from teddystudio import __version__

    if version != __version__:
        raise SystemExit("Project and application versions differ")
    output_dir = (options.output_dir or ROOT / "dist").resolve()
    name = f"TeddyShelf-{version}-Windows-x64" if options.onefile else "TeddyShelf"
    for tool_name in ("ffmpeg.exe", "ffprobe.exe"):
        if not (ROOT / "tools" / tool_name).is_file():
            raise SystemExit(f"Missing tools/{tool_name}. See docs/BUILD.md.")
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",
        "--onefile" if options.onefile else "--onedir",
        "--name",
        name,
        "--distpath",
        str(output_dir),
        "--workpath",
        str(ROOT / "build" / ("onefile" if options.onefile else "onedir")),
        "--specpath",
        str(ROOT / "build"),
        "--paths",
        str(ROOT / "src"),
        "--add-data",
        f"{ROOT / 'assets'};assets",
        "--add-data",
        f"{ROOT / 'src' / 'teddystudio' / 'locales'};teddystudio/locales",
        "--add-data",
        f"{ROOT / 'docs'};docs",
        "--add-data",
        f"{ROOT / 'LICENSE'};.",
        "--add-data",
        f"{ROOT / 'THIRD_PARTY.md'};.",
        "--add-binary",
        f"{ROOT / 'tools' / 'ffmpeg.exe'};tools",
        "--add-binary",
        f"{ROOT / 'tools' / 'ffprobe.exe'};tools",
        str(ROOT / "launch.py"),
    ]
    for folder, target in (
        (ROOT / "tools" / "web-tools", "tools/web-tools"),
        (ROOT / "artifacts" / "licenses", "licenses"),
    ):
        if folder.is_dir():
            command[3:3] = ["--add-data", f"{folder};{target}"]
        elif options.onefile:
            raise SystemExit(f"Missing {folder.name}; run scripts/build-windows.ps1")
    # Keep license and distribution metadata available in the portable package.
    for package in [
        "PySide6_Essentials",
        "PySide6_Addons",
        "shiboken6",
        "mutagen",
        "Pillow",
        "requests",
        "urllib3",
        "certifi",
        "charset-normalizer",
        "idna",
    ]:
        command[3:3] = ["--copy-metadata", package]
    # Build tools must not collect unrelated DLLs from the developer's PATH
    # (for example another program's incompatible ICU or Windows API shims).
    import PySide6
    import shiboken6

    qt_translation = Path(PySide6.__file__).parent / "translations" / "qtbase_de.qm"
    if not qt_translation.is_file():
        raise SystemExit("Missing Qt German translation catalog")
    command[3:3] = ["--add-data", f"{qt_translation};PySide6/translations"]

    environment = dict(os.environ)
    windows = Path(environment.get("SystemRoot", "C:/Windows"))
    environment["PATH"] = os.pathsep.join(
        str(path)
        for path in [
            Path(sys.base_prefix),
            Path(PySide6.__file__).parent,
            Path(shiboken6.__file__).parent,
            windows / "System32",
            windows,
        ]
    )
    subprocess.run(command, cwd=ROOT, env=environment, check=True)
    if options.onefile:
        executable = output_dir / (name + ".exe")
        with executable.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        executable.with_suffix(".exe.sha256").write_text(
            digest + "  " + executable.name + "\n", encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "exe": str(executable),
                    "sha256": digest,
                    "bytes": executable.stat().st_size,
                }
            )
        )
        return
    folder = output_dir / "TeddyShelf"
    shutil.copy2(ROOT / "README.md", folder)
    archive = output_dir / f"TeddyShelf-{version}-Windows-x64.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
        for path in folder.rglob("*"):
            if path.is_file():
                output.write(path, Path("TeddyShelf") / path.relative_to(folder))
    with zipfile.ZipFile(archive) as output:
        assert output.testzip() is None
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    archive.with_suffix(".sha256").write_text(digest + "  " + archive.name + "\n")
    print(
        json.dumps(
            {"archive": str(archive), "sha256": digest, "bytes": archive.stat().st_size}
        )
    )


if __name__ == "__main__":
    main()
