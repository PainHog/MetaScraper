"""Small formatting and value-coercion helpers shared across MetaScraper."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from fractions import Fraction
from typing import Any, Optional

# Characters a .docx/.xlsx (XML) can't hold: control characters, and unpaired
# surrogates (undecodable bytes in a file name).
_XML_ILLEGAL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")


def xml_safe(value: Any) -> Any:
    """Replace characters Word and Excel files can't store with U+FFFD.

    Camera tags sometimes carry control characters (e.g. a stray ``\\x1f``);
    written as-is they make python-docx and openpyxl refuse the whole file.
    Non-strings pass through unchanged.
    """
    if not isinstance(value, str):
        return value
    return _XML_ILLEGAL.sub("\ufffd", value)


def human_file_size(num_bytes: Optional[int]) -> str:
    """Return a human-readable file size such as ``1.4 GB``."""
    if num_bytes is None:
        return "—"
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if abs(size) < 1024.0 or unit == "PB":
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} PB"


def human_duration(seconds: Optional[float]) -> str:
    """Return ``H:MM:SS`` (or ``M:SS``) for a duration in seconds."""
    if seconds is None:
        return "—"
    try:
        total = int(round(float(seconds)))
    except (TypeError, ValueError):
        return "—"
    if total < 0:
        return "—"
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def human_bitrate(bits_per_second: Optional[float]) -> str:
    """Return a bitrate such as ``8.42 Mb/s`` or ``320 kb/s``."""
    if bits_per_second is None:
        return "—"
    try:
        bps = float(bits_per_second)
    except (TypeError, ValueError):
        return "—"
    if bps <= 0:
        return "—"
    if bps >= 1_000_000:
        return f"{bps / 1_000_000:.2f} Mb/s"
    if bps >= 1_000:
        return f"{bps / 1_000:.0f} kb/s"
    return f"{bps:.0f} b/s"


def parse_frame_rate(value: Optional[str]) -> Optional[float]:
    """Convert an ffprobe frame-rate string like ``30000/1001`` to a float."""
    if not value or value in ("0/0", "0"):
        return None
    try:
        if "/" in str(value):
            frac = Fraction(str(value))
            if frac.denominator == 0:
                return None
            return float(frac)
        return float(value)
    except (ValueError, ZeroDivisionError):
        return None


def human_frame_rate(fps: Optional[float]) -> str:
    """Format a frame rate, trimming trailing zeros (``29.97`` / ``30``)."""
    if fps is None:
        return "—"
    rounded = round(fps, 3)
    if abs(rounded - round(rounded)) < 1e-6:
        return f"{int(round(rounded))} fps"
    return f"{rounded:g} fps"


def human_sample_rate(hz: Optional[Any]) -> str:
    """Format an audio sample rate such as ``48.0 kHz``."""
    if hz in (None, "", "0"):
        return "—"
    try:
        value = float(hz)
    except (TypeError, ValueError):
        return "—"
    if value <= 0:
        return "—"
    return f"{value / 1000:g} kHz"


def to_int(value: Any) -> Optional[int]:
    """Best-effort int coercion that tolerates ``None`` and blank strings."""
    if value in (None, "", "N/A"):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def to_float(value: Any) -> Optional[float]:
    """Best-effort float coercion that tolerates ``None`` and blank strings."""
    if value in (None, "", "N/A"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_timestamp(value: Any) -> Optional[datetime]:
    """Parse the many timestamp shapes found in media tags into a datetime.

    Handles ISO 8601 with ``Z``/offsets and a couple of common camera formats.
    Returns ``None`` when the value cannot be understood.
    """
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None

    # Normalise a trailing Z to an explicit UTC offset for fromisoformat.
    candidates = []
    iso = text.replace("Z", "+00:00")
    candidates.append(iso)
    candidates.append(text)

    for candidate in candidates:
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            pass

    formats = (
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S",
        "%Y:%m:%d %H:%M:%S%z",  # EXIF / QuickTime style
        "%Y:%m:%d %H:%M:%S",
        "%Y-%m-%d",
    )
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def format_datetime(value: Optional[datetime]) -> str:
    """Render a datetime as a friendly, timezone-aware string."""
    if value is None:
        return "—"
    if value.tzinfo is not None:
        local = value.astimezone()
        return local.strftime("%Y-%m-%d %H:%M:%S %Z").strip()
    return value.strftime("%Y-%m-%d %H:%M:%S")


def timestamp_from_epoch(epoch: float) -> datetime:
    """Convert a POSIX timestamp to an aware local datetime."""
    return datetime.fromtimestamp(epoch, tz=timezone.utc).astimezone()


def coalesce(*values: Any, default: str = "—") -> Any:
    """Return the first value that is not ``None`` or an empty string."""
    for value in values:
        if value not in (None, ""):
            return value
    return default
