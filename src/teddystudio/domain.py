"""Serializable domain objects and strictly separated naming rules."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
import hashlib
import re
import unicodedata

AUDIO_EXTENSIONS = {
    ".mp3",
    ".m4a",
    ".m4b",
    ".flac",
    ".wav",
    ".ogg",
    ".opus",
    ".aac",
    ".wma",
}


def ascii_name(value: str) -> str:
    """Keep German transliterations readable, then remove Windows-unsafe characters."""
    for before, after in {
        "ä": "ae",
        "ö": "oe",
        "ü": "ue",
        "Ä": "Ae",
        "Ö": "Oe",
        "Ü": "Ue",
        "ß": "ss",
    }.items():
        value = value.replace(before, after)
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    value = re.sub(r"[^a-zA-Z0-9 ._-]", "-", value).strip(" .-")
    if not value or value.upper().split(".")[0] in {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *[f"COM{i}" for i in range(1, 10)],
        *[f"LPT{i}" for i in range(1, 10)],
    }:
        raise ValueError("Der Name ist leer oder unter Windows reserviert.")
    if len(value) > 160:
        raise ValueError("Der Dateiname ist zu lang (maximal 160 Zeichen).")
    return value


def slug(value: str) -> str:
    return re.sub(r"[- ._]+", "-", ascii_name(value)).lower()


def natural_key(value):
    return [
        int(part) if part.isdigit() else part.casefold()
        for part in re.split(r"(\d+)", str(value))
    ]


def safe_relative(value: str) -> str:
    value = value.replace("\\", "/").strip("/")
    parts = PurePosixPath(value).parts
    if not parts or any(p in {"..", "."} or ":" in p for p in parts):
        raise ValueError("Ungültiger relativer Zielpfad.")
    return "/".join(ascii_name(part) for part in parts)


@dataclass
class Track:
    path: str
    title: str = ""
    track: str = ""
    duration: float = 0
    tags: dict = field(default_factory=dict)
    error: str = ""


@dataclass
class Book:
    id: str
    folder: str
    title: str
    series: str = ""
    episode: int | None = None
    album: str = ""
    artist: str = ""
    albumartist: str = ""
    genre: str = "Hörspiel"
    date: str = ""
    disc: str = ""
    language: str = "de"
    category: str = "audio-play"
    kind: str = "Hörspiel"
    cover: str = ""
    cover_source: str = ""
    cover_confirmed: bool = False
    origin: str = "Ordner / Audiotags"
    tracks: list[Track] = field(default_factory=list)
    status: str = "Neu"
    remote_model: str = ""
    order_pending: bool = False
    confirmed_order: list[str] = field(default_factory=list)

    @property
    def duration(self):
        return sum(track.duration for track in self.tracks)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        value = dict(value)
        value["tracks"] = [Track(**x) for x in value.get("tracks", [])]
        return cls(**value)


@dataclass
class NamingProfile:
    name: str = "Serien – Standard"
    model_numbered: str = "{serie_slug}-{folge:03}"
    model_unnumbered: str = "{serie_slug}-{titel_slug}"
    title: str = "{titel}"
    series: str = "{serie}"
    episodes: str = "{titel}"
    filename: str = "{modell}"
    audio_folder: str = "library/{serie_ordner}"
    image_folder: str = "custom_img/{serie_ordner}"
    split_filename: str = "{titel_ascii}_Teil_{teil:03}"
    language: str = "{sprache}"
    category: str = "{kategorie}"
    title_source: str = "Erkannter Titel"
    series_source: str = "Erkannte Serie"
    episode_source: str = "Erkannte Folge"

    def render(self, book: Book, part: int = 1) -> dict:
        first = book.tracks[0] if book.tracks else Track("")
        folder = Path(book.folder)
        title = {
            "Erkannter Titel": book.title,
            "Album": book.album,
            "Audiotag Titel": first.tags.get("title", first.title),
            "Ordnername": folder.name,
            "Dateiname": Path(first.path).stem,
        }.get(self.title_source, book.title) or book.title
        series = {
            "Erkannte Serie": book.series,
            "Album": book.album,
            "Übergeordneter Ordner": folder.parent.name,
            "Ordner oder Album": book.series or book.album,
            "Keine Serie": "Einzelhoerspiele",
        }.get(self.series_source, book.series) or "Einzelhoerspiele"
        episode = book.episode
        if self.episode_source == "Nummer aus Dateiname":
            match = re.search(
                r"(?:folge\s*|^|[_ -])(\d{1,3})(?:[_ .-]|$)",
                Path(first.path).stem,
                re.I,
            )
            episode = int(match[1]) if match else None
        values = {
            "serie": series,
            "titel": title,
            "folge": episode or 0,
            "serie_slug": slug(series),
            "titel_slug": slug(title),
            "serie_ordner": ascii_name(series.replace(" & ", "-")),
            "titel_ascii": ascii_name(title),
            "teil": part,
            "sprache": book.language,
            "kategorie": book.category,
            "album": book.album,
            "interpret": book.artist,
            "albuminterpret": book.albumartist,
            "genre": book.genre,
            "datum": book.date,
            "ordner": folder.name,
            "dateiname": Path(first.path).stem,
            "track": (
                int(first.track.split("/")[0])
                if first.track.split("/")[0].isdigit()
                else 0
            ),
        }
        try:
            model = ascii_name(
                (
                    self.model_numbered
                    if episode is not None
                    else self.model_unnumbered
                ).format_map(values)
            )
            values["modell"] = model
            result = {
                key: getattr(self, key).format_map(values)
                for key in (
                    "title",
                    "series",
                    "episodes",
                    "filename",
                    "audio_folder",
                    "image_folder",
                    "split_filename",
                    "language",
                    "category",
                )
            }
        except (KeyError, ValueError) as exc:
            raise ValueError(f"Ungültige Namensvorlage: {exc}") from exc
        result["model"] = model
        result["episode"] = episode
        for key in ("filename", "split_filename"):
            result[key] = ascii_name(result[key])
        for key, prefix in (
            ("audio_folder", "library/"),
            ("image_folder", "custom_img/"),
        ):
            result[key] = safe_relative(result[key])
            if not result[key].startswith(prefix):
                raise ValueError(f"{key} muss mit {prefix} beginnen.")
        return result


def source_fingerprint(book: Book) -> str:
    """Content hashes prevent a same-size/same-date source change going unnoticed."""
    digest = hashlib.sha256()
    for track in book.tracks:
        path = Path(track.path)
        digest.update(str(path.resolve()).encode())
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    return digest.hexdigest()
