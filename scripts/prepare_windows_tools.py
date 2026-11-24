"""Fetch verified Windows runtime tools and retain their release provenance."""

from pathlib import Path
import hashlib
import importlib.metadata
import json
import os
import shutil
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import requests
from teddystudio import media
from teddystudio.storage import atomic_json
from teddystudio.web_tools import ToolManager, sha256


class GitHubSession(requests.Session):
    def request(self, method, url, **kwargs):
        # Use the read-only Actions token only for API rate limits, never pass it
        # to release CDN hosts, yt-dlp, Deno, or any application runtime state.
        if url.startswith("https://api.github.com/") and os.environ.get("GH_TOKEN"):
            kwargs["headers"] = {
                **kwargs.get("headers", {}),
                "Authorization": "Bearer " + os.environ["GH_TOKEN"],
            }
        return super().request(method, url, **kwargs)


def ffmpeg(session, tool_root, licenses):
    response = session.get(
        "https://api.github.com/repos/FFmpeg-Builds/FFmpeg-Builds/releases/latest",
        timeout=(15, 60),
    )
    response.raise_for_status()
    release = response.json()
    repository = "/".join(release["html_url"].split("/")[3:5])
    if repository not in {"BtbN/FFmpeg-Builds", "FFmpeg-Builds/FFmpeg-Builds"}:
        raise ValueError("Unexpected FFmpeg release repository")
    asset = next(
        a
        for a in release["assets"]
        if a["name"] == "ffmpeg-master-latest-win64-gpl.zip"
    )
    checksums = next(a for a in release["assets"] if a["name"] == "checksums.sha256")
    fetcher = ToolManager(session=session)
    with tempfile.TemporaryDirectory(dir=ROOT / "artifacts") as temporary:
        folder = Path(temporary)
        archive, sums = folder / "ffmpeg.zip", folder / "checksums.sha256"
        fetcher._fetch(checksums, repository, sums, 2_000_000)
        fetcher._fetch(asset, repository, archive, 600_000_000)
        digest = sha256(archive)
        matching = [
            line.split()[0].lower()
            for line in sums.read_text("utf-8").splitlines()
            if len(line.split()) >= 2 and line.split()[-1].lstrip("*") == asset["name"]
        ]
        if digest not in matching:
            raise ValueError("FFmpeg release checksum mismatch")
        with zipfile.ZipFile(archive) as package:
            for name in ("ffmpeg.exe", "ffprobe.exe"):
                entry = next(
                    item
                    for item in package.infolist()
                    if item.filename.endswith("/bin/" + name)
                )
                if entry.file_size > 400_000_000:
                    raise ValueError("FFmpeg executable too large")
                temporary_executable = folder / name
                with (
                    package.open(entry) as source,
                    temporary_executable.open("wb") as target,
                ):
                    shutil.copyfileobj(source, target)
                temporary_executable.replace(tool_root / name)
            for entry in package.infolist():
                if (
                    not entry.is_dir()
                    and Path(entry.filename)
                    .name.lower()
                    .startswith(("license", "readme", "copying"))
                    and entry.file_size < 2_000_000
                ):
                    target = licenses / "ffmpeg" / Path(entry.filename).name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(package.read(entry))
    return {
        "repository": repository,
        "release": release["tag_name"],
        "release_id": release["id"],
        "url": asset["browser_download_url"],
        "sha256": digest,
        "version": media.run([tool_root / "ffmpeg.exe", "-version"], 30).splitlines()[
            0
        ],
    }


def collect_licenses(folder):
    packages = (
        "PySide6-Addons",
        "PySide6-Essentials",
        "shiboken6",
        "mutagen",
        "Pillow",
        "requests",
        "urllib3",
        "certifi",
        "charset-normalizer",
        "idna",
    )
    versions = {}
    for name in packages:
        distribution = importlib.metadata.distribution(name)
        versions[name] = distribution.version
        destination = folder / name
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "METADATA.txt").write_text(
            distribution.read_text("METADATA") or "", encoding="utf-8"
        )
        for relative in distribution.files or []:
            lower = str(relative).lower()
            if any(
                part in lower for part in ("license", "copying", "notice", "copyright")
            ):
                source = Path(distribution.locate_file(relative))
                if source.is_file() and source.stat().st_size < 5_000_000:
                    target = destination / str(relative).replace("..", "_")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
    for name in ("LICENSE.txt", "LICENSE"):
        path = Path(sys.base_prefix) / name
        if path.is_file():
            shutil.copy2(path, folder / "Python-LICENSE.txt")
            break
    for path in (
        ROOT / "LICENSE",
        ROOT / "THIRD_PARTY.md",
        ROOT / "assets" / "FONT-LICENSE.txt",
    ):
        shutil.copy2(path, folder / path.name)
    return versions


def main():
    if sys.platform != "win32":
        raise SystemExit("Windows tools must be prepared on Windows")
    tools, licenses = ROOT / "tools", ROOT / "artifacts" / "licenses"
    tools.mkdir(exist_ok=True)
    licenses.mkdir(parents=True, exist_ok=True)
    session = GitHubSession()
    provenance = {
        "python": sys.version.split()[0],
        "python_source": "https://www.python.org/downloads/source/",
        "source_commit": os.environ.get("GITHUB_SHA", "local"),
        "packages": collect_licenses(licenses),
    }
    provenance["ffmpeg"] = ffmpeg(session, tools, licenses)
    manager = ToolManager(ROOT / "artifacts" / "build-web-tools", session)
    bundled = tools / "web-tools"
    bundled.mkdir(exist_ok=True)
    components = {}
    for component in ("yt-dlp", "deno"):
        tool = manager.install(manager.release(component, "stable"))
        source = Path(tool["path"])
        shutil.copy2(source, bundled / source.name)
        components[component] = {
            key: value for key, value in tool.items() if key != "path"
        }
        components[component]["filename"] = source.name
    atomic_json(bundled / "manifest.json", components)
    provenance["web_tools"] = components
    for repo, filename in (
        ("yt-dlp/yt-dlp", "LICENSE"),
        ("yt-dlp/yt-dlp", "THIRD_PARTY_LICENSES.txt"),
        ("denoland/deno", "LICENSE.md"),
    ):
        component = "deno" if repo.startswith("denoland") else "yt-dlp"
        tag = components[component]["release"]
        response = session.get(
            f"https://raw.githubusercontent.com/{repo}/{tag}/{filename}",
            timeout=(15, 60),
        )
        response.raise_for_status()
        (licenses / (component + "-" + filename)).write_bytes(response.content)
    atomic_json(licenses / "PROVENANCE.json", provenance)
    atomic_json(ROOT / "artifacts" / "build-provenance.json", provenance)
    print("Prepared verified FFmpeg, FFprobe, yt-dlp, Deno and runtime license notices")


if __name__ == "__main__":
    main()
