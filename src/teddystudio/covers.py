"""Cover provenance, safe image downloads and explicit tag embedding."""

from pathlib import Path
from io import BytesIO
import base64
import hashlib
import json
import mutagen
import requests
from PIL import Image, ImageOps, ImageDraw


def checked_image(source):
    with Image.open(source) as image:
        if image.width * image.height > 40_000_000:
            raise ValueError("Bild ist zu groß (maximal 40 Megapixel).")
        image.load()
        return ImageOps.exif_transpose(image).convert("RGB")


def embedded(path):
    audio = mutagen.File(path)
    if audio is None:
        return None
    if getattr(audio, "pictures", None):
        return audio.pictures[0].data
    if audio.tags:
        for key in audio.tags.keys():
            value = audio.tags[key]
            if str(key).startswith("APIC"):
                return value.data
            if key == "covr":
                return bytes(value[0])
            if key == "metadata_block_picture":
                from mutagen.flac import Picture

                return Picture(base64.b64decode(value[0])).data
    return None


def discover_cover(book, settings, cache):
    for source in settings.cover_sources:
        if source == "Ordnerbild":
            for path in sorted(Path(book.folder).iterdir()):
                if path.stem.lower() in {
                    "cover",
                    "folder",
                    "front",
                } and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
                    try:
                        checked_image(path)
                        return str(path), f"Ordnerbild: {path.name}"
                    except Exception:
                        continue
        elif source == "Eingebettetes Bild":
            for track in book.tracks:
                try:
                    data = embedded(track.path)
                    if data:
                        image = checked_image(BytesIO(data))
                        target = Path(cache) / (book.id + ".jpg")
                        image.save(target, "JPEG", quality=92)
                        return (
                            str(target),
                            f"Eingebettetes Bild: {Path(track.path).name}",
                        )
                except Exception:
                    continue
    return "", "Kein Cover"


def download(url, output):
    if not url.startswith(("https://", "http://")):
        raise ValueError("Eine HTTP(S)-Bildadresse ist erforderlich.")
    with requests.get(url, timeout=(10, 30), stream=True) as response:
        response.raise_for_status()
        data = bytearray()
        for chunk in response.iter_content(65536):
            data.extend(chunk)
            if len(data) > 20_000_000:
                raise ValueError("Cover-Download größer als 20 MB")
    checked_image(BytesIO(data)).save(output, "JPEG", quality=92)
    return str(output)


def prepare(source, output, settings):
    if source:
        image = checked_image(source)
    elif settings.cover_missing == "Neutrales Symbol":
        image = Image.new("RGB", (800, 800), "#e8edeb")
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle(
            (200, 230, 600, 580), radius=40, outline="#277c76", width=18
        )
        draw.ellipse((275, 290, 525, 540), outline="#277c76", width=18)
    else:
        return None
    size = settings.cover_size
    if settings.cover_upscale and max(image.size) < size:
        factor = size / max(image.size)
        image = image.resize(
            (round(image.width * factor), round(image.height * factor)),
            Image.Resampling.LANCZOS,
        )
    image.thumbnail((size, size), Image.Resampling.LANCZOS)
    image.save(
        output,
        "PNG" if settings.cover_format == "PNG" else "JPEG",
        **({} if settings.cover_format == "PNG" else {"quality": 92}),
    )
    return Path(output)


def embed_image(path, cover):
    data = Path(cover).read_bytes()
    mime = "image/png" if data.startswith(b"\x89PNG") else "image/jpeg"
    audio = mutagen.File(path)
    if audio is None:
        raise ValueError("Audioformat unterstützt keine Cover-Tags.")
    if audio.tags is None:
        audio.add_tags()
    if path.suffix.lower() == ".mp3":
        from mutagen.id3 import APIC

        audio.tags.delall("APIC")
        audio.tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=data))
    elif path.suffix.lower() in {".m4a", ".m4b"}:
        from mutagen.mp4 import MP4Cover

        audio["covr"] = [
            MP4Cover(
                data,
                imageformat=(
                    MP4Cover.FORMAT_PNG if mime == "image/png" else MP4Cover.FORMAT_JPEG
                ),
            )
        ]
    else:
        from mutagen.flac import Picture

        picture = Picture()
        picture.data, picture.mime, picture.type = data, mime, 3
        if hasattr(audio, "clear_pictures"):
            audio.clear_pictures()
            audio.add_picture(picture)
        else:
            audio["metadata_block_picture"] = [
                base64.b64encode(picture.write()).decode()
            ]
    audio.save()
