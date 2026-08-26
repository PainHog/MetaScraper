"""End-to-end CLI tests for the organize and finalize subcommands."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from metascraper import cli, organizer, probe


def _write(path, data=b"\x00" * 256):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data)


def _find(root, name):
    for base, _dirs, files in os.walk(root):
        if name in files:
            return os.path.join(base, name)
    return None


def test_organize_copies_into_tree_and_keeps_originals(tmp_path, monkeypatch):
    monkeypatch.setattr(probe.shutil, "which", lambda *_a, **_k: None)
    src = tmp_path / "in"
    _write(str(src / "clip.mp4"))
    _write(str(src / "take.wav"))
    dest = tmp_path / "lib"

    code = cli.run(["organize", str(src), "-o", str(dest), "-q"])
    assert code == 0

    # Files landed under Video/ and Audio/ (camera/date layers in between).
    vid = _find(str(dest / "Video"), "clip.mp4")
    aud = _find(str(dest / "Audio"), "take.wav")
    assert vid and aud
    assert os.sep + "Video" + os.sep in vid
    assert os.sep + "Audio" + os.sep in aud
    # Originals untouched, manifest written.
    assert (src / "clip.mp4").exists() and (src / "take.wav").exists()
    assert (dest / organizer.MANIFEST_NAME).exists()


def test_organize_dry_run_copies_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(probe.shutil, "which", lambda *_a, **_k: None)
    src = tmp_path / "in"
    _write(str(src / "clip.mp4"))
    dest = tmp_path / "lib"

    code = cli.run(["organize", str(src), "-o", str(dest), "--dry-run"])
    assert code == 0
    assert not dest.exists() or not any(dest.rglob("clip.mp4"))
    assert "dry run" in capsys.readouterr().out.lower()


def test_finalize_preview_then_delete(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(probe.shutil, "which", lambda *_a, **_k: None)
    src = tmp_path / "in"
    _write(str(src / "clip.mp4"))
    dest = tmp_path / "lib"
    cli.run(["organize", str(src), "-o", str(dest), "-q"])

    # Preview (no --yes) must not delete.
    code = cli.run(["finalize", str(dest)])
    assert code == 0
    assert (src / "clip.mp4").exists()
    assert "preview" in capsys.readouterr().out.lower()

    # With --yes the verified original is removed; the copy remains.
    code = cli.run(["finalize", str(dest), "--yes", "--checksum"])
    assert code == 0
    assert not (src / "clip.mp4").exists()
    assert _find(str(dest / "Video"), "clip.mp4")


def test_finalize_without_manifest_errors(tmp_path):
    empty = tmp_path / "nowhere"
    empty.mkdir()
    code = cli.run(["finalize", str(empty)])
    assert code == 1


def test_bare_folder_still_runs_catalog(tmp_path, monkeypatch):
    # Backward compatibility: no subcommand => catalog.
    monkeypatch.setattr(probe.shutil, "which", lambda *_a, **_k: None)
    src = tmp_path / "in"
    _write(str(src / "take.wav"))
    out = tmp_path / "out"
    code = cli.run([str(src), "-o", str(out), "-q"])
    assert code == 0
    assert (out / "Master_Catalog.xlsx").exists()
