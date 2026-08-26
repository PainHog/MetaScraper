"""Tests for media-file discovery."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from metascraper.discovery import find_media_files


def _touch(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(b"\x00")


def test_finds_video_and_audio_recurses(tmp_path):
    _touch(str(tmp_path / "a.mp4"))
    _touch(str(tmp_path / "sub" / "b.wav"))
    _touch(str(tmp_path / "sub" / "notes.txt"))
    _touch(str(tmp_path / "c.MOV"))  # case-insensitive extension

    found = find_media_files([str(tmp_path)])
    names = sorted(os.path.basename(f) for f in found)
    assert names == ["a.mp4", "b.wav", "c.MOV"]


def test_no_recursive(tmp_path):
    _touch(str(tmp_path / "a.mp4"))
    _touch(str(tmp_path / "sub" / "b.wav"))
    found = find_media_files([str(tmp_path)], recursive=False)
    names = sorted(os.path.basename(f) for f in found)
    assert names == ["a.mp4"]


def test_skip_dirs_excludes_output(tmp_path):
    _touch(str(tmp_path / "a.mp4"))
    out = tmp_path / "MetaScraper_Catalog"
    _touch(str(out / "leftover.mp4"))
    found = find_media_files([str(tmp_path)], skip_dirs=[str(out)])
    names = [os.path.basename(f) for f in found]
    assert names == ["a.mp4"]


def test_extra_extensions(tmp_path):
    # .ari (ARRIRAW) is not a default media extension.
    _touch(str(tmp_path / "shot.ari"))
    assert find_media_files([str(tmp_path)]) == []
    found = find_media_files([str(tmp_path)], extra_extensions=["ari"])
    assert [os.path.basename(f) for f in found] == ["shot.ari"]


def test_hidden_files_skipped(tmp_path):
    _touch(str(tmp_path / ".hidden.mp4"))
    _touch(str(tmp_path / "visible.mp4"))
    found = [os.path.basename(f) for f in find_media_files([str(tmp_path)])]
    assert found == ["visible.mp4"]
