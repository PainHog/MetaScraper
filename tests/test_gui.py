"""Smoke tests for the PySide6 GUI.

These are skipped automatically where Qt's platform libraries can't load (such as
a headless box without libEGL); CI installs those libraries so the tests run
there and on Windows.
"""

import os
import sys
import wave

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Importing QtWidgets pulls in Qt's platform libraries (libEGL, etc.). On a
# headless box without them this raises ImportError for a *transitive* library,
# which importorskip would re-raise — so skip the whole module on any failure.
try:
    from PySide6 import QtWidgets
except Exception as exc:  # noqa: BLE001
    pytest.skip(f"PySide6/Qt unavailable: {exc}", allow_module_level=True)

from metascraper import gui, probe, service  # noqa: E402
from metascraper.service import CatalogOptions  # noqa: E402


@pytest.fixture(scope="module")
def app():
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application


def test_mainwindow_constructs_all_tabs(app):
    window = gui.MainWindow()
    assert window.tabs.count() == 3
    labels = [window.tabs.tabText(i) for i in range(window.tabs.count())]
    assert labels == ["Catalog", "Organize", "Finalize"]


def test_folderlist_dedupes_and_lists(app):
    folder_list = gui.FolderList()
    folder_list.add_path("/a")
    folder_list.add_path("/a")  # duplicate ignored
    folder_list.add_path("/b")
    assert folder_list.paths() == ["/a", "/b"]
    folder_list.selectAll()
    folder_list.remove_selected()
    assert folder_list.paths() == []


def test_catalog_job_built_from_widgets_runs(app, tmp_path, monkeypatch):
    monkeypatch.setattr(probe.shutil, "which", lambda *_a, **_k: None)
    src = tmp_path / "in"
    src.mkdir()
    with wave.open(str(src / "a.wav"), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(48000)
        handle.writeframes(b"\x00\x00" * 48000)
    out = tmp_path / "out"

    window = gui.MainWindow()
    window.cat_folders.add_path(str(src))
    window.cat_output.set_path(str(out))

    # Build the exact options the Run button builds, and run the service.
    opts = CatalogOptions(
        folders=window.cat_folders.paths(),
        recursive=window.cat_recursive.isChecked(),
        output_dir=window.cat_output.path(),
        per_file=window.cat_per_file.isChecked(),
        master=window.cat_master.isChecked(),
    )
    result = service.run_catalog(opts, window._tools(), report=lambda _e: None)
    assert result.processed == 1
    assert (out / "Master_Catalog.xlsx").exists()
