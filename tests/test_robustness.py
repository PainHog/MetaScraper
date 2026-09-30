"""Regression tests for real-world metadata and platform quirks: control
characters in tags, tool output encoding and failures, numeric exiftool values,
cover art, audio-only containers, path case, odd file names, quoted tool paths
and Windows system folders."""

import os
import stat
import subprocess
import sys
import time
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from metascraper import catalog, discovery, organizer, probe, report, utils
from metascraper.catalog import MasterCatalog
from metascraper.models import MediaInfo

NOW = datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc)


def _info(path="/src/clip.mp4", **kwargs):
    return MediaInfo(path=os.path.abspath(path), name=os.path.basename(path),
                     ext=os.path.splitext(path)[1], media_kind="Video", **kwargs)


# -- control characters ----------------------------------------------------------

def test_xml_safe():
    assert utils.xml_safe("HERO\x1f9 bell\x07") == "HERO\ufffd9 bell\ufffd"
    assert utils.xml_safe("caf\udce9") == "caf\ufffd"
    assert utils.xml_safe("tabs\tand\nnewlines stay") == "tabs\tand\nnewlines stay"
    assert utils.xml_safe(42) == 42


def test_control_characters_do_not_break_the_catalog(tmp_path):
    store = MasterCatalog(str(tmp_path / "Master_Catalog.json")).load()
    store.upsert(_info(camera_model="HERO\x1f9", comment="bell\x07"), NOW)
    store.save_json(NOW)
    # Both renders used to raise on the stored control character, on every
    # later run too, since the bad record stays in the JSON.
    store.render_docx(str(tmp_path / "m.docx"), NOW)
    store.render_xlsx(str(tmp_path / "m.xlsx"), NOW)
    reloaded = MasterCatalog(str(tmp_path / "Master_Catalog.json")).load()
    reloaded.render_xlsx(str(tmp_path / "m2.xlsx"), NOW)


def test_control_characters_do_not_break_the_per_file_report(tmp_path):
    info = _info(comment="bell\x07here", title="t\x1fitle", camera_model="X\x00")
    info.notes.append("note with \x0b vertical tab")
    out = report.write_report(info, str(tmp_path / "clip.docx"), generated_at=NOW)
    assert os.path.getsize(out) > 0


# -- running the tools ----------------------------------------------------------

class _Spy:
    def __init__(self, stdout="{}", returncode=0, stderr=""):
        self.calls = []
        self.result = (stdout, returncode, stderr)

    def __call__(self, cmd, **kwargs):
        self.calls.append((cmd, kwargs))
        argfile = cmd[cmd.index("-@") + 1] if "-@" in cmd else None
        if argfile:
            with open(argfile, "rb") as handle:
                kwargs["argfile_bytes"] = handle.read()
        stdout, code, stderr = self.result
        return subprocess.CompletedProcess(cmd, code, stdout, stderr)


def test_tools_are_decoded_as_utf8_without_stdin(monkeypatch):
    spy = _Spy(stdout='{"format": {"tags": {"title": "Café Señor"}}}')
    monkeypatch.setattr(probe.subprocess, "run", spy)
    probe.run_ffprobe("x.mp4", "ffprobe")
    _cmd, kwargs = spy.calls[0]
    assert kwargs["encoding"] == "utf-8" and kwargs["errors"] == "replace"
    assert kwargs["stdin"] is subprocess.DEVNULL


def test_tools_run_without_a_console_window_on_windows(monkeypatch):
    spy = _Spy(stdout='[{"SourceFile": "x"}]')
    monkeypatch.setattr(probe.subprocess, "run", spy)
    monkeypatch.setattr(probe.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(probe, "_WINDOWS", True)
    probe.run_exiftool("C:/cards/clip.mp4", "exiftool")
    assert spy.calls[0][1]["creationflags"] == 0x08000000


def test_exiftool_gets_non_ascii_paths_through_a_utf8_argfile_on_windows(monkeypatch):
    spy = _Spy(stdout='[{"SourceFile": "x"}]')
    monkeypatch.setattr(probe.subprocess, "run", spy)
    monkeypatch.setattr(probe, "_WINDOWS", True)
    probe.run_exiftool("D:/Álbum/Ñame.mp4", "exiftool")
    cmd, kwargs = spy.calls[0]
    assert "D:/Álbum/Ñame.mp4" not in cmd
    assert cmd[cmd.index("-charset") + 1] == "filename=utf8"
    assert kwargs["argfile_bytes"] == "D:/Álbum/Ñame.mp4\n".encode("utf-8")
    assert not os.path.exists(cmd[cmd.index("-@") + 1])  # cleaned up


def test_ffprobe_failure_is_reported_not_swallowed(monkeypatch):
    # Real ffprobe prints "{}" and exits 1 for an unreadable file.
    spy = _Spy(stdout="{\n\n}\n", returncode=1,
               stderr="corrupt.mp4: Invalid data found when processing input")
    monkeypatch.setattr(probe.subprocess, "run", spy)
    with pytest.raises(RuntimeError, match="Invalid data"):
        probe.run_ffprobe("corrupt.mp4", "ffprobe")


# -- parsing ----------------------------------------------------------------------

def test_numeric_exiftool_values_become_text():
    info = probe.media_info_from_payloads(
        "/src/clip.mp4",
        exiftool_data={"QuickTime:Make": 6300, "QuickTime:Model": 6300,
                       "QuickTime:Software": 1.1},
    )
    assert info.camera_model == "6300" and isinstance(info.camera_model, str)
    assert organizer.camera_label(info) == "6300"


def _stream(kind, codec, **extra):
    base = {"index": 0, "codec_type": kind, "codec_name": codec}
    base.update(extra)
    return base


def test_cover_art_is_not_the_video_stream():
    info = probe.media_info_from_payloads("/src/song.mp3", ffprobe_data={"streams": [
        _stream("audio", "mp3", sample_rate="44100", channels=2),
        _stream("video", "png", width=500, height=500, r_frame_rate="90000/1",
                disposition={"default": 0, "attached_pic": 1}),
    ]})
    assert info.video_streams == [] and info.primary_video is None
    assert info.media_kind == "Audio"
    row = info.master_row()
    assert row["video_codec"] is None and row["resolution"] is None


def test_audio_only_mp4_is_audio_and_real_video_stays_video():
    memo = probe.media_info_from_payloads("/src/memo.mp4", ffprobe_data={
        "streams": [_stream("audio", "aac")]})
    assert memo.media_kind == "Audio"
    clip = probe.media_info_from_payloads("/src/clip.mp4", ffprobe_data={
        "streams": [_stream("video", "h264", width=1920, height=1080),
                    _stream("audio", "aac")]})
    assert clip.media_kind == "Video"
    # Without ffprobe data the extension still decides.
    assert probe.media_info_from_payloads("/src/x.mp4").media_kind == "Video"


# -- paths and names -------------------------------------------------------------

def test_catalog_keys_ignore_case_where_the_os_does(tmp_path, monkeypatch):
    # Emulate Windows' case-insensitive paths.
    monkeypatch.setattr(os.path, "normcase", lambda p: p.lower())
    store = MasterCatalog(str(tmp_path / "c.json"))
    store.records = MasterCatalog._rekey({
        "/Footage/CLIP01.MP4": {"cataloged_at": "2024-01-02", "first_cataloged_at": "2024-01-01"},
        "/footage/clip01.mp4": {"cataloged_at": "2024-02-01", "first_cataloged_at": "2024-02-01"},
    })
    assert len(store.records) == 1
    (record,) = store.records.values()
    assert record["cataloged_at"] == "2024-02-01"
    assert record["first_cataloged_at"] == "2024-01-01"

    assert store.upsert(_info("/FOOTAGE/Clip01.mp4"), NOW) is False
    assert len(store.records) == 1


def test_undecodable_file_names_survive_the_json_store(tmp_path):
    store = MasterCatalog(str(tmp_path / "c.json")).load()
    store.upsert(_info(f"{tmp_path}/caf\udce9.mp4"), NOW)
    store.save_json(NOW)
    assert not os.path.exists(str(tmp_path / "c.json.tmp"))
    reloaded = MasterCatalog(str(tmp_path / "c.json")).load()
    assert list(reloaded.records) == list(store.records)
    reloaded.render_docx(str(tmp_path / "c.docx"), NOW)


def test_quoted_tool_path_is_accepted(tmp_path, monkeypatch):
    monkeypatch.setattr(probe.shutil, "which", lambda *_a, **_k: None)
    tool = tmp_path / "ffprobe"
    tool.write_text("#!/bin/sh\n")
    tool.chmod(tool.stat().st_mode | stat.S_IEXEC)
    if not os.access(str(tool), os.X_OK):
        pytest.skip("can't mark files executable here")
    assert probe.ToolLocation(ffprobe=f'"{tool}"').ffprobe == str(tool)
    assert probe.ToolLocation(ffprobe=f'  {tool} ').ffprobe == str(tool)


def test_windows_system_folders_are_not_scanned(tmp_path):
    for rel in ("$RECYCLE.BIN/S-1-5-21/$R8K2J1Q.MP4",
                "System Volume Information/x.mp4", "DCIM/C0001.MP4"):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x00")
    found = discovery.find_media_files([str(tmp_path)])
    assert [os.path.basename(p) for p in found] == ["C0001.MP4"]


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="needs time.tzset")
def test_date_folder_uses_the_local_day(monkeypatch):
    monkeypatch.setenv("TZ", "America/Chicago")
    time.tzset()
    try:
        late = datetime(2024, 5, 12, 1, 30, tzinfo=timezone.utc)  # 20:30 on the 11th
        info = _info(recorded_at=late)
        assert organizer.date_label(info) == "2024-05-11"
        assert utils.format_datetime(late).startswith("2024-05-11")
    finally:
        monkeypatch.undo()
        time.tzset()
