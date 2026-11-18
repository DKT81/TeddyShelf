"""Build a portable, dynamically linked Qt application with audio tools included."""

from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    for name in ("ffmpeg.exe", "ffprobe.exe"):
        if not (ROOT / "tools" / name).is_file():
            raise SystemExit(f"Missing tools/{name}. See docs/BUILD.md.")
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",
        "--onedir",
        "--name",
        "TeddyStudio",
        "--paths",
        str(ROOT / "src"),
        "--add-data",
        f"{ROOT / 'assets'};assets",
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
    folder = ROOT / "dist" / "TeddyStudio"
    shutil.copy2(ROOT / "README.md", folder)
    archive = ROOT / "dist" / "TeddyStudio-0.4.3-Windows-x64.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
        for path in folder.rglob("*"):
            if path.is_file():
                output.write(path, Path("TeddyStudio") / path.relative_to(folder))
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
