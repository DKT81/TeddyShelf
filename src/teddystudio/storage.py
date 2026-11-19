"""Atomic settings and a transactional, restartable SQLite job journal."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
import contextlib
import json
import os
import sqlite3
import threading
import time
import uuid

from .domain import Book, NamingProfile


def data_directory() -> Path:
    # Keep the same journal/lock for existing installations; never split a library
    # merely because the program's display name changed.
    configured = os.environ.get("TEDDYSHELF_DATA") or os.environ.get("TEDDYSTUDIO_DATA")
    if configured:
        root = Path(configured)
    else:
        base = Path(os.environ.get("LOCALAPPDATA", Path.home()))
        current, legacy = base / "TeddyShelf", base / "TeddyStudio"

        def has_data(path):
            return any(
                (path / name).is_file() for name in ("settings.json", "library.sqlite")
            )

        root = legacy if not has_data(current) and has_data(legacy) else current
    root.mkdir(parents=True, exist_ok=True)
    return root


def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


@dataclass
class Settings:
    parallel_jobs: int = 1
    cloud_overlay: str = ""
    download_destination: str = field(
        default_factory=lambda: str(Path.home() / "Music" / "TeddyShelf-Downloads")
    )
    url: str = ""
    verify_tls: bool = True
    ca_file: str = ""
    source: str = field(
        default_factory=lambda: str(Path.home() / "Music" / "Hoerspiele")
    )
    export: str = field(
        default_factory=lambda: str(Path.home() / "Music" / "TeddyShelf-Export")
    )
    ffmpeg: str = ""
    ffprobe: str = ""
    theme: str = "System"
    language: str = "Deutsch"
    reopen_collection: bool = True
    show_unfinished: bool = True
    last_collection: str = ""
    cover_sources: list[str] = field(
        default_factory=lambda: ["Ordnerbild", "Eingebettetes Bild"]
    )
    cover_missing: str = "Leer lassen"
    cover_size: int = 800
    cover_format: str = "JPG"
    cover_upscale: bool = False
    preserve_manual_cover: bool = True
    embed_cover: bool = False
    audio_format: str = "mp3"
    audio_mode: str = "Separate Dateien / Custom-Tonies"
    audio_bitrate: int = 128
    taf_bitrate: int = 96
    split_seconds: int = 600
    timeout: int = 1800
    existing: str = "Prüfen und aktualisieren"
    default_profile: int = 0
    profiles: list[dict] = field(default_factory=lambda: [asdict(NamingProfile())])

    @classmethod
    def load(cls, path=None):
        path = Path(path or data_directory() / "settings.json")
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(**{k: v for k, v in raw.items() if k in cls.__dataclass_fields__})

    def save(self, path=None):
        atomic_json(Path(path or data_directory() / "settings.json"), asdict(self))

    def profile(self):
        return NamingProfile(**self.profiles[self.default_profile])


class Store:
    def __init__(self, path=None):
        self.path = Path(path or data_directory() / "library.sqlite")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        with self.connection() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS books(id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, kind TEXT NOT NULL,
                    status TEXT NOT NULL, payload TEXT NOT NULL, progress REAL DEFAULT 0,
                    message TEXT DEFAULT '', created REAL, updated REAL);
                CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, job TEXT,
                    timestamp REAL, stage TEXT, message TEXT);
                CREATE TABLE IF NOT EXISTS associations(source TEXT, server TEXT,
                    model TEXT, fingerprint TEXT, PRIMARY KEY(source,server));
            """)
            # A crash never establishes success. The worker revalidates its artifacts.
            db.execute(
                "UPDATE jobs SET status='Unterbrochen' WHERE status IN ('Aktiv','Abbruch angefordert','Wird gelöscht','Wird pausiert')"
            )

    @contextlib.contextmanager
    def connection(self):
        with self.lock:
            db = sqlite3.connect(self.path, timeout=30)
            db.row_factory = sqlite3.Row
            try:
                with db:
                    yield db
            finally:
                db.close()

    def save_book(self, book):
        self.save_books([book])

    def save_books(self, books):
        """Commit a scan in one transaction instead of one disk sync per title."""
        with self.connection() as db:
            db.executemany(
                "INSERT OR REPLACE INTO books VALUES (?,?)",
                [
                    (book.id, json.dumps(book.to_dict(), ensure_ascii=False))
                    for book in books
                ],
            )

    def books(self):
        with self.connection() as db:
            return [
                Book.from_dict(json.loads(r[0]))
                for r in db.execute("SELECT data FROM books")
            ]

    def add_job(self, kind, payload):
        identifier = uuid.uuid4().hex
        with self.connection() as db:
            db.execute(
                "INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?)",
                (
                    identifier,
                    kind,
                    "Wartet",
                    json.dumps(payload, ensure_ascii=False),
                    0,
                    "",
                    time.time(),
                    time.time(),
                ),
            )
        self.event(
            identifier,
            "Wartet",
            "Auftrag mit gespeichertem Einstellungsstand angelegt.",
        )
        return identifier

    def jobs(self):
        with self.connection() as db:
            return [
                dict(r) for r in db.execute("SELECT * FROM jobs ORDER BY created DESC")
            ]

    def update_job(self, identifier, status, progress=0, message=""):
        with self.connection() as db:
            db.execute(
                "UPDATE jobs SET status=?,progress=?,message=?,updated=? WHERE id=?",
                (status, progress, message, time.time(), identifier),
            )

    def delete_jobs(self, identifiers):
        """Delete journal entries/events atomically; caller must hold the worker lock."""
        identifiers = set(identifiers)
        with self.connection() as db:
            rows = list(db.execute("SELECT id,status FROM jobs"))
            selected = [r for r in rows if r["id"] in identifiers]
            if any(r["status"] == "Aktiv" for r in selected):
                raise ValueError("Laufende Aufträge können nicht gelöscht werden.")
            db.executemany(
                "DELETE FROM events WHERE job=?", [(r["id"],) for r in selected]
            )
            db.executemany(
                "DELETE FROM jobs WHERE id=?", [(r["id"],) for r in selected]
            )
        return len(selected)

    def event(self, identifier, stage, message):
        with self.connection() as db:
            db.execute(
                "INSERT INTO events(job,timestamp,stage,message) VALUES (?,?,?,?)",
                (identifier, time.time(), stage, str(message)),
            )

    def events(self, identifier=None):
        with self.connection() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM events"
                    + (" WHERE job=?" if identifier else "")
                    + " ORDER BY id",
                    (identifier,) if identifier else (),
                )
            ]

    def associate(self, source, server, model, fingerprint):
        with self.connection() as db:
            db.execute(
                "INSERT OR REPLACE INTO associations VALUES (?,?,?,?)",
                (source, server, model, fingerprint),
            )
