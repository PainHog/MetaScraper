"""Tests for the PySide6 GUI.

These are skipped automatically where Qt's platform libraries can't load (such as
a headless box without libEGL); CI installs those libraries so the tests run
there and on Windows.
"""

import os
import sys
import threading
import time
import wave

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Importing QtWidgets pulls in Qt's platform libraries (libEGL, etc.). On a
# headless box without them this raises ImportError for a *transitive* library,
# which importorskip would re-raise — so skip the whole module on any failure.
try:
    from PySide6 import QtWidgets
    from PySide6.QtCore import QSettings, Qt
except Exception as exc:  # noqa: BLE001
    pytest.skip(f"PySide6/Qt unavailable: {exc}", allow_module_level=True)

from metascraper import gui, probe, service, theme  # noqa: E402
from metascraper.service import CatalogOptions  # noqa: E402


@pytest.fixture(scope="module")
def app():
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application


@pytest.fixture
def settings(tmp_path):
    return QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)


@pytest.fixture
def window(app, settings, monkeypatch):
    monkeypatch.setattr(probe.shutil, "which", lambda *_a, **_k: None)
    win = gui.MainWindow(settings)
    win.warnings = []
    # Modal dialogs would block a test; record them instead.
    win._warn = win.warnings.append
    win._offer_open = lambda *_a: None
    yield win
    win._running = False
    win.close()
    win.deleteLater()


def _wav(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with wave.open(path, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(48000)
        handle.writeframes(b"\x00\x00" * 4800)


def _blob(path, size=2048):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(os.urandom(size))


def _wait(app, win, timeout=30.0):
    """Pump the event loop until the window's background task has finished."""
    deadline = time.time() + timeout
    while (win._running or win._thread is not None) and time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    assert not win._running, "background task did not finish"


def test_mainwindow_has_all_pages(window):
    labels = [window.nav.item(i).text() for i in range(window.nav.count())]
    assert labels == ["Catalog", "Organize", "Finalize", "Settings"]
    assert window.pages.count() == 4
    window.nav.setCurrentRow(1)
    assert window.pages.currentIndex() == 1


def test_folderlist_dedupes_and_lists(app):
    folder_list = gui.FolderList()
    folder_list.add_path("/a")
    folder_list.add_path("/a")  # duplicate ignored
    folder_list.add_path("/b")
    assert folder_list.paths() == [os.path.normpath("/a"), os.path.normpath("/b")]
    folder_list.selectAll()
    folder_list.remove_selected()
    assert folder_list.paths() == []


def test_catalog_job_built_from_widgets_runs(window, tmp_path):
    src = tmp_path / "in"
    _wav(str(src / "a.wav"))
    out = tmp_path / "out"
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


def test_run_catalog_button_through_worker_thread(app, window, tmp_path):
    src = tmp_path / "in"
    _wav(str(src / "a.wav"))
    window.cat_folders.add_path(str(src))
    window.cat_output.set_path(str(tmp_path / "out"))
    window._run_catalog()
    _wait(app, window)
    assert (tmp_path / "out" / "Master_Catalog.xlsx").exists()
    assert "Catalog complete" in window.log.toPlainText()
    assert window.cat_run.isEnabled()


def test_result_handler_runs_on_the_ui_thread(app, window):
    # Regression: the finished handler used to run on the worker thread,
    # touching widgets (and waiting on its own thread) from there.
    seen = {}
    ui_thread = threading.get_ident()

    def job(report, should_stop):
        seen["job_thread"] = threading.get_ident()
        return 42

    window._start(job, lambda result: seen.update(
        result=result, handler_thread=threading.get_ident()), "Working…")
    _wait(app, window)
    assert seen["result"] == 42
    assert seen["job_thread"] != ui_thread
    assert seen["handler_thread"] == ui_thread


def test_cancel_stops_a_running_job(app, window):
    started = threading.Event()

    def job(report, should_stop):
        started.set()
        while not should_stop():
            time.sleep(0.01)
        return "stopped"

    got = {}
    window._start(job, lambda r: got.update(r=r), "Working…")
    assert started.wait(5)
    assert window.cancel_button.isVisible() or window.cancel_button.isEnabled()
    window._cancel()
    _wait(app, window)
    assert got["r"] == "stopped"


def test_assignment_model(app):
    model = gui.AssignmentModel()
    infos = [gui.MediaInfo(path=os.path.abspath(f"/src/{n}"), name=n, ext=".mp4",
                           media_kind="Video")
             for n in ("a.mp4", "b.mp4", "c.mp4")]
    model.set_infos(infos)
    model.set_projects(["Doc", "Reel"])
    assert model.columnCount() == 1 + 2 + 5
    assert model.headerData(1, Qt.Horizontal) == "Doc"
    assert model.headerData(3, Qt.Horizontal) == "Type"

    model.set_rows([0, 1], "Doc", True)
    # Ticking through the Qt API, as a click does (Qt may pass a plain int).
    assert model.setData(model.index(1, 2), 2, Qt.CheckStateRole)
    assert model.assignments() == {infos[0].path: ["Doc"],
                                   infos[1].path: ["Doc", "Reel"]}
    assert model.stats() == (3, 2, 3)

    # Removing a project unticks it everywhere; ticks survive a re-scan.
    model.set_projects(["Reel"])
    model.set_infos(list(reversed(infos)))
    assert model.assignments() == {infos[1].path: ["Reel"]}


def test_organize_projects_flow_end_to_end(app, window, tmp_path, monkeypatch):
    src = tmp_path / "card"
    _blob(str(src / "DCIM" / "C0001.MP4"))
    _blob(str(src / "DCIM" / "C0002.MP4"))
    _wav(str(src / "AUDIO" / "ZOOM0001.WAV"))
    lib = tmp_path / "Library"
    lib.mkdir()

    window.org_folders.add_path(str(src))
    window.org_dest.set_path(str(lib))
    window._scan()
    _wait(app, window)
    assert window.org_model.rowCount() == 3

    window._set_projects(["Wildlife Doc", "Client Reel"])
    names = [os.path.basename(i.path) for i in window.org_model.infos()]
    row = {name: index for index, name in enumerate(names)}
    window.org_model.set_rows([row["C0001.MP4"], row["ZOOM0001.WAV"]],
                              "Wildlife Doc", True)
    window.org_model.set_rows([row["C0001.MP4"]], "Client Reel", True)
    assert window.org_run.isEnabled()

    # Preview copies nothing.
    window._run_organize(dry=True)
    _wait(app, window)
    assert "Preview: 3 copies" in window.log.toPlainText()
    assert not any(p.is_file() for p in lib.rglob("*"))

    window._run_organize(dry=False)
    _wait(app, window)
    assert window.warnings == []
    assert list((lib / "Wildlife Doc").rglob("C0001.MP4"))
    assert list((lib / "Wildlife Doc").rglob("ZOOM0001.WAV"))
    assert list((lib / "Client Reel").rglob("C0001.MP4"))
    assert not list(lib.rglob("C0002.MP4"))  # unassigned -> skipped
    # The "Already in" column now reflects the copies.
    already = window.org_model.index(row["C0001.MP4"], window.org_model.columnCount() - 1)
    assert window.org_model.data(already) == "Wildlife Doc, Client Reel"

    # Finalize: preview, then delete (confirm dialog auto-accepted).
    assert window.fin_dest.path() == str(lib)
    monkeypatch.setattr(gui.QMessageBox, "question",
                        lambda *_a, **_k: gui.QMessageBox.Yes)
    window._run_finalize(apply=True)
    _wait(app, window)
    assert not (src / "DCIM" / "C0001.MP4").exists()
    assert not (src / "AUDIO" / "ZOOM0001.WAV").exists()
    assert (src / "DCIM" / "C0002.MP4").exists()  # never copied, never deleted
    assert "Deleted 2 original(s)" in window.log.toPlainText()


def test_organize_requires_a_project_tick(app, window, tmp_path):
    src = tmp_path / "card"
    _blob(str(src / "C0001.MP4"))
    window.org_folders.add_path(str(src))
    window.org_dest.set_path(str(tmp_path / "Library"))
    window._scan()
    _wait(app, window)
    window._run_organize(dry=False)
    assert window.warnings and "Tick at least one project" in window.warnings[-1]
    assert not window.org_run.isEnabled()


def test_unassigned_to_root_option(app, window, tmp_path):
    src = tmp_path / "card"
    _blob(str(src / "C0001.MP4"))
    _blob(str(src / "C0002.MP4"))
    lib = tmp_path / "Library"
    window.org_folders.add_path(str(src))
    window.org_dest.set_path(str(lib))
    window._scan()
    _wait(app, window)
    assert not window.org_run.isEnabled()      # nothing ticked, option off

    window.org_to_root.setChecked(True)        # plain organize, no projects
    assert window.org_run.isEnabled()
    assert "to the library root" in window.org_summary.text()
    window._run_organize(dry=False)
    _wait(app, window)
    assert window.warnings == []
    assert len(list((lib / "Video").rglob("*.MP4"))) == 2
    assert window.settings.value("organize/unassigned_to_root") in (True, "true")


def test_scan_warns_about_missing_folders(app, window, tmp_path):
    window.org_folders.add_path(str(tmp_path / "not-there"))
    window._scan()
    _wait(app, window)
    assert window.warnings and "Folder not found" in window.warnings[-1]
    assert "Warning: Folder not found" in window.log.toPlainText()


def test_projects_and_paths_persist(app, settings, tmp_path, monkeypatch):
    monkeypatch.setattr(probe.shutil, "which", lambda *_a, **_k: None)
    lib = tmp_path / "Library"
    (lib / "From Disk" / "Video").mkdir(parents=True)

    first = gui.MainWindow(settings)
    first._set_projects(["Doc"])
    first.org_dest.set_path(str(lib))          # picks up "From Disk"
    first.org_folders.add_path(str(tmp_path))
    first.close()
    first.deleteLater()

    second = gui.MainWindow(settings)
    assert second._projects == ["Doc", "From Disk"]
    assert second.org_dest.path() == str(lib)
    assert second.org_folders.paths() == [str(tmp_path)]
    second.close()
    second.deleteLater()


def test_new_project_validation(window, monkeypatch):
    answers = iter([("Doc", True), ("doc", True), ("a/b", True), ("Reel", False)])
    monkeypatch.setattr(gui.QInputDialog, "getText", lambda *_a, **_k: next(answers))
    for _ in range(4):
        window._new_project()
    assert window._projects == ["Doc"]
    assert any("already a project" in w for w in window.warnings)
    assert any("folder name" in w for w in window.warnings)


def test_tool_path_accepts_a_folder(tmp_path):
    exe = tmp_path / "bin" / "ffprobe"
    exe.parent.mkdir()
    exe.write_text("")
    assert gui._tool_path(str(tmp_path), "ffprobe") == str(exe)
    assert gui._tool_path(None, "ffprobe") is None


def test_themes_apply(app):
    for mode in theme.MODES:
        tokens = theme.apply(app, mode)
        assert tokens in (theme.LIGHT, theme.DARK)
    assert theme.apply(app, "dark") is theme.DARK
    assert theme.apply(app, "light") is theme.LIGHT
