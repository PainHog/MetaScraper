# MetaScraper

**Catalog the metadata of your camera video and audio recordings.**

Point MetaScraper at a folder of recordings and it will:

1. 📄 Write a polished, professional **Word document (`.docx`) for every file** —
   a "Project Information" sheet capturing all of that file's metadata.
2. 📚 Keep a running **master catalog** of everything, as both a formatted
   **Word document** and a sortable **Excel spreadsheet (`.xlsx`)**.

It reads rich technical details — codec, resolution, frame rate, bitrate,
duration, audio sample rate/channels/bit depth, camera make & model, lens,
recording date, GPS location, and every raw tag — using **FFmpeg's `ffprobe`**
(and **ExifTool** when available). Nothing is thrown away: each per-file
document ends with a complete dump of every field the tools reported, so your
catalog is a true archive.

Re-running is safe: the master catalog is keyed by file path, so scanning the
same folder again **updates** existing entries instead of creating duplicates.

---

## Quick start

```bash
# 1. Install MetaScraper (from the project folder)
pip install .

# 2. Catalog a folder of recordings
metascraper "/path/to/your/recordings"
```

That produces a `MetaScraper_Catalog/` folder next to your recordings:

```
MetaScraper_Catalog/
├── Documents/
│   ├── IMG_4021.docx          ← one per recording
│   ├── C0042.docx
│   └── ZOOM0007_Tr1.docx
├── Master_Catalog.docx        ← formatted master list
├── Master_Catalog.xlsx        ← same data as a spreadsheet
└── Master_Catalog.json        ← the catalog "database" (source of truth)
```

Want to see what the output looks like without touching your own files?

```bash
python examples/generate_samples.py sample_output
```

---

## Requirements

- **Python 3.8+**
- **FFmpeg** (provides `ffprobe`) — required for full technical metadata.
- **ExifTool** *(optional)* — adds richer camera make/model, lens and GPS.

MetaScraper still runs without these tools, but it can then only record
filesystem details (name, size, dates) and will tell you so in each document.

### Installing FFmpeg

| Platform | Command |
| --- | --- |
| **Windows** | `winget install Gyan.FFmpeg` — or download from [ffmpeg.org](https://ffmpeg.org/download.html) |
| **macOS** | `brew install ffmpeg` |
| **Linux (Debian/Ubuntu)** | `sudo apt install ffmpeg` |

### Installing ExifTool (optional)

| Platform | Command |
| --- | --- |
| **Windows** | `winget install OliverBetz.ExifTool` |
| **macOS** | `brew install exiftool` |
| **Linux** | `sudo apt install libimage-exiftool-perl` |

If a tool isn't on your `PATH`, point MetaScraper at it directly with
`--ffprobe` / `--exiftool`.

---

## Usage

```
metascraper [FOLDERS...] [options]
```

| Option | Description |
| --- | --- |
| `FOLDERS...` | One or more folders (or files) to scan. Defaults to the current folder. |
| `-o, --output-dir DIR` | Where to write documents and the master catalog. Defaults to a `MetaScraper_Catalog` folder beside the input. |
| `--no-recursive` | Do not descend into subfolders. |
| `--include EXT,EXT` | Extra file extensions to treat as media (e.g. `--include braw,ari`). |
| `--all-files` | Catalog every file regardless of extension. |
| `--no-per-file` | Skip the per-file Word documents; only update the master catalog. |
| `--no-master` | Skip the master catalog; only write per-file documents. |
| `--master-name NAME` | Base name for the master files (default: `Master_Catalog`). |
| `--ffprobe PATH` | Path to the `ffprobe` binary. |
| `--exiftool PATH` | Path to the `exiftool` binary. |
| `-q, --quiet` | Only print warnings and the final summary. |

### Examples

```bash
# Catalog a single project folder (recursive by default)
metascraper "~/Shoots/2024-05 Wildlife Doc"

# Catalog several folders into one shared catalog
metascraper ./Footage ./B-Roll ./Audio -o "~/Catalogs/WildlifeDoc"

# Only the top level, and include RAW cinema formats
metascraper ./Footage --no-recursive --include braw,ari,r3d

# Point at tools that aren't on PATH (e.g. on Windows)
metascraper ./Footage --ffprobe "C:\ffmpeg\bin\ffprobe.exe"
```

You can run MetaScraper as a module too: `python -m metascraper ./Footage`.

---

## What gets captured

**Per-file document sections**

- *At a Glance* — type, duration, size, container, resolution, frame rate, audio
- *File Details* — name, folder, full path, size, created / modified dates
- *Container / Format* — format, duration, overall bitrate, title/artist/comment
- *Video Stream(s)* — codec, profile, resolution, aspect ratio, frame rate,
  bitrate, pixel format, color space, scan type, rotation
- *Audio Stream(s)* — codec, sample rate, channels, bitrate, bit depth, language
- *Camera / Device & Provenance* — make, model, lens, software, recorded date, GPS
- *Complete Technical Metadata* — every raw field, preserved verbatim

**Master catalog columns**

File Name · Type · Folder · Duration · Size · Container · Video Codec ·
Resolution · Frame Rate · Video Bitrate · Audio Codec · Sample Rate · Channels ·
Camera Make · Camera Model · Recorded · Modified · Full Path

The spreadsheet also has a **Summary** tab (total files, duration, size, counts
by type).

---

## Supported formats

**Video:** mp4, mov, m4v, avi, mkv, mts, m2ts, ts, wmv, flv, webm, mpg, mpeg,
3gp, mxf, insv, braw, r3d, and more.

**Audio:** wav, mp3, flac, aac, m4a, aiff, ogg, opus, wma, alac, ac3, dts, caf,
bwf, and more.

Add anything else with `--include`.

---

## How it stays in sync

`Master_Catalog.json` is the single source of truth. Every run loads it, upserts
the files it just scanned (keyed by absolute path), then re-renders the `.xlsx`
and `.docx` from it — so the three views never drift apart. Delete the `.json`
to start a fresh catalog.

---

## Development

```bash
pip install -e ".[dev]"
pytest
```

The test-suite covers metadata parsing (against realistic `ffprobe`/`exiftool`
payloads), the master catalog's upsert logic, document rendering, file
discovery, and full end-to-end CLI runs — so it passes without FFmpeg installed.

## License

MIT
