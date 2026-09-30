"""Extract media metadata using ffprobe (and exiftool when available).

The heavy lifting is split into small pure functions (``media_info_from_payloads``
and friends) so the parsing logic can be unit-tested without the binaries
present. :func:`probe_file` is the convenience entry point that actually runs
the tools.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from typing import Any, Dict, List, Optional

from . import utils
from .models import MediaInfo, StreamSummary, classify_extension

_WINDOWS = os.name == "nt"


class ToolLocation:
    """Resolves and caches the location of ffprobe / exiftool binaries."""

    def __init__(
        self,
        ffprobe: Optional[str] = None,
        exiftool: Optional[str] = None,
    ) -> None:
        # An explicit path (from the CLI flag or env var) is validated too, so a
        # typo falls back to a PATH search instead of failing on every file.
        self.ffprobe = _resolve_binary(
            explicit=ffprobe or os.environ.get("METASCRAPER_FFPROBE"),
            names=("ffprobe",),
        )
        self.exiftool = _resolve_binary(
            explicit=exiftool or os.environ.get("METASCRAPER_EXIFTOOL"),
            names=("exiftool",),
        )

    @property
    def has_ffprobe(self) -> bool:
        return self.ffprobe is not None

    @property
    def has_exiftool(self) -> bool:
        return self.exiftool is not None


def _resolve_binary(explicit: Optional[str], names: tuple) -> Optional[str]:
    # Explorer's "Copy as path" wraps the path in quotes.
    explicit = (explicit or "").strip().strip('"').strip("'").strip()
    if explicit:
        if os.path.isfile(explicit) and os.access(explicit, os.X_OK):
            return explicit
        found = shutil.which(explicit)
        if found:
            return found
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


# ---------------------------------------------------------------------------
# Running the external tools
# ---------------------------------------------------------------------------

def _run_tool(cmd: List[str], timeout: int) -> subprocess.CompletedProcess:
    """Run a metadata tool and capture its output.

    Both tools print UTF-8; decoding explicitly avoids Windows' ANSI code page
    garbling non-English names. CREATE_NO_WINDOW stops a console window from
    flashing up for every file when the windowed app runs them.
    """
    kwargs: Dict[str, Any] = {}
    if _WINDOWS:
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(
        cmd,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        **kwargs,
    )


def run_ffprobe(path: str, ffprobe: str, timeout: int = 120) -> Dict[str, Any]:
    """Run ffprobe and return the parsed JSON payload (may raise)."""
    cmd = [
        ffprobe,
        "-hide_banner",
        "-loglevel", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        "-show_chapters",
        path,
    ]
    result = _run_tool(cmd, timeout)
    if not result.stdout.strip():
        message = result.stderr.strip() or f"ffprobe exited with {result.returncode}"
        raise RuntimeError(message)
    data = json.loads(result.stdout)
    # An unreadable file still prints "{}" — report why instead of silently
    # cataloging it with no technical details.
    if result.returncode != 0 and not (data.get("format") or data.get("streams")):
        raise RuntimeError(result.stderr.strip()
                           or f"ffprobe exited with {result.returncode}")
    return data


def run_exiftool(path: str, exiftool: str, timeout: int = 120) -> Dict[str, Any]:
    """Run exiftool and return the parsed JSON payload (may raise)."""
    cmd = [exiftool, "-json", "-G", "-n", "-api", "largefilesupport=1"]
    argfile = None
    if _WINDOWS and not path.isascii():
        # exiftool (Perl) reads its command line in the ANSI code page, which
        # can't hold every name; pass the path in a UTF-8 argfile instead.
        fd, argfile = tempfile.mkstemp(suffix=".args")
        with os.fdopen(fd, "wb") as handle:
            handle.write(path.encode("utf-8") + b"\n")
        cmd += ["-charset", "filename=utf8", "-@", argfile]
    else:
        cmd.append(path)
    try:
        result = _run_tool(cmd, timeout)
    finally:
        if argfile:
            try:
                os.remove(argfile)
            except OSError:
                pass
    if not result.stdout.strip():
        message = result.stderr.strip() or f"exiftool exited with {result.returncode}"
        raise RuntimeError(message)
    data = json.loads(result.stdout)
    if isinstance(data, list):
        return data[0] if data else {}
    return data


# ---------------------------------------------------------------------------
# Pure parsing (unit-testable without the binaries)
# ---------------------------------------------------------------------------

def _pick_frame_rate(raw: Dict[str, Any]) -> Optional[float]:
    """Prefer avg_frame_rate, but fall back to r_frame_rate.

    ffprobe reports ``avg_frame_rate`` as the truthy string ``"0/0"`` for many
    variable-frame-rate / MKV / TS streams, so a plain ``a or b`` would keep the
    useless value; parse each in turn and take the first that resolves.
    """
    for key in ("avg_frame_rate", "r_frame_rate"):
        rate = utils.parse_frame_rate(raw.get(key))
        if rate is not None:
            return rate
    return None


def _stream_from_ffprobe(raw: Dict[str, Any]) -> StreamSummary:
    tags = raw.get("tags", {}) or {}
    rotation = None
    # Rotation can live in tags or in a side-data list.
    if "rotate" in tags:
        rotation = utils.to_int(tags.get("rotate"))
    for side in raw.get("side_data_list", []) or []:
        if "rotation" in side:
            rotation = utils.to_int(side.get("rotation"))

    bit_rate = utils.to_float(raw.get("bit_rate"))
    if bit_rate is None:
        # Some containers expose bitrate only as a tag (e.g. BPS / BPS-eng).
        for key in ("BPS", "BPS-eng", "variant_bitrate"):
            if key in tags:
                bit_rate = utils.to_float(tags.get(key))
                if bit_rate is not None:
                    break

    return StreamSummary(
        index=utils.to_int(raw.get("index")),
        codec_type=raw.get("codec_type", "unknown"),
        codec_name=raw.get("codec_name"),
        codec_long_name=raw.get("codec_long_name"),
        profile=raw.get("profile"),
        width=utils.to_int(raw.get("width")),
        height=utils.to_int(raw.get("height")),
        display_aspect_ratio=raw.get("display_aspect_ratio"),
        pixel_format=raw.get("pix_fmt"),
        frame_rate=_pick_frame_rate(raw),
        color_space=raw.get("color_space"),
        color_transfer=raw.get("color_transfer"),
        color_primaries=raw.get("color_primaries"),
        field_order=raw.get("field_order"),
        rotation=rotation,
        sample_rate=utils.to_int(raw.get("sample_rate")),
        channels=utils.to_int(raw.get("channels")),
        channel_layout=raw.get("channel_layout"),
        bits_per_sample=(
            utils.to_int(raw.get("bits_per_raw_sample"))
            or utils.to_int(raw.get("bits_per_sample")) or None
        ),
        bit_rate=bit_rate,
        duration_seconds=utils.to_float(raw.get("duration")),
        language=tags.get("language"),
        handler_name=tags.get("handler_name"),
        tags=tags,
        raw=raw,
    )


# Tag keys that commonly carry camera make / model across QuickTime, MP4,
# Android and generic containers.
_MAKE_KEYS = (
    "com.apple.quicktime.make", "make", "Make",
    "com.android.manufacturer", "manufacturer",
)
_MODEL_KEYS = (
    "com.apple.quicktime.model", "model", "Model",
    "com.android.model", "device_model",
)
_SOFTWARE_KEYS = (
    "com.apple.quicktime.software", "software", "Software",
    "encoder", "encoded_by",
)
_DATE_KEYS = (
    "creation_time", "com.apple.quicktime.creationdate",
    "date", "date_recorded", "originaldate",
)


def _text(value: Any) -> Optional[str]:
    return None if value in (None, "") else str(value)


def _first_tag(tags: Dict[str, Any], keys) -> Optional[str]:
    lowered = {str(k).lower(): v for k, v in tags.items()}
    for key in keys:
        if key in tags and tags[key] not in (None, ""):
            return str(tags[key])
        lk = key.lower()
        if lk in lowered and lowered[lk] not in (None, ""):
            return str(lowered[lk])
    return None


def _extract_gps(format_tags: Dict[str, Any], exif: Dict[str, Any]) -> Optional[str]:
    # QuickTime ISO-6709 location string, e.g. "+34.0522-118.2437/".
    iso = _first_tag(format_tags, (
        "com.apple.quicktime.location.ISO6709", "location", "location-eng",
    ))
    if iso:
        return str(iso).rstrip("/")

    lat = exif.get("Composite:GPSLatitude", exif.get("EXIF:GPSLatitude"))
    lon = exif.get("Composite:GPSLongitude", exif.get("EXIF:GPSLongitude"))
    if lat is not None and lon is not None:
        return f"{lat}, {lon}"
    position = exif.get("Composite:GPSPosition")
    if position:
        return str(position)
    return None


def media_info_from_payloads(
    path: str,
    *,
    ffprobe_data: Optional[Dict[str, Any]] = None,
    exiftool_data: Optional[Dict[str, Any]] = None,
    stat_result: Optional[os.stat_result] = None,
    notes: Optional[List[str]] = None,
) -> MediaInfo:
    """Build a :class:`MediaInfo` from already-collected tool payloads.

    This function performs no I/O beyond an optional ``os.stat`` fallback, so it
    is the seam used by the test-suite.
    """
    ffprobe_data = ffprobe_data or {}
    exiftool_data = exiftool_data or {}
    notes = list(notes or [])

    name = os.path.basename(path)
    _, ext = os.path.splitext(name)

    info = MediaInfo(
        path=os.path.abspath(path),
        name=name,
        ext=ext,
        media_kind=classify_extension(ext),
        ffprobe_raw=ffprobe_data,
        exiftool_raw=exiftool_data,
        notes=notes,
    )

    # Filesystem stat.
    if stat_result is None and os.path.exists(path):
        try:
            stat_result = os.stat(path)
        except OSError:
            stat_result = None
    if stat_result is not None:
        info.size_bytes = stat_result.st_size
        info.fs_modified = utils.timestamp_from_epoch(stat_result.st_mtime)
        # birth time where available, else ctime.
        created = getattr(stat_result, "st_birthtime", None) or stat_result.st_ctime
        info.fs_created = utils.timestamp_from_epoch(created)

    # Container / format block.
    fmt = ffprobe_data.get("format", {}) or {}
    format_tags = fmt.get("tags", {}) or {}
    info.format_tags = format_tags
    info.container_format = fmt.get("format_name")
    info.container_long_name = fmt.get("format_long_name")
    info.duration_seconds = utils.to_float(fmt.get("duration"))
    info.overall_bit_rate = utils.to_float(fmt.get("bit_rate"))
    if info.size_bytes is None:
        info.size_bytes = utils.to_int(fmt.get("size"))

    # Streams.
    info.streams = [
        _stream_from_ffprobe(s) for s in ffprobe_data.get("streams", []) or []
    ]

    # Trust the streams over the extension: an audio-only .mp4 (a voice memo)
    # is audio. Only files with a known media extension are reclassified.
    if info.streams and info.media_kind in ("Video", "Audio"):
        if info.video_streams:
            info.media_kind = "Video"
        elif info.audio_streams:
            info.media_kind = "Audio"

    # If ffprobe gave no duration, fall back to the longest stream duration.
    if info.duration_seconds is None and info.streams:
        durations = [s.duration_seconds for s in info.streams if s.duration_seconds]
        if durations:
            info.duration_seconds = max(durations)

    # Human-facing container tags.
    info.title = _first_tag(format_tags, ("title", "com.apple.quicktime.title"))
    info.artist = _first_tag(
        format_tags, ("artist", "author", "com.apple.quicktime.artist")
    )
    info.comment = _first_tag(
        format_tags, ("comment", "description", "com.apple.quicktime.description")
    )

    # Device / provenance — prefer exiftool, fall back to container tags.
    # exiftool's JSON leaves number-like values unquoted (a model "6300"
    # arrives as an int), so coerce these text fields back to strings.
    info.camera_make = _text(
        exiftool_data.get("EXIF:Make")
        or exiftool_data.get("QuickTime:Make")
        or exiftool_data.get("Make")
        or _first_tag(format_tags, _MAKE_KEYS)
    )
    info.camera_model = _text(
        exiftool_data.get("EXIF:Model")
        or exiftool_data.get("QuickTime:Model")
        or exiftool_data.get("Model")
        or _first_tag(format_tags, _MODEL_KEYS)
    )
    info.lens_model = _text(
        exiftool_data.get("EXIF:LensModel")
        or exiftool_data.get("Composite:LensID")
        or exiftool_data.get("XMP:Lens")
    )
    info.software = _text(
        exiftool_data.get("EXIF:Software")
        or exiftool_data.get("QuickTime:Software")
        or _first_tag(format_tags, _SOFTWARE_KEYS)
    )
    info.gps_coordinates = _extract_gps(format_tags, exiftool_data)

    recorded_raw = (
        _first_tag(format_tags, _DATE_KEYS)
        or exiftool_data.get("QuickTime:CreateDate")
        or exiftool_data.get("EXIF:DateTimeOriginal")
        or exiftool_data.get("EXIF:CreateDate")
        or exiftool_data.get("QuickTime:MediaCreateDate")
    )
    info.recorded_at = utils.parse_timestamp(recorded_raw)

    return info


# ---------------------------------------------------------------------------
# Convenience entry point that runs the tools
# ---------------------------------------------------------------------------

def probe_file(path: str, tools: Optional[ToolLocation] = None) -> MediaInfo:
    """Probe a single file, running whatever tools are available."""
    tools = tools or ToolLocation()
    notes: List[str] = []
    ffprobe_data: Dict[str, Any] = {}
    exiftool_data: Dict[str, Any] = {}

    if tools.has_ffprobe:
        try:
            ffprobe_data = run_ffprobe(path, tools.ffprobe)
        except subprocess.TimeoutExpired:
            notes.append("ffprobe timed out; only filesystem metadata was captured.")
        except Exception as exc:  # noqa: BLE001 - surfaced to the report
            notes.append(f"ffprobe could not read this file: {exc}")
    else:
        notes.append(
            "ffprobe was not found on this system — install FFmpeg for full "
            "technical metadata. Only filesystem details were captured."
        )

    if tools.has_exiftool:
        try:
            exiftool_data = run_exiftool(path, tools.exiftool)
        except subprocess.TimeoutExpired:
            notes.append("exiftool timed out; camera details may be incomplete.")
        except Exception as exc:  # noqa: BLE001
            notes.append(f"exiftool could not read this file: {exc}")

    return media_info_from_payloads(
        path,
        ffprobe_data=ffprobe_data,
        exiftool_data=exiftool_data,
        notes=notes,
    )
