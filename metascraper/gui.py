"""A PySide6 desktop front-end for MetaScraper.

The window is deliberately thin: every button builds an options object and hands
it to :mod:`metascraper.service`, exactly like the CLI does. Adding a new feature
later means adding a tab here and a function there — nothing else.

Run with ``metascraper-gui`` or ``python -m metascraper.gui``.
"""

from __future__ import annotations

import os
import sys
from typing import Callable, List, Optional

from PySide6.QtCore import Qt, QObject, QThread, Signal, Slot, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QFileDialog, QFrame, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QTabWidget, QVBoxLayout, QWidget,
)

from . import __version__, service
from .service import CatalogOptions, Event, FinalizeOptions, OrganizeOptions

APP_NAME = "MetaScraper"


# ---------------------------------------------------------------------------
# Background worker: run a service job off the UI thread
# ---------------------------------------------------------------------------

class Worker(QObject):
    """Runs one job callable in a thread, relaying progress + result via signals."""

    progress = Signal(object)   # service.Event
    finished = Signal(object)   # the job's return value
    failed = Signal(str)

    def __init__(self, job: Callable[[Callable[[Event], None]], object]) -> None:
        super().__init__()
        self._job = job

    @Slot()
    def run(self) -> None:
        try:
            result = self._job(lambda event: self.progress.emit(event))
        except Exception as exc:  # noqa: BLE001 - surfaced to the UI
            self.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.finished.emit(result)


# ---------------------------------------------------------------------------
# A folder list with drag-and-drop
# ---------------------------------------------------------------------------

class FolderList(QListWidget):
    """A list of folders that accepts dropped directories."""

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self.setSelectionMode(QListWidget.ExtendedSelection)
        self.setMinimumHeight(90)
        self.setToolTip("Drag folders here, or use the Add button.")

    def add_path(self, path: str) -> None:
        if path and not self._contains(path):
            self.addItem(QListWidgetItem(path))

    def _contains(self, path: str) -> bool:
        return any(self.item(i).text() == path for i in range(self.count()))

    def paths(self) -> List[str]:
        return [self.item(i).text() for i in range(self.count())]

    def remove_selected(self) -> None:
        for item in self.selectedItems():
            self.takeItem(self.row(item))

    # -- drag and drop --
    def dragEnterEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802
        if not event.mimeData().hasUrls():
            super().dropEvent(event)
            return
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path and os.path.isdir(path):
                self.add_path(path)
            elif path:
                # Dropping a file adds its containing folder.
                self.add_path(os.path.dirname(path))
        event.acceptProposedAction()


# ---------------------------------------------------------------------------
# Small reusable widgets
# ---------------------------------------------------------------------------

def _folder_picker(placeholder: str) -> "PathRow":
    return PathRow(placeholder)


class PathRow(QWidget):
    """A line edit + Browse button for choosing one folder."""

    def __init__(self, placeholder: str) -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(placeholder)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        layout.addWidget(self.edit, 1)
        layout.addWidget(browse)

    def _browse(self) -> None:
        start = self.edit.text() or os.path.expanduser("~")
        chosen = QFileDialog.getExistingDirectory(self, "Choose a folder", start)
        if chosen:
            self.edit.setText(chosen)

    def path(self) -> Optional[str]:
        text = self.edit.text().strip()
        return text or None

    def set_path(self, path: str) -> None:
        self.edit.setText(path)


def _folders_group(folder_list: FolderList) -> QGroupBox:
    box = QGroupBox("Source folders")
    outer = QVBoxLayout(box)
    outer.addWidget(folder_list)
    row = QHBoxLayout()
    add = QPushButton("Add folder…")
    remove = QPushButton("Remove selected")

    def do_add() -> None:
        chosen = QFileDialog.getExistingDirectory(
            box, "Add a source folder", os.path.expanduser("~"))
        if chosen:
            folder_list.add_path(chosen)

    add.clicked.connect(do_add)
    remove.clicked.connect(folder_list.remove_selected)
    row.addWidget(add)
    row.addWidget(remove)
    row.addStretch(1)
    outer.addLayout(row)
    return box


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(820, 640)
        self._thread: Optional[QThread] = None
        self._worker: Optional[Worker] = None

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        self._tool_banner = QLabel()
        self._tool_banner.setWordWrap(True)
        self._tool_banner.setFrameShape(QFrame.StyledPanel)
        self._tool_banner.setContentsMargins(8, 6, 8, 6)
        root.addWidget(self._tool_banner)

        self.ffprobe_row = PathRow("Path to ffprobe (optional — leave blank to use PATH)")
        self.ffprobe_row.edit.textChanged.connect(lambda _t: self._refresh_tools())
        ff_box = QGroupBox("FFmpeg")
        ff_layout = QVBoxLayout(ff_box)
        ff_layout.addWidget(self.ffprobe_row)
        root.addWidget(ff_box)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_catalog_tab(), "Catalog")
        self.tabs.addTab(self._build_organize_tab(), "Organize")
        self.tabs.addTab(self._build_finalize_tab(), "Finalize")
        root.addWidget(self.tabs, 1)

        self.progress = QProgressBar()
        self.progress.setTextVisible(True)
        root.addWidget(self.progress)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(150)
        root.addWidget(self.log, 1)

        self.statusBar().showMessage("Ready")
        self._refresh_tools()

    # -- tool status ------------------------------------------------------

    def _tools(self):
        return service.resolve_tools(ffprobe=self.ffprobe_row.path())

    def _refresh_tools(self) -> None:
        tools = self._tools()
        parts = []
        parts.append("✓ FFmpeg found" if tools.has_ffprobe
                     else "✗ FFmpeg (ffprobe) not found")
        parts.append("✓ ExifTool found" if tools.has_exiftool
                     else "○ ExifTool not found (optional)")
        self._tool_banner.setText(
            "   ".join(parts)
            + ("" if tools.has_ffprobe else
               "  —  Install FFmpeg from ffmpeg.org, or set the ffprobe path below. "
               "Without it, only basic file details are captured.")
        )
        ok = tools.has_ffprobe
        self._tool_banner.setStyleSheet(
            "background:#e8f3ec;color:#1a5;" if ok
            else "background:#fbeceb;color:#a33;")

    # -- catalog tab ------------------------------------------------------

    def _build_catalog_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.cat_folders = FolderList()
        layout.addWidget(_folders_group(self.cat_folders))

        self.cat_output = _folder_picker(
            "Output folder (optional — defaults beside the input)")
        out_box = QGroupBox("Output")
        out_layout = QVBoxLayout(out_box)
        out_layout.addWidget(self.cat_output)
        layout.addWidget(out_box)

        opts = QGroupBox("Options")
        opts_layout = QHBoxLayout(opts)
        self.cat_recursive = QCheckBox("Include subfolders")
        self.cat_recursive.setChecked(True)
        self.cat_per_file = QCheckBox("Per-file documents")
        self.cat_per_file.setChecked(True)
        self.cat_master = QCheckBox("Master catalog (.docx + .xlsx)")
        self.cat_master.setChecked(True)
        for widget in (self.cat_recursive, self.cat_per_file, self.cat_master):
            opts_layout.addWidget(widget)
        opts_layout.addStretch(1)
        layout.addWidget(opts)

        self.cat_run = QPushButton("Run Catalog")
        self.cat_run.setMinimumHeight(36)
        self.cat_run.clicked.connect(self._run_catalog)
        layout.addWidget(self.cat_run)
        layout.addStretch(1)
        return tab

    def _run_catalog(self) -> None:
        folders = self.cat_folders.paths()
        if not folders:
            self._warn("Add at least one source folder first.")
            return
        opts = CatalogOptions(
            folders=folders, recursive=self.cat_recursive.isChecked(),
            output_dir=self.cat_output.path(),
            per_file=self.cat_per_file.isChecked(),
            master=self.cat_master.isChecked(),
        )
        tools = self._tools()
        self._start(lambda report: service.run_catalog(opts, tools, report),
                    self._catalog_done)

    def _catalog_done(self, result) -> None:
        if result.no_media:
            self._warn("No media files were found in the chosen folders.")
            return
        self._log(f"Catalog complete: {result.processed} file(s) processed.")
        if result.summary:
            self._log(f"  Master catalog: {result.new_count} new, "
                      f"{result.updated_count} updated, "
                      f"{result.summary['count']} total.")
        for output in result.master_outputs:
            self._log(f"  → {output}")
        self._offer_open(result.output_dir, "Catalog finished")

    # -- organize tab -----------------------------------------------------

    def _build_organize_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.org_folders = FolderList()
        layout.addWidget(_folders_group(self.org_folders))

        self.org_dest = _folder_picker("Library / destination folder")
        dest_box = QGroupBox("Destination")
        dest_layout = QVBoxLayout(dest_box)
        dest_layout.addWidget(QLabel(
            "Files are copied into Video|Audio / Camera / Date. "
            "Your originals are never changed."))
        dest_layout.addWidget(self.org_dest)
        layout.addWidget(dest_box)

        opts = QGroupBox("Options")
        opts_layout = QHBoxLayout(opts)
        self.org_recursive = QCheckBox("Include subfolders")
        self.org_recursive.setChecked(True)
        self.org_checksum = QCheckBox("Verify with checksum (safer, slower)")
        self.org_checksum.setChecked(True)
        for widget in (self.org_recursive, self.org_checksum):
            opts_layout.addWidget(widget)
        opts_layout.addStretch(1)
        layout.addWidget(opts)

        buttons = QHBoxLayout()
        self.org_preview = QPushButton("Preview (dry run)")
        self.org_preview.clicked.connect(lambda: self._run_organize(dry=True))
        self.org_run = QPushButton("Copy into library")
        self.org_run.setMinimumHeight(36)
        self.org_run.clicked.connect(lambda: self._run_organize(dry=False))
        buttons.addWidget(self.org_preview)
        buttons.addWidget(self.org_run, 1)
        layout.addLayout(buttons)
        layout.addStretch(1)
        return tab

    def _run_organize(self, dry: bool) -> None:
        folders = self.org_folders.paths()
        if not folders:
            self._warn("Add at least one source folder first.")
            return
        opts = OrganizeOptions(
            folders=folders, recursive=self.org_recursive.isChecked(),
            dest=self.org_dest.path(), dry_run=dry,
            checksum=self.org_checksum.isChecked(),
        )
        tools = self._tools()
        self._start(lambda report: service.run_organize(opts, tools, report),
                    self._organize_done)

    def _organize_done(self, outcome) -> None:
        if outcome.no_media:
            self._warn("No media files were found in the chosen folders.")
            return
        # Remember the destination for the Finalize tab's convenience.
        if not self.fin_dest.path():
            self.fin_dest.set_path(outcome.dest_root)
        if outcome.dry_run:
            self._log(f"Dry run: {len(outcome.plan)} file(s) would be organized "
                      f"into {outcome.dest_root}")
            for move in outcome.plan[:100]:
                self._log(f"    → {os.path.relpath(move.dest, outcome.dest_root)}")
            if len(outcome.plan) > 100:
                self._log(f"    … and {len(outcome.plan) - 100} more")
            return
        result = outcome.result
        self._log(f"Organize complete: {result.copied} copied, "
                  f"{result.skipped} already in place"
                  + (f", {result.renamed} renamed" if result.renamed else "") + ".")
        self._log("Originals are untouched — use the Finalize tab to remove them "
                  "once you've checked the copies.")
        self._offer_open(outcome.dest_root, "Organize finished")

    # -- finalize tab -----------------------------------------------------

    def _build_finalize_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        info = QLabel(
            "Finalize is the 'move later' step: it deletes the original files "
            "whose organized copies check out. Nothing is deleted unless its copy "
            "verifies. Always Preview first.")
        info.setWordWrap(True)
        layout.addWidget(info)

        self.fin_dest = _folder_picker("Organized library folder (the destination)")
        dest_box = QGroupBox("Library")
        dest_layout = QVBoxLayout(dest_box)
        dest_layout.addWidget(self.fin_dest)
        layout.addWidget(dest_box)

        self.fin_checksum = QCheckBox("Re-verify with checksum before deleting")
        self.fin_checksum.setChecked(True)
        layout.addWidget(self.fin_checksum)

        buttons = QHBoxLayout()
        self.fin_preview = QPushButton("Preview what would be deleted")
        self.fin_preview.clicked.connect(lambda: self._run_finalize(apply=False))
        self.fin_delete = QPushButton("Delete originals")
        self.fin_delete.setMinimumHeight(36)
        self.fin_delete.clicked.connect(lambda: self._run_finalize(apply=True))
        buttons.addWidget(self.fin_preview)
        buttons.addWidget(self.fin_delete, 1)
        layout.addLayout(buttons)
        layout.addStretch(1)
        return tab

    def _run_finalize(self, apply: bool) -> None:
        dest = self.fin_dest.path()
        if not dest:
            self._warn("Choose your organized library folder first.")
            return
        if apply:
            confirmed = QMessageBox.question(
                self, "Delete originals?",
                "This permanently deletes the original files whose copies verify.\n"
                "The organized copies are kept. Continue?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if confirmed != QMessageBox.Yes:
                return
        opts = FinalizeOptions(
            dest=dest, checksum=self.fin_checksum.isChecked(), apply=apply)
        self._start(lambda report: service.run_finalize(opts, report),
                    self._finalize_done)

    def _finalize_done(self, outcome) -> None:
        if outcome.missing_manifest:
            self._warn("No organize manifest found in that folder.\n"
                       "Run Organize first, then finalize the same destination.")
            return
        if outcome.pending == 0:
            self._log("Nothing to finalize — no copied originals are awaiting deletion.")
            return
        result = outcome.result
        from .utils import human_file_size
        if outcome.preview:
            self._log(f"Preview: would delete {result.deleted} original(s), "
                      f"freeing {human_file_size(result.freed_bytes)}. "
                      "Nothing was deleted.")
        else:
            self._log(f"Deleted {result.deleted} original(s), "
                      f"freed {human_file_size(result.freed_bytes)}.")
        if result.problems:
            self._log(f"  {len(result.problems)} kept (did not verify).")

    # -- worker plumbing --------------------------------------------------

    def _start(self, job, on_result) -> None:
        if self._thread is not None:
            self._warn("A task is already running — please wait for it to finish.")
            return
        self._set_running(True)
        self.progress.setRange(0, 0)  # busy until the first counted event
        self.log.clear()

        self._thread = QThread(self)
        self._worker = Worker(job)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(lambda r: self._on_finished(r, on_result))
        self._worker.failed.connect(self._on_failed)
        self._thread.start()

    @Slot(object)
    def _on_progress(self, event: Event) -> None:
        if event.kind in ("phase", "done") and event.message:
            self._log(event.message)
        elif event.kind == "item" and event.message:
            if event.current and event.total:
                self.progress.setRange(0, event.total)
                self.progress.setValue(event.current)
                self.statusBar().showMessage(
                    f"[{event.current}/{event.total}] {event.message}")
            else:
                self._log(f"    {event.message}")

    def _on_finished(self, result, on_result) -> None:
        self._teardown_thread()
        self._set_running(False)
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.statusBar().showMessage("Done")
        try:
            on_result(result)
        except Exception as exc:  # noqa: BLE001
            self._warn(f"Finished, but displaying the result failed: {exc}")

    def _on_failed(self, message: str) -> None:
        self._teardown_thread()
        self._set_running(False)
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.statusBar().showMessage("Failed")
        self._warn(f"The task failed:\n{message}")

    def _teardown_thread(self) -> None:
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait()
        self._thread = None
        self._worker = None

    def _set_running(self, running: bool) -> None:
        for button in (self.cat_run, self.org_run, self.org_preview,
                       self.fin_preview, self.fin_delete):
            button.setEnabled(not running)

    # -- small helpers ----------------------------------------------------

    def _log(self, message: str) -> None:
        self.log.appendPlainText(message)

    def _warn(self, message: str) -> None:
        QMessageBox.warning(self, APP_NAME, message)

    def _offer_open(self, folder: str, title: str) -> None:
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setText(f"{title}.\n\nOutput folder:\n{folder}")
        open_button = box.addButton("Open folder", QMessageBox.AcceptRole)
        box.addButton("Close", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is open_button and os.path.isdir(folder):
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
