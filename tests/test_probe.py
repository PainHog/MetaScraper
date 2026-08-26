"""Unit tests for the pure ffprobe/exiftool parsing logic."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from metascraper import probe
from metascraper.probe import ToolLocation, media_info_from_payloads
from tests.sample_payloads import (
    IPHONE_EXIFTOOL,
    IPHONE_MOV_FFPROBE,
    WAV_FFPROBE,
)


def test_video_stream_normalization():
    info = media_info_from_payloads(
        "IMG_4021.MOV",
        ffprobe_data=IPHONE_MOV_FFPROBE,
        exiftool_data=IPHONE_EXIFTOOL,
    )
    assert info.media_kind == "Video"
    video = info.primary_video
    assert video is not None
    assert video.codec_name == "h264"
    assert video.width == 3840 and video.height == 2160
    assert video.resolution == "3840 × 2160"
    # 30000/1001 ≈ 29.97
    assert round(video.frame_rate, 2) == 29.97
    assert video.rotation == -90
    assert video.pixel_format == "yuv420p"


def test_audio_stream_normalization_and_messy_bitrate():
    info = media_info_from_payloads(
        "IMG_4021.MOV", ffprobe_data=IPHONE_MOV_FFPROBE
    )
    audio = info.primary_audio
    assert audio is not None
    assert audio.codec_name == "aac"
    assert audio.sample_rate == 44100
    assert audio.channels == 2
    # The intentionally-corrupt bit_rate string must coerce to None, not crash.
    assert audio.bit_rate is None


def test_container_and_duration():
    info = media_info_from_payloads(
        "IMG_4021.MOV", ffprobe_data=IPHONE_MOV_FFPROBE
    )
    assert info.container_format == "mov,mp4,m4a,3gp,3g2,mj2"
    assert info.container_long_name == "QuickTime / MOV"
    assert round(info.duration_seconds, 2) == 12.41
    assert info.size_bytes == 74691234


def test_device_provenance_from_tags_and_exiftool():
    info = media_info_from_payloads(
        "IMG_4021.MOV",
        ffprobe_data=IPHONE_MOV_FFPROBE,
        exiftool_data=IPHONE_EXIFTOOL,
    )
    assert info.camera_make == "Apple"
    assert info.camera_model == "iPhone 15 Pro"
    assert info.software == "17.4.1"
    assert "iPhone 15 Pro back triple camera" in info.lens_model
    assert info.recorded_at is not None
    assert info.recorded_at.year == 2024 and info.recorded_at.month == 5
    # ISO-6709 GPS should be picked up from container tags.
    assert info.gps_coordinates == "+34.0522-118.2437+015.000"


def test_device_provenance_without_exiftool_falls_back_to_tags():
    info = media_info_from_payloads(
        "IMG_4021.MOV", ffprobe_data=IPHONE_MOV_FFPROBE
    )
    # No exiftool payload -> values come from QuickTime container tags.
    assert info.camera_make == "Apple"
    assert info.camera_model == "iPhone 15 Pro"


def test_wav_audio():
    info = media_info_from_payloads("ZOOM0001.WAV", ffprobe_data=WAV_FFPROBE)
    assert info.media_kind == "Audio"
    assert info.primary_video is None
    audio = info.primary_audio
    assert audio.codec_name == "pcm_s24le"
    assert audio.sample_rate == 48000
    assert audio.bits_per_sample == 24
    row = info.master_row()
    assert row["sample_rate"] == "48 kHz"
    assert row["audio_codec"] == "pcm_s24le"
    assert row["media_kind"] == "Audio"


def test_master_row_shape():
    info = media_info_from_payloads(
        "IMG_4021.MOV",
        ffprobe_data=IPHONE_MOV_FFPROBE,
        exiftool_data=IPHONE_EXIFTOOL,
    )
    row = info.master_row()
    assert row["file_name"] == "IMG_4021.MOV"
    assert row["resolution"] == "3840 × 2160"
    assert row["frame_rate"] == "29.97 fps"
    assert row["camera_model"] == "iPhone 15 Pro"


def test_frame_rate_falls_back_when_avg_is_zero():
    # ffprobe reports avg_frame_rate '0/0' for many VFR/MKV streams; the real
    # rate lives in r_frame_rate and must not be lost.
    payload = {
        "streams": [{
            "index": 0, "codec_type": "video", "codec_name": "h264",
            "width": 1920, "height": 1080,
            "avg_frame_rate": "0/0", "r_frame_rate": "25/1",
            "tags": {},
        }],
        "format": {"format_name": "matroska", "duration": "5.0", "tags": {}},
    }
    info = media_info_from_payloads("clip.mkv", ffprobe_data=payload)
    assert info.primary_video.frame_rate == 25.0
    assert info.master_row()["frame_rate"] == "25 fps"


def test_explicit_tool_path_is_validated(monkeypatch):
    # A bad --ffprobe path must not be trusted; with nothing on PATH it resolves
    # to None so the caller can warn instead of failing on every file.
    monkeypatch.setattr(probe.shutil, "which", lambda *_a, **_k: None)
    tools = ToolLocation(ffprobe="/definitely/not/here/ffprobe")
    assert tools.ffprobe is None
    assert tools.has_ffprobe is False


def test_explicit_tool_path_accepts_real_executable(tmp_path, monkeypatch):
    monkeypatch.setattr(probe.shutil, "which", lambda *_a, **_k: None)
    fake = tmp_path / "ffprobe"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    tools = ToolLocation(ffprobe=str(fake))
    assert tools.ffprobe == str(fake)
    assert tools.has_ffprobe is True


def test_missing_ffprobe_still_produces_basic_record(tmp_path):
    media_file = tmp_path / "clip.mp4"
    media_file.write_bytes(b"\x00" * 2048)
    info = media_info_from_payloads(
        str(media_file),
        ffprobe_data={},
        notes=["ffprobe was not found"],
    )
    assert info.media_kind == "Video"
    assert info.size_bytes == 2048
    assert info.notes
    # A record is still emitted so nothing is silently dropped.
    assert info.master_row()["file_name"] == "clip.mp4"
