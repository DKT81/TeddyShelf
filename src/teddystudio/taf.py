"""Independent TAF writer: protobuf wire format, CRC-checked Ogg and Opus padding.

No TonieToolbox imports. FFmpeg performs Opus encoding; repagination changes only
packet framing/padding, never compressed frame bytes. See docs/FORMAT.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import hashlib
import struct
import tempfile

from .media import binary, decoded_duration, run


def varint(value):
    result = bytearray()
    while value > 127:
        result.append((value & 127) | 128)
        value >>= 7
    return bytes(result + bytes([value]))


def read_varint(data, offset):
    value = 0
    for shift in range(0, 70, 7):
        if offset >= len(data):
            raise ValueError("Abgeschnittenes Protobuf-Feld")
        byte = data[offset]
        offset += 1
        value |= (byte & 127) << shift
        if byte < 128:
            return value, offset
    raise ValueError("Zu langes Protobuf-Feld")


def field(number, value):
    if isinstance(value, bytes):
        return varint(number * 8 + 2) + varint(len(value)) + value
    return varint(number * 8) + varint(value)


def header(payload_hash, size, audio_id, chapters):
    raw = (
        field(1, payload_hash)
        + field(2, size)
        + field(3, audio_id)
        + field(4, b"".join(varint(x) for x in chapters))
    )
    for padding in range(4092 - len(raw), -1, -1):
        candidate = raw + field(5, bytes(padding))
        if len(candidate) == 4092:
            return struct.pack(">I", 4092) + candidate
    raise ValueError("Zu viele Kapitel für einen TAF-Header")


CRC_TABLE = []
for _index in range(256):
    _crc = _index << 24
    for _ in range(8):
        _crc = ((_crc << 1) ^ (0x04C11DB7 if _crc & 0x80000000 else 0)) & 0xFFFFFFFF
    CRC_TABLE.append(_crc)


def crc(data):
    value = 0
    for byte in data:
        value = ((value << 8) ^ CRC_TABLE[((value >> 24) ^ byte) & 255]) & 0xFFFFFFFF
    return value


@dataclass
class Page:
    raw: bytes
    sequence: int
    granule: int
    flags: int
    serial: int
    packets: list[bytes]


def pages(data, verify_crc=True):
    offset = 0
    pending = bytearray()
    while offset < len(data):
        if data[offset : offset + 5] != b"OggS\x00" or offset + 27 > len(data):
            raise ValueError("Ungültige Ogg-Seite")
        count = data[offset + 26]
        segments = data[offset + 27 : offset + 27 + count]
        if len(segments) != count:
            raise ValueError("Abgeschnittene Ogg-Segmenttabelle")
        size = 27 + count + sum(segments)
        raw = data[offset : offset + size]
        if len(raw) != size:
            raise ValueError("Abgeschnittene Ogg-Seite")
        if (
            verify_crc
            and crc(raw[:22] + bytes(4) + raw[26:])
            != struct.unpack_from("<I", raw, 22)[0]
        ):
            raise ValueError("Ogg-Prüfsumme stimmt nicht")
        packet_list, position = [], 27 + count
        for segment in segments:
            pending.extend(raw[position : position + segment])
            position += segment
            if segment < 255:
                packet_list.append(bytes(pending))
                pending.clear()
        yield Page(
            raw,
            struct.unpack_from("<I", raw, 18)[0],
            struct.unpack_from("<Q", raw, 6)[0],
            raw[5],
            struct.unpack_from("<I", raw, 14)[0],
            packet_list,
        )
        offset += size
    if pending:
        raise ValueError("Unvollständiges letztes Ogg-Paket")


def lace(packet):
    return bytes([255] * (len(packet) // 255) + [len(packet) % 255])


def make_page(packets, sequence, granule, serial, flags=0):
    segments = b"".join(lace(p) for p in packets)
    if len(segments) > 255:
        raise ValueError("Zu viele Segmente")
    raw = (
        b"OggS\x00"
        + bytes([flags])
        + struct.pack("<QII", granule, serial, sequence)
        + bytes(4)
        + bytes([len(segments)])
        + segments
        + b"".join(packets)
    )
    return raw[:22] + struct.pack("<I", crc(raw)) + raw[26:]


def pad_single_frame(packet, target):
    """CBR 20-ms output uses Opus code 0; convert to code 3 plus RFC 6716 padding."""
    if packet[0] & 3:
        raise ValueError("Encoder lieferte unerwartetes Opus-Framing")
    frame = packet[1:]
    for padding in range(max(0, target - len(packet) - 64), target):
        encoded = bytes([255]) * (padding // 254) + bytes([padding % 254])
        result = bytes([(packet[0] & 252) | 3, 0x41]) + encoded + frame + bytes(padding)
        if len(result) == target:
            # The compressed frame is exactly the source frame, not re-encoded audio.
            assert (
                result[2 + len(encoded) : len(result) - padding if padding else None]
                == frame
            )
            return result
        if len(result) > target:
            break
    raise ValueError("Opus-Padding passt nicht")


def fit_page(packets, size, sequence, granule, serial, flags=0):
    original = packets[-1]
    base = 27 + sum(len(lace(p)) + len(p) for p in packets[:-1])
    for last_size in range(max(len(original) + 2, size - base - 20), size - base):
        if base + last_size + last_size // 255 + 1 == size:
            padded = pad_single_frame(original, last_size)
            result = make_page(
                [*packets[:-1], padded], sequence, granule, serial, flags
            )
            if len(result) == size:
                return result
    raise ValueError("Ogg-Seite konnte nicht blockgenau gefüllt werden")


def create(pcm, output, chapter_seconds, ffmpeg="", bitrate=96, timeout=1800):
    """Encode a normalized 48-kHz stereo PCM source and atomically publish a valid TAF."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    # A content-derived ID is stable for retries and separate from editable model names.
    with Path(pcm).open("rb") as source:
        audio_id = (
            int.from_bytes(hashlib.file_digest(source, "sha256").digest()[:4], "big")
            | 0x50000000
        )
    with tempfile.TemporaryDirectory(prefix="teddystudio-taf-") as directory:
        opus = Path(directory) / "encoded.opus"
        run(
            [
                binary("ffmpeg", ffmpeg),
                "-v",
                "error",
                "-y",
                "-f",
                "s16le",
                "-ar",
                "48000",
                "-ac",
                "2",
                "-i",
                pcm,
                "-c:a",
                "libopus",
                "-b:a",
                f"{bitrate}k",
                "-vbr",
                "off",
                "-frame_duration",
                "20",
                opus,
            ],
            timeout,
        )
        encoded_pages = list(pages(opus.read_bytes()))
        packets = [p for page in encoded_pages for p in page.packets]
        if not packets[0].startswith(b"OpusHead") or not packets[1].startswith(
            b"OpusTags"
        ):
            raise ValueError("Encoder lieferte kein Ogg/Opus")
        final_granule = encoded_pages[-1].granule
        preskip = struct.unpack_from("<H", packets[0], 10)[0]
        head = make_page([packets[0]], 0, 0, audio_id, 2)
        # Header pages occupy 512 bytes; the first audio page fills the 4096 block.
        tags = (
            b"OpusTags" + struct.pack("<I", 11) + b"TeddyStudio" + struct.pack("<I", 0)
        )
        for length in range(400, 512):
            comments = tags + bytes(max(0, length - len(tags)))
            tag_page = make_page([comments], 1, 0, audio_id)
            if len(head) + len(tag_page) == 512:
                break
        else:
            raise ValueError("Ogg-Metadatenblock passt nicht")
        payload = bytearray(head + tag_page)
        audio = packets[2:]
        index, sequence, samples = 0, 2, 0
        chapter_pages = [0]
        remaining_chapters = list(chapter_seconds[1:])
        while index < len(audio):
            start = max(0, samples - preskip) / 48000
            while remaining_chapters and start >= remaining_chapters[0]:
                chapter_pages.append(sequence)
                remaining_chapters.pop(0)
            target = 3584 if sequence == 2 else 4096
            selected = []
            while index < len(audio):
                next_start = max(0, samples + len(selected) * 960 - preskip) / 48000
                if (
                    selected
                    and remaining_chapters
                    and next_start >= remaining_chapters[0]
                ):
                    break
                packet = audio[index]
                estimate = 27 + sum(len(p) + len(lace(p)) for p in [*selected, packet])
                if estimate + 16 > target:
                    break
                selected.append(packet)
                index += 1
            if not selected:
                raise ValueError("Opus-Paket ist zu groß")
            samples += len(selected) * 960
            granule = min(samples, final_granule)
            payload.extend(
                fit_page(
                    selected,
                    target,
                    sequence,
                    granule,
                    audio_id,
                    4 if index == len(audio) else 0,
                )
            )
            sequence += 1
        # A very short final chapter must still have a distinct physical page.
        if remaining_chapters or len(set(chapter_pages)) != len(chapter_seconds):
            raise ValueError(
                "Kapitel sind kürzer als eine Ogg-Seite; größere Abschnittslänge wählen."
            )
        temporary = output.with_suffix(".partial.taf")
        temporary.write_bytes(
            header(
                hashlib.sha1(payload).digest(), len(payload), audio_id, chapter_pages
            )
            + payload
        )
        validate(
            temporary,
            ffmpeg,
            expected=Path(pcm).stat().st_size / 192000,
            chapters=len(chapter_seconds),
            timeout=timeout,
        )
        temporary.replace(output)
    return validate_header(output)


def validate_header(path):
    raw = Path(path).read_bytes()
    if len(raw) < 4096 or struct.unpack_from(">I", raw)[0] != 4092:
        raise ValueError("Ungültiger TAF-Header")
    data, offset, values = raw[4:4096], 0, {}
    while offset < len(data):
        key, offset = read_varint(data, offset)
        number, wire = key >> 3, key & 7
        if wire == 0:
            value, offset = read_varint(data, offset)
        elif wire == 2:
            length, offset = read_varint(data, offset)
            value = data[offset : offset + length]
            if len(value) != length:
                raise ValueError("Abgeschnittener Header")
            offset += length
        else:
            raise ValueError("Unbekanntes TAF-Headerfeld")
        values[number] = value
    payload = raw[4096:]
    if values.get(2) != len(payload) or values.get(1) != hashlib.sha1(payload).digest():
        raise ValueError("TAF-Datenlänge oder SHA1 stimmt nicht")
    chapter_data, position, chapters = values.get(4, b""), 0, []
    while position < len(chapter_data):
        value, position = read_varint(chapter_data, position)
        chapters.append(value)
    parsed = list(pages(payload))
    if (
        not parsed
        or not chapters
        or chapters[0] != 0
        or chapters != sorted(set(chapters))
        or chapters[-1] >= len(parsed)
    ):
        raise ValueError("Ungültige TAF-Kapitel")
    if not parsed[0].packets[0].startswith(b"OpusHead") or not parsed[-1].flags & 4:
        raise ValueError("Ungültiger Opus-Anfang oder unvollständiges Ende")
    for index, page in enumerate(parsed):
        if page.sequence != index or page.serial != values.get(3):
            raise ValueError("Ogg-Sequenz oder Audio-ID stimmt nicht")
    return {
        "audioId": str(values[3]),
        "sha1Hash": values[1].hex(),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "chapters": len(chapters),
        "chapterPages": chapters,
        "bytes": len(raw),
    }


def validate(path, ffmpeg="", expected=None, chapters=None, timeout=1800):
    info = validate_header(path)
    duration = decoded_duration(path, ffmpeg, taf=True, timeout=timeout)
    if expected is not None and abs(duration - expected) > max(0.15, expected * 0.001):
        raise ValueError(f"TAF-Dauer stimmt nicht: {duration:.3f} statt {expected:.3f}")
    if chapters is not None and info["chapters"] != chapters:
        raise ValueError("TAF-Kapitelzahl stimmt nicht")
    return {**info, "duration": duration}
