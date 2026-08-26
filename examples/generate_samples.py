#!/usr/bin/env python3
"""Generate realistic sample output so you can see what MetaScraper produces.

This does NOT need ffmpeg installed — it feeds MetaScraper representative
metadata (an iPhone 4K clip and a 24-bit field-recorder WAV) so you can open
the resulting Word documents and spreadsheet and see the real formatting.

    python examples/generate_samples.py [output_dir]
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from metascraper.catalog import MasterCatalog
from metascraper.probe import media_info_from_payloads
from metascraper.report import write_report

# --- Representative payloads (the shape ffprobe/exiftool really return) ------

IPHONE_MOV = {
    "streams": [
        {
            "index": 0, "codec_name": "hevc",
            "codec_long_name": "H.265 / HEVC (High Efficiency Video Coding)",
            "profile": "Main 10", "codec_type": "video",
            "width": 3840, "height": 2160, "display_aspect_ratio": "16:9",
            "pix_fmt": "yuv420p10le", "color_space": "bt2020nc",
            "color_primaries": "bt2020", "color_transfer": "arib-std-b67",
            "field_order": "progressive",
            "avg_frame_rate": "30000/1001", "r_frame_rate": "30000/1001",
            "bit_rate": "48123456", "duration": "42.712000",
            "side_data_list": [{"rotation": -90}],
            "tags": {"creation_time": "2024-05-11T18:32:07.000000Z",
                     "handler_name": "Core Media Video", "language": "und"},
        },
        {
            "index": 1, "codec_name": "aac",
            "codec_long_name": "AAC (Advanced Audio Coding)", "profile": "LC",
            "codec_type": "audio", "sample_rate": "44100", "channels": 2,
            "channel_layout": "stereo", "bit_rate": "160000",
            "duration": "42.712000",
            "tags": {"handler_name": "Core Media Audio", "language": "und"},
        },
    ],
    "format": {
        "filename": "IMG_4021.MOV", "nb_streams": 2,
        "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
        "format_long_name": "QuickTime / MOV",
        "duration": "42.712000", "size": "257114880", "bit_rate": "48150000",
        "tags": {
            "major_brand": "qt  ", "minor_version": "0",
            "creation_time": "2024-05-11T18:32:07.000000Z",
            "com.apple.quicktime.make": "Apple",
            "com.apple.quicktime.model": "iPhone 15 Pro",
            "com.apple.quicktime.software": "17.4.1",
            "com.apple.quicktime.creationdate": "2024-05-11T13:32:07-0500",
            "com.apple.quicktime.location.ISO6709": "+34.0522-118.2437+015.000/",
        },
    },
    "chapters": [],
}

IPHONE_EXIF = {
    "SourceFile": "IMG_4021.MOV", "EXIF:Make": "Apple",
    "EXIF:Model": "iPhone 15 Pro", "EXIF:Software": "17.4.1",
    "EXIF:LensModel": "iPhone 15 Pro back triple camera 6.765mm f/1.78",
    "QuickTime:CreateDate": "2024:05:11 18:32:07",
    "Composite:GPSLatitude": 34.0522, "Composite:GPSLongitude": -118.2437,
    "Composite:GPSPosition": "34.0522 -118.2437",
}

FIELD_WAV = {
    "streams": [
        {
            "index": 0, "codec_name": "pcm_s24le",
            "codec_long_name": "PCM signed 24-bit little-endian",
            "codec_type": "audio", "sample_rate": "48000", "channels": 2,
            "channel_layout": "stereo", "bits_per_raw_sample": "24",
            "bits_per_sample": 24, "bit_rate": "2304000", "duration": "184.320000",
            "tags": {},
        }
    ],
    "format": {
        "filename": "ZOOM0007_Tr1.WAV", "nb_streams": 1, "format_name": "wav",
        "format_long_name": "WAV / WAVE (Waveform Audio)",
        "duration": "184.320000", "size": "53084160", "bit_rate": "2304000",
        "tags": {"encoder": "ZOOM Handy Recorder H6", "date": "2024-05-11"},
    },
    "chapters": [],
}

DSLR_MP4 = {
    "streams": [
        {
            "index": 0, "codec_name": "h264",
            "codec_long_name": "H.264 / AVC", "profile": "High", "codec_type": "video",
            "width": 1920, "height": 1080, "display_aspect_ratio": "16:9",
            "pix_fmt": "yuv420p", "avg_frame_rate": "24000/1001",
            "r_frame_rate": "24000/1001", "bit_rate": "92000000",
            "duration": "128.400000", "color_space": "bt709",
            "field_order": "progressive",
            "tags": {"creation_time": "2024-03-02T09:14:55.000000Z",
                     "handler_name": "VideoHandler"},
        },
        {
            "index": 1, "codec_name": "pcm_s16le",
            "codec_long_name": "PCM signed 16-bit little-endian",
            "codec_type": "audio", "sample_rate": "48000", "channels": 2,
            "channel_layout": "stereo", "bit_rate": "1536000",
            "duration": "128.400000", "tags": {"handler_name": "SoundHandler"},
        },
    ],
    "format": {
        "filename": "C0042.MP4", "nb_streams": 2, "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
        "format_long_name": "QuickTime / MOV", "duration": "128.400000",
        "size": "1476000000", "bit_rate": "93536000",
        "tags": {"major_brand": "mp42", "creation_time": "2024-03-02T09:14:55.000000Z",
                 "make": "Sony", "model": "ILCE-7M4",
                 "com.sony.mediaprofile": "XAVC S"},
    },
    "chapters": [],
}

SAMPLES = [
    ("/Projects/Wildlife_Doc/Footage/IMG_4021.MOV", IPHONE_MOV, IPHONE_EXIF),
    ("/Projects/Wildlife_Doc/Footage/C0042.MP4", DSLR_MP4, {}),
    ("/Projects/Wildlife_Doc/Audio/ZOOM0007_Tr1.WAV", FIELD_WAV, {}),
]


def main() -> None:
    out_dir = sys.argv[1] if len(sys.argv) > 1 else "sample_output"
    docs_dir = os.path.join(out_dir, "Documents")
    now = datetime.now().astimezone()

    catalog = MasterCatalog(os.path.join(out_dir, "Master_Catalog.json")).load()
    for path, ffprobe_data, exif in SAMPLES:
        info = media_info_from_payloads(
            path, ffprobe_data=ffprobe_data, exiftool_data=exif,
        )
        # These sample paths don't exist on disk; give them plausible stats.
        info.size_bytes = int(ffprobe_data["format"]["size"])
        doc_path = os.path.join(docs_dir, os.path.splitext(info.name)[0] + ".docx")
        write_report(info, doc_path, generated_at=now)
        catalog.upsert(info, now)
        print("wrote", doc_path)

    catalog.save_json(now)
    catalog.render_docx(os.path.join(out_dir, "Master_Catalog.docx"), now)
    catalog.render_xlsx(os.path.join(out_dir, "Master_Catalog.xlsx"), now)
    print("wrote master catalog (.docx, .xlsx, .json) in", out_dir)


if __name__ == "__main__":
    main()
