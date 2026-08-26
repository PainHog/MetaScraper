"""MetaScraper — catalog metadata from camera video and audio recordings.

MetaScraper scans a folder of media files, extracts rich technical metadata
using ``ffprobe`` (and ``exiftool`` when available), writes a polished
per-file "Project Information" Word document for each recording, and keeps a
running master catalog as both a formatted ``.docx`` and an ``.xlsx``
spreadsheet.
"""

__version__ = "1.0.0"

__all__ = ["__version__"]
