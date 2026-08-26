"""Realistic ffprobe / exiftool payloads used across the test-suite.

These mirror the real JSON shapes emitted by the tools so the parsing code is
exercised the same way it will be in production.
"""

# An iPhone-style .mov: H.264 video + AAC audio, with QuickTime metadata tags.
IPHONE_MOV_FFPROBE = {
    "streams": [
        {
            "index": 0,
            "codec_name": "h264",
            "codec_long_name": "H.264 / AVC / MPEG-4 AVC / MPEG-4 part 10",
            "profile": "High",
            "codec_type": "video",
            "width": 3840,
            "height": 2160,
            "coded_width": 3840,
            "coded_height": 2160,
            "display_aspect_ratio": "16:9",
            "pix_fmt": "yuv420p",
            "level": 51,
            "color_space": "bt2020nc",
            "color_transfer": "arib-std-b67",
            "color_primaries": "bt2020",
            "field_order": "progressive",
            "r_frame_rate": "30000/1001",
            "avg_frame_rate": "30000/1001",
            "bit_rate": "48123456",
            "duration": "12.412000",
            "side_data_list": [{"side_data_type": "Display Matrix", "rotation": -90}],
            "tags": {
                "creation_time": "2024-05-11T18:32:07.000000Z",
                "language": "und",
                "handler_name": "Core Media Video",
            },
        },
        {
            "index": 1,
            "codec_name": "aac",
            "codec_long_name": "AAC (Advanced Audio Coding)",
            "profile": "LC",
            "codec_type": "audio",
            "sample_rate": "44100",
            "channels": 2,
            "channel_layout": "stereo",
            "bits_per_sample": 0,
            "bit_rate": "159printed",  # intentionally messy -> should coerce to None
            "duration": "12.412000",
            "tags": {
                "creation_time": "2024-05-11T18:32:07.000000Z",
                "language": "und",
                "handler_name": "Core Media Audio",
            },
        },
    ],
    "format": {
        "filename": "IMG_4021.MOV",
        "nb_streams": 2,
        "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
        "format_long_name": "QuickTime / MOV",
        "start_time": "0.000000",
        "duration": "12.412000",
        "size": "74691234",
        "bit_rate": "48150000",
        "tags": {
            "major_brand": "qt  ",
            "minor_version": "0",
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

# A production WAV file: 24-bit / 48 kHz stereo PCM.
WAV_FFPROBE = {
    "streams": [
        {
            "index": 0,
            "codec_name": "pcm_s24le",
            "codec_long_name": "PCM signed 24-bit little-endian",
            "codec_type": "audio",
            "sample_rate": "48000",
            "channels": 2,
            "channel_layout": "stereo",
            "bits_per_raw_sample": "24",
            "bits_per_sample": 24,
            "bit_rate": "2304000",
            "duration": "3.500000",
            "tags": {},
        }
    ],
    "format": {
        "filename": "ZOOM0001.WAV",
        "nb_streams": 1,
        "format_name": "wav",
        "format_long_name": "WAV / WAVE (Waveform Audio)",
        "duration": "3.500000",
        "size": "1008000",
        "bit_rate": "2304000",
        "tags": {"encoder": "Lavf58.29.100"},
    },
    "chapters": [],
}

# exiftool payload (with -G group prefixes, -n numeric) for the iPhone clip.
IPHONE_EXIFTOOL = {
    "SourceFile": "IMG_4021.MOV",
    "EXIF:Make": "Apple",
    "EXIF:Model": "iPhone 15 Pro",
    "EXIF:Software": "17.4.1",
    "EXIF:LensModel": "iPhone 15 Pro back triple camera 6.765mm f/1.78",
    "QuickTime:CreateDate": "2024:05:11 18:32:07",
    "Composite:GPSLatitude": 34.0522,
    "Composite:GPSLongitude": -118.2437,
    "Composite:GPSPosition": "34.0522 -118.2437",
}
