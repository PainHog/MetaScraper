"""Tests for CLI helpers and the fixes around them."""

import os
import sys

from docx import Document
from docx.enum.text import WD_TAB_ALIGNMENT

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from metascraper import cli, docx_style, probe


def test_plan_doc_names_unique_basename():
    plan = cli._plan_doc_names(["/A/clip.mp4"], "/out")
    assert plan[os.path.abspath("/A/clip.mp4")] == os.path.join("/out", "clip.docx")


def test_plan_doc_names_disambiguates_collisions_stably():
    paths = ["/A/clip.mp4", "/B/clip.mp4"]
    plan = cli._plan_doc_names(paths, "/out")
    names = {os.path.basename(v) for v in plan.values()}
    # Two different files with the same base name get two distinct documents.
    assert len(names) == 2
    assert all(n.startswith("clip__") and n.endswith(".docx") for n in names)

    # Order must not change which file maps to which document.
    plan_reordered = cli._plan_doc_names(list(reversed(paths)), "/out")
    for path in paths:
        key = os.path.abspath(path)
        assert plan[key] == plan_reordered[key]


def test_display_path_survives_cross_drive(monkeypatch):
    def boom(_path):
        raise ValueError("path is on mount 'D:', start on mount 'C:'")

    monkeypatch.setattr(cli.os.path, "relpath", boom)
    # Must not raise; falls back to returning the original path.
    assert cli._display_path(r"D:\Footage\clip.mp4") == r"D:\Footage\clip.mp4"


def test_include_flag_strips_whitespace(tmp_path, monkeypatch):
    monkeypatch.setattr(probe.shutil, "which", lambda *_a, **_k: None)
    media = tmp_path / "in"
    media.mkdir()
    (media / "a.aaa").write_bytes(b"\x00")
    (media / "b.bbb").write_bytes(b"\x00")
    out = tmp_path / "out"

    # Note the comma-space; the second extension must still be honored.
    code = cli.run([str(media), "-o", str(out), "-q", "--include", "aaa, bbb"])
    assert code == 0
    docs = sorted(os.listdir(out / "Documents"))
    assert docs == ["a.docx", "b.docx"]


def test_footer_has_right_aligned_tab_stop():
    document = docx_style.new_document()
    docx_style.add_footer(document, "Left label")
    footer_par = document.sections[0].footer.paragraphs[0]
    stops = footer_par.paragraph_format.tab_stops
    assert len(stops) == 1
    assert stops[0].alignment == WD_TAB_ALIGNMENT.RIGHT
    # The stop sits at the printable width (page width minus both margins).
    section = document.sections[0]
    expected = section.page_width - section.left_margin - section.right_margin
    assert abs(stops[0].position - expected) < 2000  # EMU tolerance
