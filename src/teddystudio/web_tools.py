"""Verified side-by-side download tools, independent of the application release.

Only the active pointer changes on update/rollback. Queued/running jobs contain
immutable paths and hashes, so an update never replaces an executable in use.
"""

from pathlib import Path
from urllib.parse import urlsplit
import hashlib
import json
import os
import re
import shutil
import tempfile
import zipfile
import sys

import requests

from .storage import atomic_json, data_directory
from . import media

REPOSITORIES = {"stable": "yt-dlp/yt-dlp", "nightly": "yt-dlp/yt-dlp-nightly-builds"}
DENO_REPOSITORY = "denoland/deno"


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def release_url(url, repository):
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or not parsed.path.startswith("/" + repository + "/releases/download/")
    ):
        raise ValueError("Release-Datei stammt nicht aus dem offiziellen Repository")
    return url


class ToolManager:
    def __init__(self, root=None, session=None):
        self.root = Path(root or data_directory() / "web-tools")
        self.session = session or requests.Session()

    def state(self, seed=True):
        path = self.root / "active.json"
        state = json.loads(path.read_text("utf-8")) if path.exists() else {}
        if (
            seed
            and getattr(sys, "frozen", False)
            and any(name not in state for name in ("yt-dlp", "deno"))
        ):
            self.seed_bundled(Path(sys._MEIPASS) / "tools" / "web-tools")
            return self.state(seed=False)
        return state

    def seed_bundled(self, folder):
        """Install bundled starting versions into writable user data, once only."""
        from .jobs import exclusive_lock

        folder = Path(folder)
        manifest = folder / "manifest.json"
        if not manifest.is_file():
            return
        entries = json.loads(manifest.read_text("utf-8"))
        self.root.mkdir(parents=True, exist_ok=True)
        with exclusive_lock(self.root / "update.lock"):
            state = self.state(seed=False)
            for component in ("yt-dlp", "deno"):
                if component in state:
                    continue
                entry = entries[component]
                filename = component + ".exe"
                if entry.get("filename") != filename:
                    raise ValueError("Invalid bundled web tool filename")
                source = folder / filename
                self.verify({"path": str(source), "sha256": entry["sha256"]})
                target = self.root / "versions" / component / entry["sha256"] / filename
                target.parent.mkdir(parents=True, exist_ok=True)
                if not target.exists():
                    shutil.copy2(source, target)
                self.verify({"path": str(target), "sha256": entry["sha256"]})
                state[component] = {
                    key: value for key, value in entry.items() if key != "filename"
                }
                state[component]["path"] = str(target.resolve())
            atomic_json(self.root / "active.json", state)

    def snapshot(self):
        state = self.state()
        result = {}
        for name in ("yt-dlp", "deno"):
            if name not in state:
                raise ValueError(
                    "Web-Werkzeuge zuerst einrichten: yt-dlp und Deno fehlen oder sind unvollständig"
                )
            result[name] = state[name]
            self.verify(state[name])
        return result

    @staticmethod
    def verify(tool):
        path = Path(tool["path"])
        if not path.is_file() or sha256(path) != tool["sha256"]:
            raise ValueError("Web-Werkzeug fehlt oder wurde verändert: " + path.name)

    def release(self, component="yt-dlp", channel="stable"):
        if component == "deno":
            channel = "stable"
        repo = DENO_REPOSITORY if component == "deno" else REPOSITORIES[channel]
        response = self.session.get(
            f"https://api.github.com/repos/{repo}/releases/latest",
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "TeddyShelf",
            },
            timeout=(15, 45),
        )
        response.raise_for_status()
        data = response.json()
        if data.get("draft") or not isinstance(data.get("assets"), list):
            raise ValueError("Ungültige Release-Information")
        filename = (
            "deno-x86_64-pc-windows-msvc.zip" if component == "deno" else "yt-dlp.exe"
        )
        asset = next((a for a in data["assets"] if a["name"] == filename), None)
        checksum = next(
            (
                a
                for a in data["assets"]
                if a["name"]
                == (filename + ".sha256sum" if component == "deno" else "SHA2-256SUMS")
            ),
            None,
        )
        if not asset or not checksum:
            raise ValueError(
                "Release enthält kein passendes Windows-Werkzeug mit Prüfsumme"
            )
        return {
            "component": component,
            "channel": channel,
            "tag": data["tag_name"],
            "repository": repo,
            "asset": asset,
            "checksum": checksum,
        }

    def _fetch(self, asset, repository, target, limit, cancelled=lambda: False):
        url = release_url(asset["browser_download_url"], repository)
        total = 0
        with self.session.get(url, stream=True, timeout=(15, 90)) as response:
            response.raise_for_status()
            with Path(target).open("wb") as stream:
                for chunk in response.iter_content(256 * 1024):
                    if cancelled():
                        raise InterruptedError("Werkzeug-Update abgebrochen")
                    total += len(chunk)
                    if total > limit:
                        raise ValueError("Release-Datei überschreitet das Größenlimit")
                    stream.write(chunk)
        if not total:
            raise ValueError("Leere Release-Datei")
        digest = asset.get("digest")
        if digest and digest != "sha256:" + sha256(target):
            raise ValueError("GitHub-Release-Prüfsumme stimmt nicht")

    def install(self, release, progress=lambda message: None, cancelled=lambda: False):
        """Stage, hash-check, smoke-test, then atomically activate under a lock."""
        from .jobs import exclusive_lock

        component, repo = release["component"], release["repository"]
        expected_repo = (
            DENO_REPOSITORY if component == "deno" else REPOSITORIES[release["channel"]]
        )
        if component not in {"yt-dlp", "deno"} or repo != expected_repo:
            raise ValueError("Unbekannte Werkzeugquelle")
        self.state()
        self.root.mkdir(parents=True, exist_ok=True)
        with (
            exclusive_lock(self.root / "update.lock"),
            tempfile.TemporaryDirectory(dir=self.root) as temp,
        ):
            folder = Path(temp)
            progress("Release herunterladen und Prüfsumme vergleichen")
            archive, sums = folder / "asset", folder / "checksums"
            self._fetch(release["checksum"], repo, sums, 2_000_000, cancelled)
            self._fetch(release["asset"], repo, archive, 250_000_000, cancelled)
            name = release["asset"]["name"]
            lines = sums.read_text("utf-8").splitlines()
            candidates = [
                line.split()[0].lower()
                for line in lines
                if line.strip()
                and (len(line.split()) == 1 or line.split()[-1].lstrip("*") == name)
            ]
            # Windows Deno releases publish PowerShell Get-FileHash output,
            # rather than the GNU sha256sum format used by yt-dlp.
            if component == "deno":
                checksum_text = sums.read_text("utf-8")
                hash_match = re.search(
                    r"^Hash\s*:\s*([A-Fa-f0-9]{64})\s*$", checksum_text, re.M
                )
                path_match = re.search(r"^Path\s*:\s*(.+?)\s*$", checksum_text, re.M)
                if (
                    hash_match
                    and path_match
                    and path_match[1].replace("\\", "/").split("/")[-1] == name
                ):
                    candidates.append(hash_match[1].lower())
            digest = sha256(archive)
            if digest not in candidates:
                raise ValueError(
                    "Offizielle SHA256-Prüfsumme stimmt nicht; Update bleibt inaktiv"
                )
            executable = folder / (component + ".exe")
            if component == "deno":
                with zipfile.ZipFile(archive) as package:
                    entry = package.getinfo("deno.exe")
                    if entry.file_size > 300_000_000:
                        raise ValueError("Deno-Archiv ist zu groß")
                    with package.open(entry) as source, executable.open("wb") as output:
                        shutil.copyfileobj(source, output)
            else:
                archive.replace(executable)
            progress("Neue Version vor Aktivierung testen")
            version = media.run([executable, "--version"], timeout=30).strip()
            if component == "deno":
                match = re.search(r"deno (\d+)\.(\d+)\.(\d+)", version)
                if not match or tuple(map(int, match.groups())) < (2, 3, 0):
                    raise ValueError(
                        "Deno-Version wird nicht unterstützt (mindestens 2.3.0)"
                    )
                version = match.group(0)
            else:
                if not re.fullmatch(r"[0-9][0-9A-Za-z.+_-]{3,80}", version):
                    raise ValueError("Ungültige yt-dlp-Version")
                help_text = media.run(
                    [executable, "--ignore-config", "--help"], timeout=30
                )
                if any(
                    flag not in help_text
                    for flag in ("--js-runtimes", "--progress-template", "--print")
                ):
                    raise ValueError("yt-dlp unterstützt die benötigten Optionen nicht")
                if version != release["tag"].lstrip("v"):
                    raise ValueError("Programmversion passt nicht zum Release")
            digest = sha256(executable)
            destination = self.root / "versions" / component / digest / executable.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.exists():
                shutil.copy2(executable, destination)
            tool = {
                "path": str(destination.resolve()),
                "sha256": digest,
                "version": version,
                "channel": release["channel"],
                "repository": repo,
                "release": release["tag"],
            }
            self.verify(tool)
            state = self.state(seed=False)
            if cancelled():
                raise InterruptedError("Werkzeug-Update abgebrochen")
            if state.get(component) != tool:
                state.setdefault("previous", {})[component] = state.get(component)
                state[component] = tool
                atomic_json(self.root / "active.json", state)
            return tool

    def rollback(self, component="yt-dlp"):
        from .jobs import exclusive_lock

        self.state()
        with exclusive_lock(self.root / "update.lock"):
            state = self.state(seed=False)
            previous = state.get("previous", {}).get(component)
            if not previous:
                raise ValueError("Keine vorherige Werkzeugversion vorhanden")
            self.verify(previous)
            state["previous"][component], state[component] = state[component], previous
            atomic_json(self.root / "active.json", state)
            return previous
