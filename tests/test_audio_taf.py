from pathlib import Path
import struct
import pytest
from teddystudio import media, taf


def test_split_plan_preserves_remainder():
    plan = media.split_plan(5022, 600)
    assert len(plan) == 9 and plan[-1] == (4800, 222)
    assert sum(length for _, length in plan) == 5022
    assert len(media.split_plan(1200, 600)) == 2
    with pytest.raises(ValueError):
        media.split_plan(1200, 0)


@pytest.mark.parametrize("bitrate", [32, 64, 96, 128])
def test_standalone_taf_integrity_duration_chapters_and_blocks(
    sample, tmp_path, bitrate
):
    pcm, target = tmp_path / "source.pcm", tmp_path / "audio.taf"
    media.concat_pcm([sample], pcm)
    taf.create(pcm, target, [0, 2], bitrate=bitrate)
    info = taf.validate(target, expected=4.2, chapters=2)
    assert info["duration"] == pytest.approx(4.2, abs=0.02)
    raw = target.read_bytes()
    assert len(raw) % 4096 == 0
    pages = list(taf.pages(raw[4096:]))
    tags = pages[1].packets[0]
    vendor_length = struct.unpack_from("<I", tags, 8)[0]
    assert tags[12 : 12 + vendor_length] == b"TeddyShelf"
    preskip = struct.unpack_from("<H", pages[0].packets[0], 10)[0]
    chapter_start = (pages[info["chapterPages"][1] - 1].granule - preskip) / 48000
    assert 2 <= chapter_start <= 2.02
    assert len(pages[0].raw) + len(pages[1].raw) == 512
    assert len(pages[2].raw) == 3584
    assert all(len(page.raw) == 4096 for page in pages[3:])
    bad = bytearray(raw)
    bad[-4] ^= 1
    target.write_bytes(bad)
    with pytest.raises(ValueError, match="SHA1"):
        taf.validate_header(target)


def test_crc_and_truncation_rejected():
    page = taf.make_page([b"example"], 0, 0, 7)
    assert list(taf.pages(page))[0].packets == [b"example"]
    with pytest.raises(ValueError):
        list(taf.pages(page[:-1]))
    corrupt = bytearray(page)
    corrupt[-1] ^= 1
    with pytest.raises(ValueError, match="Prüfsumme"):
        list(taf.pages(corrupt))


@pytest.mark.parametrize("target", [244, 254, 255, 256, 500, 1000, 3900])
def test_opus_padding_preserves_compressed_frame(target):
    frame = bytes(range(239))
    packet = bytes([0xFC]) + frame
    padded = taf.pad_single_frame(packet, target)
    index, padding = 2, 0
    while True:
        value = padded[index]
        index += 1
        padding += 254 if value == 255 else value
        if value < 255:
            break
    assert padded[index : len(padded) - padding if padding else None] == frame
    assert len(padded) == target


@pytest.mark.parametrize("fmt", ["wav", "mp3", "opus", "flac"])
def test_accurate_split_formats(sample, tmp_path, fmt):
    pcm = tmp_path / "source.pcm"
    media.concat_pcm([sample], pcm)
    durations = []
    for index, (start, length) in enumerate(media.split_plan(4.2, 2)):
        durations.append(
            media.export_segment(
                pcm, tmp_path / f"part-{index}.{fmt}", start, length, fmt
            )
        )
    assert sum(durations) == pytest.approx(4.2, abs=0.15)


def test_mixed_sample_rates_and_waveform(sample, tmp_path):
    other = tmp_path / "mono.flac"
    media.run(
        [
            media.binary("ffmpeg"),
            "-v",
            "error",
            "-y",
            "-i",
            sample,
            "-ar",
            "22050",
            "-ac",
            "1",
            other,
        ]
    )
    pcm = tmp_path / "combined.pcm"
    media.concat_pcm([sample, other], pcm)
    assert pcm.stat().st_size / 192000 == pytest.approx(8.4, abs=0.02)
    peaks = media.waveform([sample, other], points=100)
    assert 0 < len(peaks) <= 100 and max(peaks) > 0.03
