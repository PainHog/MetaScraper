"""Normalized data model for a single media file's metadata."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from . import utils


VIDEO_EXTENSIONS = {
    ".mp4", ".mov", ".m4v", ".avi", ".mkv", ".mts", ".m2ts", ".ts", ".wmv",
    ".flv", ".webm", ".mpg", ".mpeg", ".3gp", ".3g2", ".mxf", ".insv", ".braw",
    ".r3d", ".vob", ".ogv", ".divx", ".f4v", ".m2v",
}

AUDIO_EXTENSIONS = {
    ".wav", ".mp3", ".flac", ".aac", ".m4a", ".aiff", ".aif", ".ogg", ".oga",
    ".opus", ".wma", ".alac", ".ape", ".ac3", ".dts", ".amr", ".caf", ".mp2",
    ".w64", ".bwf",
}


def classify_extension(ext: str) -> str:
    """Return ``"Video"``, ``"Audio"`` or ``"Other"`` for a file extension."""
    lowered = ext.lower()
    if lowered in VIDEO_EXTENSIONS:
        return "Video"
    if lowered in AUDIO_EXTENSIONS:
        return "Audio"
    return "Other"


@dataclass
class StreamSummary:
    """A normalized, display-ready summary of one media stream."""

    index: Optional[int]
    codec_type: str
    codec_name: Optional[str] = None
    codec_long_name: Optional[str] = None
    profile: Optional[str] = None
    # video
    width: Optional[int] = None
    height: Optional[int] = None
    display_aspect_ratio: Optional[str] = None
    pixel_format: Optional[str] = None
    frame_rate: Optional[float] = None
    color_space: Optional[str] = None
    color_transfer: Optional[str] = None
    color_primaries: Optional[str] = None
    field_order: Optional[str] = None
    rotation: Optional[int] = None
    # audio
    sample_rate: Optional[int] = None
    channels: Optional[int] = None
    channel_layout: Optional[str] = None
    bits_per_sample: Optional[int] = None
    # shared
    bit_rate: Optional[float] = None
    duration_seconds: Optional[float] = None
    language: Optional[str] = None
    handler_name: Optional[str] = None
    tags: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_attached_picture(self) -> bool:
        """True for cover art that ffprobe reports as a one-frame video stream."""
        disposition = self.raw.get("disposition") or {}
        return str(disposition.get("attached_pic", 0)) == "1"

    @property
    def resolution(self) -> Optional[str]:
        if self.width and self.height:
            return f"{self.width} × {self.height}"
        return None


@dataclass
class MediaInfo:
    """Everything MetaScraper knows about one media file."""

    # Filesystem
    path: str
    name: str
    ext: str
    media_kind: str
    size_bytes: Optional[int] = None
    fs_created: Optional[datetime] = None
    fs_modified: Optional[datetime] = None

    # Container / format
    container_format: Optional[str] = None
    container_long_name: Optional[str] = None
    duration_seconds: Optional[float] = None
    overall_bit_rate: Optional[float] = None
    format_tags: Dict[str, Any] = field(default_factory=dict)

    # Streams
    streams: List[StreamSummary] = field(default_factory=list)

    # Device / provenance (best-effort from tags + exiftool)
    camera_make: Optional[str] = None
    camera_model: Optional[str] = None
    lens_model: Optional[str] = None
    software: Optional[str] = None
    recorded_at: Optional[datetime] = None
    gps_coordinates: Optional[str] = None
    title: Optional[str] = None
    artist: Optional[str] = None
    comment: Optional[str] = None

    # Raw payloads (for the "keep ALL the information" dump)
    ffprobe_raw: Dict[str, Any] = field(default_factory=dict)
    exiftool_raw: Dict[str, Any] = field(default_factory=dict)

    # Diagnostics
    notes: List[str] = field(default_factory=list)

    # --- Derived convenience accessors -----------------------------------

    @property
    def video_streams(self) -> List[StreamSummary]:
        """Real video streams — embedded cover art is left out."""
        return [s for s in self.streams
                if s.codec_type == "video" and not s.is_attached_picture]

    @property
    def audio_streams(self) -> List[StreamSummary]:
        return [s for s in self.streams if s.codec_type == "audio"]

    @property
    def primary_video(self) -> Optional[StreamSummary]:
        # Prefer a real video stream over an attached cover-art image stream.
        candidates = [
            s for s in self.video_streams
            if s.codec_name not in ("mjpeg", "png", "bmp", "gif")
        ]
        chosen = candidates or self.video_streams
        return chosen[0] if chosen else None

    @property
    def primary_audio(self) -> Optional[StreamSummary]:
        streams = self.audio_streams
        return streams[0] if streams else None

    @property
    def size_human(self) -> str:
        return utils.human_file_size(self.size_bytes)

    @property
    def duration_human(self) -> str:
        return utils.human_duration(self.duration_seconds)

    # --- Row for the master catalog --------------------------------------

    def master_row(self) -> Dict[str, Any]:
        """Return the flat record stored in the master catalog."""
        video = self.primary_video
        audio = self.primary_audio
        return {
            "file_name": self.name,
            "media_kind": self.media_kind,
            "folder": _parent_folder(self.path),
            "duration": self.duration_human,
            "duration_seconds": self.duration_seconds,
            "file_size": self.size_human,
            "size_bytes": self.size_bytes,
            "container": self.container_format or self.ext.lstrip(".").upper(),
            "video_codec": (video.codec_name if video else None),
            "resolution": (video.resolution if video else None),
            "frame_rate": (
                utils.human_frame_rate(video.frame_rate)
                if video and video.frame_rate else None
            ),
            "video_bitrate": (
                utils.human_bitrate(video.bit_rate) if video and video.bit_rate else None
            ),
            "audio_codec": (audio.codec_name if audio else None),
            "sample_rate": (
                utils.human_sample_rate(audio.sample_rate)
                if audio and audio.sample_rate else None
            ),
            "channels": (audio.channels if audio else None),
            "camera_make": self.camera_make,
            "camera_model": self.camera_model,
            "recorded_at": utils.format_datetime(self.recorded_at)
            if self.recorded_at else None,
            "modified_at": utils.format_datetime(self.fs_modified)
            if self.fs_modified else None,
            "path": self.path,
        }


def _parent_folder(path: str) -> str:
    import os

    return os.path.basename(os.path.dirname(os.path.abspath(path)))


# Column order + human headers for the master catalog. Keeping this next to
# the model keeps the spreadsheet and the model in lock-step.
MASTER_COLUMNS: List[tuple] = [
    ("file_name", "File Name"),
    ("media_kind", "Type"),
    ("folder", "Folder"),
    ("duration", "Duration"),
    ("file_size", "Size"),
    ("container", "Container"),
    ("video_codec", "Video Codec"),
    ("resolution", "Resolution"),
    ("frame_rate", "Frame Rate"),
    ("video_bitrate", "Video Bitrate"),
    ("audio_codec", "Audio Codec"),
    ("sample_rate", "Sample Rate"),
    ("channels", "Channels"),
    ("camera_make", "Camera Make"),
    ("camera_model", "Camera Model"),
    ("recorded_at", "Recorded"),
    ("modified_at", "Modified"),
    ("path", "Full Path"),
]
