"""A PySide6 desktop front-end for MetaScraper.

The window is deliberately thin: every button builds an options object and hands
it to :mod:`metascraper.service`, exactly like the CLI does. Adding a new feature
later means adding a page here and a function there — nothing else.

Run with ``metascraper-gui`` or ``python -m metascraper.gui``.
"""

from __future__ import annotations

import os
import sys
import threading
from collections import Counter
from typing import Callable, Dict, List, Optional, Set

from PySide6.QtCore import (
    QAbstractTableModel, QEvent, QModelIndex, QObject, QRect, QSettings,
    QSortFilterProxyModel, QThread, QUrl, Qt, Signal, Slot,
)
from PySide6.QtGui import QDesktopServices, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QFileDialog, QFrame,
    QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMenu, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton, QScrollArea, QSplitter, QStackedWidget, QStyle,
    QStyledItemDelegate, QStyleOptionButton, QStyleOptionViewItem, QTableView,
    QVBoxLayout, QWidget,
)

from . import __version__, organizer, service, theme
from .models import MediaInfo
from .service import (
    CatalogOptions, Event, FinalizeOptions, OrganizeOptions, ScanOptions,
)
from .utils import human_file_size

APP_NAME = "MetaScraper"
PAGES = ["Catalog", "Organize", "Finalize", "Settings"]


# ---------------------------------------------------------------------------
# Background worker: run a service job off the UI thread
# ---------------------------------------------------------------------------

class Worker(QObject):
    """Runs one job in a thread, relaying progress + result via signals.

    The job is called as ``job(report, should_stop)``; ``should_stop`` turns
    True once :meth:`cancel` is called, and the service stops between files.
    """

    progress = Signal(object)   # service.Event
    finished = Signal(object)   # the job's return value
    failed = Signal(str)

    def __init__(self, job: Callable[..., object]) -> None:
        super().__init__()
        self._job = job
        self._stop = threading.Event()

    def cancel(self) -> None:
        self._stop.set()

    @Slot()
    def run(self) -> None:
        try:
            result = self._job(self.progress.emit, self._stop.is_set)
        except Exception as exc:  # noqa: BLE001 - surfaced to the UI
            self.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.finished.emit(result)


# ---------------------------------------------------------------------------
# Small reusable widgets
# ---------------------------------------------------------------------------

def _button(text: str, variant: Optional[str] = None) -> QPushButton:
    button = QPushButton(text)
    if variant:
        button.setProperty("variant", variant)
    button.setCursor(Qt.PointingHandCursor)
    return button


def _label(text: str, name: Optional[str] = None, wrap: bool = False) -> QLabel:
    label = QLabel(text)
    if name:
        label.setObjectName(name)
    label.setWordWrap(wrap)
    return label


def _repolish(widget: QWidget) -> None:
    """Re-apply the stylesheet after a dynamic property change."""
    widget.style().unpolish(widget)
    widget.style().polish(widget)


class Card(QFrame):
    """A rounded panel with a title row and a body layout."""

    def __init__(self, title: str, subtitle: str = "", step: Optional[int] = None):
        super().__init__()
        self.setObjectName("card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(16, 14, 16, 16)
        self.body.setSpacing(10)
        self.header = QHBoxLayout()
        self.header.setSpacing(8)
        if step is not None:
            self.header.addWidget(_label(str(step), "step"))
        self.header.addWidget(_label(title, "cardTitle"))
        self.header.addStretch(1)
        self.body.addLayout(self.header)
        if subtitle:
            self.body.addWidget(_label(subtitle, "muted", wrap=True))


class FolderList(QListWidget):
    """A list of folders that accepts dropped directories."""

    changed = Signal()

    def __init__(self, height: int = 110) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self.setSelectionMode(QListWidget.ExtendedSelection)
        self.setMinimumHeight(min(84, height))
        self.setMaximumHeight(height)
        self.setToolTip("Drag folders here, or use Add folder.")

    def add_path(self, path: str) -> None:
        path = os.path.normpath(path) if path else path
        if path and not self._contains(path):
            self.addItem(QListWidgetItem(path))
            self.changed.emit()

    def _contains(self, path: str) -> bool:
        return any(self.item(i).text() == path for i in range(self.count()))

    def paths(self) -> List[str]:
        return [self.item(i).text() for i in range(self.count())]

    def set_paths(self, paths: List[str]) -> None:
        self.clear()
        for path in paths:
            self.add_path(path)

    def remove_selected(self) -> None:
        items = self.selectedItems()
        for item in items:
            self.takeItem(self.row(item))
        if items:
            self.changed.emit()

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        super().paintEvent(event)
        if self.count() == 0:
            painter = QPainter(self.viewport())
            painter.setPen(self.palette().placeholderText().color())
            painter.drawText(self.viewport().rect(), Qt.AlignCenter,
                             "Drop folders here, or click Add folder")

    # -- drag and drop --
    def dragEnterEvent(self, event) -> None:  # noqa: N802
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


def _folders_widget(folder_list: FolderList, extra: Optional[QWidget] = None) -> QWidget:
    """The folder list plus its Add / Remove buttons."""
    widget = QWidget()
    outer = QVBoxLayout(widget)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.addWidget(folder_list, 1)
    row = QHBoxLayout()
    add = _button("Add folder…")
    remove = _button("Remove")

    def do_add() -> None:
        chosen = QFileDialog.getExistingDirectory(
            widget, "Add a source folder", os.path.expanduser("~"))
        if chosen:
            folder_list.add_path(chosen)

    add.clicked.connect(do_add)
    remove.clicked.connect(folder_list.remove_selected)
    row.addWidget(add)
    row.addWidget(remove)
    row.addStretch(1)
    if extra is not None:
        row.addWidget(extra)
    outer.addLayout(row)
    return widget


class PathRow(QWidget):
    """A line edit + Browse button for choosing one folder (or file)."""

    changed = Signal(str)

    def __init__(self, placeholder: str, pick_file: bool = False,
                 openable: bool = False, stacked: bool = False) -> None:
        super().__init__()
        self._pick_file = pick_file
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(placeholder)
        self.edit.setClearButtonEnabled(True)
        self.edit.editingFinished.connect(lambda: self.changed.emit(self.edit.text()))
        browse = _button("Browse…")
        browse.clicked.connect(self._browse)
        row = QHBoxLayout()
        row.setSpacing(8)
        if stacked:
            outer.addWidget(self.edit)
        else:
            row.addWidget(self.edit, 1)
        row.addWidget(browse)
        if openable:
            show = _button("Open")
            show.setToolTip("Open this folder")
            show.clicked.connect(self._open)
            row.addWidget(show)
        if stacked:
            row.addStretch(1)
        outer.addLayout(row)

    def set_path_quietly(self, path: str) -> None:
        """Fill in a path without emitting :attr:`changed` (e.g. on startup)."""
        self.edit.setText(os.path.normpath(path) if path else "")

    def _browse(self) -> None:
        current = self.edit.text().strip()
        start = current if current and os.path.exists(current) else os.path.expanduser("~")
        if self._pick_file:
            chosen, _ = QFileDialog.getOpenFileName(self, "Choose a file", start)
        else:
            chosen = QFileDialog.getExistingDirectory(self, "Choose a folder", start)
        if chosen:
            self.set_path(chosen)

    def _open(self) -> None:
        path = self.path()
        if path and os.path.isdir(path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def path(self) -> Optional[str]:
        text = self.edit.text().strip()
        return text or None

    def set_path(self, path: str) -> None:
        path = os.path.normpath(path) if path else ""
        if path != self.edit.text():
            self.edit.setText(path)
            self.changed.emit(path)


def _tool_path(path: Optional[str], name: str) -> Optional[str]:
    """Accept either the tool itself or a folder containing it (or its bin/)."""
    if not path or not os.path.isdir(path):
        return path
    for folder in (path, os.path.join(path, "bin")):
        for candidate in (f"{name}.exe", name):
            full = os.path.join(folder, candidate)
            if os.path.isfile(full):
                return full
    return path


def _settings_list(settings: QSettings, key: str) -> List[str]:
    value = settings.value(key, [])
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    return [str(v) for v in value if v]


# ---------------------------------------------------------------------------
# The project assignment table
# ---------------------------------------------------------------------------

def _key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _is_checked(value) -> bool:
    raw = getattr(value, "value", value)
    try:
        return int(raw) == Qt.CheckState.Checked.value
    except (TypeError, ValueError):
        return bool(value)


class AssignmentModel(QAbstractTableModel):
    """Scanned files as rows: the file name, one checkbox column per project,
    then the file's details."""

    FIXED = ["File", "Type", "Camera", "Recorded", "Size", "Already in"]
    SIZE, ALREADY = 4, 5   # positions within FIXED
    SORT_ROLE = Qt.UserRole + 1

    assignmentsChanged = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._infos: List[MediaInfo] = []
        self._projects: List[str] = []
        # Kept across re-scans, so ticks survive refreshing the file list.
        self._assigned: Dict[str, Set[str]] = {}
        self._existing: Dict[str, List[str]] = {}

    # -- content ----------------------------------------------------------

    def infos(self) -> List[MediaInfo]:
        return list(self._infos)

    def projects(self) -> List[str]:
        return list(self._projects)

    def set_infos(self, infos: List[MediaInfo]) -> None:
        self.beginResetModel()
        self._infos = list(infos)
        self.endResetModel()
        self.assignmentsChanged.emit()

    def set_projects(self, projects: List[str]) -> None:
        self.beginResetModel()
        self._projects = list(projects)
        keep = set(projects)
        for names in self._assigned.values():
            names &= keep
        self.endResetModel()
        self.assignmentsChanged.emit()

    def set_existing(self, existing: Dict[str, List[str]]) -> None:
        self._existing = dict(existing)
        column = self.columnCount() - 1
        if self._infos:
            self.dataChanged.emit(self.index(0, column),
                                  self.index(len(self._infos) - 1, column))

    def project_at(self, column: int) -> Optional[str]:
        if 1 <= column <= len(self._projects):
            return self._projects[column - 1]
        return None

    def fixed_at(self, column: int) -> int:
        """Position in FIXED for a non-project column."""
        return 0 if column == 0 else column - len(self._projects)

    def project_columns(self) -> range:
        return range(1, len(self._projects) + 1)

    def is_assigned(self, row: int, project: str) -> bool:
        return project in self._assigned.get(_key(self._infos[row].path), set())

    def set_rows(self, rows: List[int], project: str, on: bool) -> None:
        column = 1 + self._projects.index(project)
        for row in rows:
            names = self._assigned.setdefault(_key(self._infos[row].path), set())
            if on:
                names.add(project)
            else:
                names.discard(project)
            index = self.index(row, column)
            self.dataChanged.emit(index, index, [Qt.CheckStateRole])
        self.assignmentsChanged.emit()

    def assignments(self) -> Dict[str, List[str]]:
        """Source path -> projects, for listed files with at least one tick."""
        result: Dict[str, List[str]] = {}
        for info in self._infos:
            chosen = self._assigned.get(_key(info.path), set())
            names = [p for p in self._projects if p in chosen]
            if names:
                result[info.path] = names
        return result

    def stats(self) -> tuple:
        """(files listed, files with a project, copies to make)."""
        assigned = self.assignments()
        return len(self._infos), len(assigned), sum(len(v) for v in assigned.values())

    def count_for(self, project: str) -> int:
        return sum(1 for info in self._infos
                   if project in self._assigned.get(_key(info.path), set()))

    # -- Qt model API -----------------------------------------------------

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._infos)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.FIXED) + len(self._projects)

    def headerData(self, section, orientation, role=Qt.DisplayRole):  # noqa: N802
        if orientation != Qt.Horizontal:
            return None
        project = self.project_at(section)
        if role == Qt.DisplayRole:
            return project if project is not None else self.FIXED[self.fixed_at(section)]
        if role == Qt.ToolTipRole and project is not None:
            return f"Tick to copy into the '{project}' project"
        return None

    def flags(self, index):
        if not index.isValid():
            return Qt.NoItemFlags
        base = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if self.project_at(index.column()) is not None:
            base |= Qt.ItemIsUserCheckable
        return base

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        info = self._infos[index.row()]
        column = index.column()
        project = self.project_at(column)

        if project is not None:
            checked = self.is_assigned(index.row(), project)
            if role == Qt.CheckStateRole:
                return Qt.Checked if checked else Qt.Unchecked
            if role == self.SORT_ROLE:
                return 0 if checked else 1
            return None

        existing = self._existing.get(_key(info.path), [])
        column = self.fixed_at(column)
        if role in (Qt.DisplayRole, self.SORT_ROLE):
            if column == self.SIZE and role == self.SORT_ROLE:
                return info.size_bytes if info.size_bytes is not None else -1
            value = [
                info.name,
                info.media_kind,
                organizer.camera_label(info),
                organizer.date_label(info),
                human_file_size(info.size_bytes),
                ", ".join(existing),
            ][column]
            return value.lower() if role == self.SORT_ROLE else value
        if role == Qt.ToolTipRole:
            if column == 0:
                return info.path
            if column == self.ALREADY and existing:
                return "Already copied into: " + ", ".join(existing)
        if role == Qt.TextAlignmentRole and column == self.SIZE:
            return int(Qt.AlignRight | Qt.AlignVCenter)
        return None

    def setData(self, index, value, role=Qt.EditRole) -> bool:  # noqa: N802
        project = self.project_at(index.column()) if index.isValid() else None
        if project is None or role != Qt.CheckStateRole:
            return False
        self.set_rows([index.row()], project, _is_checked(value))
        return True


class CheckDelegate(QStyledItemDelegate):
    """Draws a centered checkbox and toggles it on a click anywhere in the cell."""

    def paint(self, painter, option, index) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        checked = _is_checked(index.data(Qt.CheckStateRole))
        opt.features &= ~QStyleOptionViewItem.ViewItemFeature.HasCheckIndicator
        opt.text = ""
        style = opt.widget.style() if opt.widget else QApplication.style()
        style.drawControl(QStyle.CE_ItemViewItem, opt, painter, opt.widget)

        box = QStyleOptionButton()
        size = style.pixelMetric(QStyle.PM_IndicatorWidth, None, opt.widget)
        center = option.rect.center()
        box.rect = QRect(center.x() - size // 2, center.y() - size // 2, size, size)
        box.state = QStyle.State_Enabled | (QStyle.State_On if checked else QStyle.State_Off)
        box.palette = opt.palette
        style.drawPrimitive(QStyle.PE_IndicatorCheckBox, box, painter, opt.widget)

    def editorEvent(self, event, model, option, index) -> bool:  # noqa: N802
        if not (index.flags() & Qt.ItemIsUserCheckable):
            return False
        kind = event.type()
        toggle = False
        if kind == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
            toggle = option.rect.contains(event.position().toPoint())
        elif kind == QEvent.MouseButtonDblClick:
            return True  # a double click shouldn't toggle twice
        elif kind == QEvent.KeyPress and event.key() in (Qt.Key_Space, Qt.Key_Select):
            toggle = True
        if toggle:
            checked = _is_checked(index.data(Qt.CheckStateRole))
            model.setData(index, Qt.Unchecked if checked else Qt.Checked,
                          Qt.CheckStateRole)
            return True
        return False


class AssignmentTable(QTableView):
    """The file table, with an empty-state message."""

    def __init__(self) -> None:
        super().__init__()
        self.empty_text = "Add source folders, then click Scan files."

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        model = self.model()
        if model is None or model.rowCount() > 0:
            return
        source = model.sourceModel() if hasattr(model, "sourceModel") else None
        text = ("No files match the filter." if source is not None
                and source.rowCount() > 0 else self.empty_text)
        painter = QPainter(self.viewport())
        painter.setPen(self.palette().placeholderText().color())
        painter.drawText(self.viewport().rect(), Qt.AlignCenter, text)


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self, settings: Optional[QSettings] = None) -> None:
        super().__init__()
        self.settings = settings or QSettings(APP_NAME, APP_NAME)
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(860, 560)
        self._thread: Optional[QThread] = None
        self._worker: Optional[Worker] = None
        self._on_result: Optional[Callable[[object], None]] = None
        self._running = False
        self._close_when_done = False
        self._projects: List[str] = []

        central = QWidget()
        central.setObjectName("root")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        root.addLayout(body, 1)

        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setFixedWidth(176)
        self.nav.addItems(PAGES)
        body.addWidget(self.nav)

        right = QVBoxLayout()
        right.setContentsMargins(20, 16, 20, 12)
        right.setSpacing(10)
        body.addLayout(right, 1)

        self.banner = self._build_banner()
        right.addWidget(self.banner)

        self.pages = QStackedWidget()
        self.pages.addWidget(self._scroll(self._build_catalog_page()))
        self.pages.addWidget(self._build_organize_page())
        self.pages.addWidget(self._scroll(self._build_finalize_page()))
        self.pages.addWidget(self._scroll(self._build_settings_page()))
        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)

        self.splitter = QSplitter(Qt.Vertical)
        self.splitter.setChildrenCollapsible(True)
        self.splitter.addWidget(self.pages)
        self.splitter.addWidget(self._build_log_panel())
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 0)
        self.splitter.setSizes([640, 110])
        right.addWidget(self.splitter, 1)
        right.addLayout(self._build_activity_bar())

        self._load_settings()
        self.nav.setCurrentRow(int(self.settings.value("window/page", 0) or 0)
                               % len(PAGES))
        self._refresh_tools()
        self._refresh_buttons()

    # -- layout pieces ----------------------------------------------------

    def _build_header(self) -> QWidget:
        header = QFrame()
        header.setObjectName("header")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(20, 12, 20, 12)
        layout.addWidget(_label(APP_NAME, "appTitle"))
        version = _label(f"v{__version__}", "muted")
        layout.addWidget(version, 0, Qt.AlignBottom)
        layout.addStretch(1)
        self.ffprobe_pill = _label("", "pill")
        self.exiftool_pill = _label("", "pill")
        layout.addWidget(self.ffprobe_pill)
        layout.addWidget(self.exiftool_pill)
        return header

    def _build_banner(self) -> QWidget:
        banner = QFrame()
        banner.setObjectName("banner")
        layout = QHBoxLayout(banner)
        layout.setContentsMargins(12, 8, 8, 8)
        text = _label(
            "FFmpeg (ffprobe) wasn't found, so only basic file details can be read. "
            "Install it with  winget install Gyan.FFmpeg  or set its location in "
            "Settings.", wrap=True)
        layout.addWidget(text, 1)
        go = _button("Open Settings", "link")
        go.clicked.connect(lambda: self.nav.setCurrentRow(PAGES.index("Settings")))
        layout.addWidget(go)
        return banner

    def _build_log_panel(self) -> QWidget:
        panel = Card("Activity")
        panel.body.setContentsMargins(14, 8, 14, 12)
        panel.body.setSpacing(6)
        clear = _button("Clear", "link")
        panel.header.addWidget(clear)
        self.log = QPlainTextEdit()
        self.log.setObjectName("log")
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(40)
        self.log.setPlaceholderText("Progress and results appear here.")
        clear.clicked.connect(self.log.clear)
        panel.body.addWidget(self.log, 1)
        return panel

    def _build_activity_bar(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self.status = _label("Ready", "muted")
        self.status.setMinimumWidth(100)
        row.addWidget(self.status, 1)
        self.progress = QProgressBar()
        self.progress.setFixedWidth(260)
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        row.addWidget(self.progress)
        self.cancel_button = _button("Cancel")
        self.cancel_button.setVisible(False)
        self.cancel_button.clicked.connect(self._cancel)
        row.addWidget(self.cancel_button)
        return row

    @staticmethod
    def _scroll(page: QWidget) -> QScrollArea:
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.NoFrame)
        area.viewport().setAutoFillBackground(False)
        area.setWidget(page)
        return area

    @staticmethod
    def _page(title: str, subtitle: str) -> tuple:
        page = QWidget()
        page.setObjectName("pageBody")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 4, 0)
        layout.setSpacing(12)
        layout.addWidget(_label(title, "pageTitle"))
        layout.addWidget(_label(subtitle, "muted", wrap=True))
        return page, layout

    # -- tool status ------------------------------------------------------

    def _tools(self):
        return service.resolve_tools(
            ffprobe=_tool_path(self.ffprobe_row.path(), "ffprobe"),
            exiftool=_tool_path(self.exiftool_row.path(), "exiftool"),
        )

    def _refresh_tools(self) -> None:
        tools = self._tools()
        self.ffprobe_pill.setText("FFmpeg ready" if tools.has_ffprobe
                                  else "FFmpeg missing")
        self.ffprobe_pill.setProperty("state", "ok" if tools.has_ffprobe else "bad")
        self.exiftool_pill.setText("ExifTool ready" if tools.has_exiftool
                                   else "ExifTool (optional)")
        self.exiftool_pill.setProperty("state", "ok" if tools.has_exiftool else "off")
        self.ffprobe_pill.setToolTip(tools.ffprobe or "ffprobe not found")
        self.exiftool_pill.setToolTip(
            tools.exiftool or "Optional — adds richer camera, lens and GPS details")
        for pill in (self.ffprobe_pill, self.exiftool_pill):
            _repolish(pill)
        self.banner.setVisible(not tools.has_ffprobe)
        self.tools_status.setText(
            f"ffprobe: {tools.ffprobe or 'not found'}\n"
            f"exiftool: {tools.exiftool or 'not found (optional)'}")

    # -- catalog page -----------------------------------------------------

    def _build_catalog_page(self) -> QWidget:
        page, layout = self._page(
            "Catalog",
            "Write a Word document for every recording and keep a running master "
            "catalog (.docx + .xlsx). Re-running updates entries instead of "
            "duplicating them.")

        sources = Card("Source folders", step=1)
        self.cat_folders = FolderList(height=130)
        sources.body.addWidget(_folders_widget(self.cat_folders))
        layout.addWidget(sources)

        output = Card("Output folder", "Optional — defaults to a MetaScraper_Catalog "
                      "folder beside the first source.", step=2)
        self.cat_output = PathRow("Choose where the catalog is written", openable=True)
        output.body.addWidget(self.cat_output)
        layout.addWidget(output)

        options = Card("Options", step=3)
        row = QHBoxLayout()
        self.cat_recursive = QCheckBox("Include subfolders")
        self.cat_per_file = QCheckBox("Per-file documents")
        self.cat_master = QCheckBox("Master catalog (.docx + .xlsx)")
        for box in (self.cat_recursive, self.cat_per_file, self.cat_master):
            box.setChecked(True)
            row.addWidget(box)
        row.addStretch(1)
        options.body.addLayout(row)
        layout.addWidget(options)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self.cat_run = _button("Run catalog", "primary")
        self.cat_run.clicked.connect(self._run_catalog)
        actions.addWidget(self.cat_run)
        layout.addLayout(actions)
        layout.addStretch(1)
        return page

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
        self._start(
            lambda report, stop: service.run_catalog(opts, tools, report,
                                                     should_stop=stop),
            self._catalog_done, "Cataloging…")

    def _catalog_done(self, result) -> None:
        if result.no_media:
            self._warn("No media files were found in the chosen folders.")
            return
        verb = "cancelled after" if result.cancelled else "complete:"
        self._log(f"Catalog {verb} {result.processed} file(s) processed.")
        if result.summary:
            self._log(f"  Master catalog: {result.new_count} new, "
                      f"{result.updated_count} updated, "
                      f"{result.summary['count']} total.")
        for output in result.master_outputs:
            self._log(f"  → {output}")
        self._log_failures(result.failures)
        if not result.cancelled:
            self._offer_open(result.output_dir, "Catalog finished")

    # -- organize page ----------------------------------------------------

    def _build_organize_page(self) -> QWidget:
        page, layout = self._page(
            "Organize into projects",
            "Tick the project(s) each recording belongs to, then copy. Every project "
            "gets its own Video|Audio / Camera / Date tree. Originals are never changed.")

        columns = QHBoxLayout()
        columns.setSpacing(12)
        left = QVBoxLayout()
        left.setSpacing(12)

        sources = Card("Source folders", step=1)
        self.org_folders = FolderList(height=80)
        self.org_recursive = QCheckBox("Subfolders")
        self.org_recursive.setToolTip("Include subfolders")
        self.org_recursive.setChecked(True)
        sources.header.addWidget(self.org_recursive)
        sources.body.addWidget(_folders_widget(self.org_folders))
        left.addWidget(sources)

        library = Card("Library", step=2)
        library.setToolTip("Projects are folders here: "
                           "Library / Project / Video|Audio / Camera / Date")
        self.org_dest = PathRow("Choose the library folder", openable=True,
                                stacked=True)
        self.org_dest.changed.connect(self._library_changed)
        library.body.addWidget(self.org_dest)
        left.addWidget(library)

        left.addWidget(self._build_projects_card(), 1)
        left_box = QWidget()
        left_box.setFixedWidth(300)
        left.setContentsMargins(0, 0, 0, 0)
        left_box.setLayout(left)
        columns.addWidget(left_box)
        columns.addWidget(self._build_assign_card(), 1)
        layout.addLayout(columns, 1)

        actions = QHBoxLayout()
        self.org_checksum = QCheckBox("Verify each copy with a checksum (safer, slower)")
        self.org_checksum.setChecked(True)
        actions.addWidget(self.org_checksum)
        actions.addStretch(1)
        self.org_preview = _button("Preview")
        self.org_preview.setToolTip("List where every copy would go, without copying")
        self.org_preview.clicked.connect(lambda: self._run_organize(dry=True))
        self.org_run = _button("Copy to projects", "primary")
        self.org_run.clicked.connect(lambda: self._run_organize(dry=False))
        actions.addWidget(self.org_preview)
        actions.addWidget(self.org_run)
        layout.addLayout(actions)

        self.org_folders.changed.connect(self._sources_changed)
        return page

    def _build_projects_card(self) -> QWidget:
        card = Card("Projects", step=3)
        card.setToolTip("Saved between sessions. Project folders already in the "
                        "library are added automatically.")
        self.proj_list = QListWidget()
        self.proj_list.setMinimumHeight(70)
        self.proj_list.setSelectionMode(QListWidget.SingleSelection)
        card.body.addWidget(self.proj_list, 1)
        row = QHBoxLayout()
        new = _button("New project…")
        new.clicked.connect(self._new_project)
        self.proj_remove = _button("Remove")
        self.proj_remove.clicked.connect(self._remove_project)
        row.addWidget(new, 1)
        row.addWidget(self.proj_remove)
        card.body.addLayout(row)
        return card

    def _build_assign_card(self) -> QWidget:
        card = Card("Assign files", step=4)
        self.org_scan = _button("Scan files", "primary")
        self.org_scan.clicked.connect(self._scan)
        card.header.addWidget(self.org_scan)

        tools = QHBoxLayout()
        self.org_filter = QLineEdit()
        self.org_filter.setPlaceholderText("Filter by name, camera, date…")
        self.org_filter.setClearButtonEnabled(True)
        tools.addWidget(self.org_filter, 1)
        self.org_add_to = _button("Add selected to")
        self.org_remove_from = _button("Remove selected from")
        for button, on in ((self.org_add_to, True), (self.org_remove_from, False)):
            menu = QMenu(button)
            menu.aboutToShow.connect(
                lambda m=menu, v=on: self._fill_project_menu(m, v))
            button.setMenu(menu)
            tools.addWidget(button)
        card.body.addLayout(tools)

        self.org_model = AssignmentModel()
        self.org_model.assignmentsChanged.connect(self._assignments_changed)
        self.org_proxy = QSortFilterProxyModel(self)
        self.org_proxy.setSourceModel(self.org_model)
        self.org_proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.org_proxy.setFilterKeyColumn(-1)
        self.org_proxy.setSortRole(AssignmentModel.SORT_ROLE)
        self.org_proxy.setDynamicSortFilter(False)  # don't jump rows while ticking
        self.org_filter.textChanged.connect(self.org_proxy.setFilterFixedString)

        self.org_table = AssignmentTable()
        self.org_table.setModel(self.org_proxy)
        self.org_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.org_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.org_table.setAlternatingRowColors(True)
        self.org_table.setSortingEnabled(True)
        self.org_table.setShowGrid(False)
        self.org_table.setWordWrap(False)
        self.org_table.verticalHeader().setVisible(False)
        self.org_table.verticalHeader().setDefaultSectionSize(30)
        self.org_table.setMinimumHeight(160)
        self.org_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.org_table.customContextMenuRequested.connect(self._table_menu)
        self._check_delegate = CheckDelegate(self.org_table)
        self._plain_delegate = QStyledItemDelegate(self.org_table)
        self.org_model.modelReset.connect(self._configure_columns)
        card.body.addWidget(self.org_table, 1)

        footer = QHBoxLayout()
        self.org_summary = _label("", "muted")
        footer.addWidget(self.org_summary, 1)
        self.org_hint = _label("Source folders changed — click Scan files to refresh "
                               "the list.", "hint")
        self.org_hint.setVisible(False)
        footer.addWidget(self.org_hint)
        card.body.addLayout(footer)
        return card

    def _configure_columns(self) -> None:
        header = self.org_table.horizontalHeader()
        header.setMinimumSectionSize(56)
        header.setStretchLastSection(True)
        header.setSectionResizeMode(0, QHeaderView.Interactive)
        header.resizeSection(0, 190)
        projects = self.org_model.project_columns()
        for column in range(1, self.org_model.columnCount()):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
            self.org_table.setItemDelegateForColumn(
                column, self._check_delegate if column in projects else self._plain_delegate)

    # projects ---------------------------------------------------------

    def _set_projects(self, projects: List[str], save: bool = True) -> None:
        self._projects = list(projects)
        self.org_model.set_projects(self._projects)
        self._refresh_project_list()
        if save:
            self.settings.setValue("projects", self._projects)

    def _merge_projects(self, names: List[str]) -> None:
        known = {p.lower() for p in self._projects}
        added = [n for n in names if n.lower() not in known
                 and organizer.project_name_problem(n) is None]
        if added:
            self._set_projects(self._projects + added)

    def _refresh_project_list(self) -> None:
        selected = self._selected_project()
        self.proj_list.clear()
        for name in self._projects:
            count = self.org_model.count_for(name)
            item = QListWidgetItem(f"{name}   ({count})" if count else name)
            item.setData(Qt.UserRole, name)
            item.setToolTip(f"Copies go into  Library / {organizer.project_folder(name)}")
            self.proj_list.addItem(item)
            if name == selected:
                item.setSelected(True)
        self.proj_remove.setEnabled(bool(self._projects))

    def _selected_project(self) -> Optional[str]:
        items = self.proj_list.selectedItems()
        return items[0].data(Qt.UserRole) if items else None

    def _new_project(self) -> None:
        name, ok = QInputDialog.getText(self, "New project", "Project name:")
        if not ok:
            return
        name = name.strip()
        problem = organizer.project_name_problem(name)
        if problem is None and name.lower() in (p.lower() for p in self._projects):
            problem = f"There's already a project called '{name}'."
        if problem:
            self._warn(problem)
            return
        self._set_projects(self._projects + [name])

    def _remove_project(self) -> None:
        name = self._selected_project()
        if name is None:
            self._warn("Select a project in the list first.")
            return
        count = self.org_model.count_for(name)
        detail = (f"\n\n{count} file(s) ticked for it will be unticked." if count else "")
        answer = QMessageBox.question(
            self, "Remove project?",
            f"Remove '{name}' from the list?{detail}\n\nNothing on disk is changed. "
            "If its folder exists in the library it will be listed again next time.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer == QMessageBox.Yes:
            self._set_projects([p for p in self._projects if p != name])

    def _fill_project_menu(self, menu: QMenu, on: bool) -> None:
        menu.clear()
        if not self._projects:
            menu.addAction("No projects yet — create one first").setEnabled(False)
            return
        for name in self._projects:
            action = menu.addAction(name)
            action.triggered.connect(
                lambda _checked=False, n=name, v=on: self._assign_selected(n, v))

    def _selected_rows(self) -> List[int]:
        return sorted({self.org_proxy.mapToSource(index).row()
                       for index in self.org_table.selectionModel().selectedRows()})

    def _assign_selected(self, project: str, on: bool) -> None:
        rows = self._selected_rows()
        if not rows:
            self._warn("Select one or more files in the table first "
                       "(Ctrl+A selects all visible files).")
            return
        self.org_model.set_rows(rows, project, on)
        self.org_table.viewport().update()

    def _table_menu(self, pos) -> None:
        menu = QMenu(self)
        add = menu.addMenu("Add selected to")
        self._fill_project_menu(add, True)
        remove = menu.addMenu("Remove selected from")
        self._fill_project_menu(remove, False)
        menu.addSeparator()
        index = self.org_table.indexAt(pos)
        if index.isValid():
            path = self.org_model.infos()[self.org_proxy.mapToSource(index).row()].path
            reveal = menu.addAction("Open containing folder")
            reveal.triggered.connect(lambda: QDesktopServices.openUrl(
                QUrl.fromLocalFile(os.path.dirname(path))))
        menu.exec(self.org_table.viewport().mapToGlobal(pos))

    def _assignments_changed(self) -> None:
        self._refresh_project_list()
        self._update_org_summary()
        self._refresh_buttons()

    def _update_org_summary(self) -> None:
        files, assigned, copies = self.org_model.stats()
        if not files:
            text = "No files scanned yet."
        elif not self._projects:
            text = f"{files} file(s) · create a project to start assigning."
        else:
            text = (f"{files} file(s) · {assigned} assigned · {copies} "
                    f"cop{'y' if copies == 1 else 'ies'} to make")
            if files - assigned:
                text += f" · {files - assigned} not assigned (skipped)"
        self.org_summary.setText(text)

    # library + scanning ----------------------------------------------

    def _library_changed(self, path: str) -> None:
        path = (path or "").strip()
        self.settings.setValue("organize/library", path)
        if path and os.path.isdir(path):
            self._merge_projects(service.list_projects(path))
        self.org_model.set_existing(service.copied_projects(path))

    def _sources_changed(self) -> None:
        self.settings.setValue("organize/sources", self.org_folders.paths())
        self.org_hint.setVisible(self.org_model.rowCount() > 0)

    def _scan(self) -> None:
        folders = self.org_folders.paths()
        if not folders:
            self._warn("Add at least one source folder first.")
            return
        opts = ScanOptions(folders=folders, recursive=self.org_recursive.isChecked())
        library = service.organize_dest(
            OrganizeOptions(folders=folders, dest=self.org_dest.path()))
        tools = self._tools()
        self._start(
            lambda report, stop: service.scan_media(
                opts, tools, report, skip_dirs=[library], should_stop=stop),
            self._scan_done, "Scanning…")

    def _scan_done(self, result) -> None:
        self.org_hint.setVisible(False)
        if result.no_media:
            self.org_model.set_infos([])
            self._warn("No media files were found in the chosen folders.")
            return
        self.org_model.set_infos(result.infos)
        self.org_model.set_existing(service.copied_projects(self.org_dest.path()))
        self.org_table.sortByColumn(0, Qt.AscendingOrder)
        state = "stopped early" if result.cancelled else "done"
        self._log(f"Scan {state}: {len(result.infos)} file(s) listed.")
        self._log_failures(result.failures)
        if not self._projects:
            self._log("Next: create a project, then tick it for the files that "
                      "belong to it.")

    def _run_organize(self, dry: bool) -> None:
        if self.org_model.rowCount() == 0:
            self._warn("Scan your source folders first.")
            return
        assignments = self.org_model.assignments()
        if not assignments:
            self._warn("Tick at least one project for the files you want to copy.")
            return
        dest = self.org_dest.path()
        if not dest:
            self._warn("Choose a library folder first.")
            return
        opts = OrganizeOptions(
            folders=self.org_folders.paths(), dest=dest, dry_run=dry,
            checksum=self.org_checksum.isChecked(), projects=assignments,
        )
        infos = self.org_model.infos()
        tools = self._tools()
        self._start(
            lambda report, stop: service.run_organize(
                opts, tools, report, infos=infos, should_stop=stop),
            self._organize_done, "Planning…" if dry else "Copying…")

    def _organize_done(self, outcome) -> None:
        if outcome.no_media:
            self._warn("Nothing to copy — the listed files are already inside "
                       "the library.")
            return
        if not self.fin_dest.path():
            self.fin_dest.set_path(outcome.dest_root)
        per_project = Counter(move.project for move in outcome.plan)
        breakdown = ", ".join(f"{name}: {n}" for name, n in per_project.items())
        if outcome.dry_run:
            self._log(f"Preview: {len(outcome.plan)} cop"
                      f"{'y' if len(outcome.plan) == 1 else 'ies'} into "
                      f"{outcome.dest_root}  ({breakdown})")
            for move in outcome.plan[:200]:
                marker = "  =" if move.action == "skip-identical" else "  →"
                self._log(f"{marker} {os.path.relpath(move.dest, outcome.dest_root)}")
            if len(outcome.plan) > 200:
                self._log(f"    … and {len(outcome.plan) - 200} more")
            if outcome.unassigned:
                self._log(f"  {outcome.unassigned} file(s) have no project and "
                          "will be skipped.")
            self._log("Nothing was copied.")
            return
        result = outcome.result
        self.org_model.set_existing(service.copied_projects(outcome.dest_root))
        verb = "cancelled" if outcome.cancelled else "complete"
        self._log(f"Organize {verb}: {result.copied} copied, "
                  f"{result.skipped} already in place"
                  + (f", {result.renamed} renamed to avoid a clash"
                     if result.renamed else "")
                  + f".  ({breakdown})")
        self._log_failures(result.failures)
        self._log("Originals are untouched — use Finalize to remove them once "
                  "you've checked the copies.")
        if not outcome.cancelled:
            self._offer_open(outcome.dest_root, "Organize finished")

    # -- finalize page ----------------------------------------------------

    def _build_finalize_page(self) -> QWidget:
        page, layout = self._page(
            "Finalize",
            "The 'move later' step: delete the original files whose organized copies "
            "check out. A recording copied into several projects is only deleted "
            "once every copy verifies. Always preview first.")

        library = Card("Library", step=1)
        self.fin_dest = PathRow("The organized library folder", openable=True)
        library.body.addWidget(self.fin_dest)
        layout.addWidget(library)

        options = Card("Options", step=2)
        self.fin_checksum = QCheckBox("Re-verify with a checksum before deleting")
        self.fin_checksum.setChecked(True)
        options.body.addWidget(self.fin_checksum)
        layout.addWidget(options)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self.fin_preview = _button("Preview")
        self.fin_preview.setToolTip("Show what would be deleted, without deleting")
        self.fin_preview.clicked.connect(lambda: self._run_finalize(apply=False))
        self.fin_delete = _button("Delete originals", "danger")
        self.fin_delete.clicked.connect(lambda: self._run_finalize(apply=True))
        actions.addWidget(self.fin_preview)
        actions.addWidget(self.fin_delete)
        layout.addLayout(actions)
        layout.addStretch(1)
        return page

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
        self._start(
            lambda report, stop: service.run_finalize(opts, report, should_stop=stop),
            self._finalize_done, "Deleting…" if apply else "Checking copies…")

    def _finalize_done(self, outcome) -> None:
        if outcome.missing_manifest:
            self._warn("No organize manifest found in that folder.\n"
                       "Run Organize first, then finalize the same library.")
            return
        if outcome.pending == 0:
            self._log("Nothing to finalize — no copied originals are awaiting deletion.")
            return
        result = outcome.result
        if outcome.preview:
            self._log(f"Preview: would delete {result.deleted} original(s), "
                      f"freeing {human_file_size(result.freed_bytes)}. "
                      "Nothing was deleted.")
        else:
            self._log(f"Deleted {result.deleted} original(s), "
                      f"freed {human_file_size(result.freed_bytes)}.")
        if result.cancelled:
            self._log("  Stopped early at your request.")
        if result.problems:
            self._log(f"  {len(result.problems)} original(s) kept because a copy "
                      "didn't verify:")
            self._log_failures(result.problems, header=False)

    # -- settings page ----------------------------------------------------

    def _build_settings_page(self) -> QWidget:
        page, layout = self._page("Settings", "Tool locations and appearance.")

        tools = Card("Tools", "Leave blank to find them automatically. You can point "
                     "at the program itself or the folder it's in.")
        tools.body.addWidget(_label("FFmpeg — ffprobe (required for full metadata)"))
        self.ffprobe_row = PathRow("e.g. C:\\ffmpeg\\bin\\ffprobe.exe", pick_file=True)
        tools.body.addWidget(self.ffprobe_row)
        tools.body.addWidget(_label("ExifTool (optional)"))
        self.exiftool_row = PathRow("e.g. C:\\exiftool\\exiftool.exe", pick_file=True)
        tools.body.addWidget(self.exiftool_row)
        self.tools_status = _label("", "muted", wrap=True)
        self.tools_status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        tools.body.addWidget(self.tools_status)
        for row, key in ((self.ffprobe_row, "tools/ffprobe"),
                         (self.exiftool_row, "tools/exiftool")):
            row.changed.connect(
                lambda text, k=key: (self.settings.setValue(k, text.strip()),
                                     self._refresh_tools()))
        layout.addWidget(tools)

        appearance = Card("Appearance")
        row = QHBoxLayout()
        row.addWidget(_label("Theme"))
        self.theme_combo = QComboBox()
        for mode in theme.MODES:
            self.theme_combo.addItem(theme.MODE_LABELS[mode], mode)
        self.theme_combo.currentIndexChanged.connect(self._theme_changed)
        row.addWidget(self.theme_combo)
        row.addStretch(1)
        appearance.body.addLayout(row)
        layout.addWidget(appearance)
        layout.addStretch(1)
        return page

    def _theme_mode(self) -> str:
        return self.theme_combo.currentData() or "system"

    def _theme_changed(self) -> None:
        self.settings.setValue("appearance/theme", self._theme_mode())
        app = QApplication.instance()
        if app is not None:
            theme.apply(app, self._theme_mode())

    def _system_theme_changed(self, *_args) -> None:
        if self._theme_mode() == "system":
            theme.apply(QApplication.instance(), "system")

    # -- settings persistence ---------------------------------------------

    def _load_settings(self) -> None:
        s = self.settings
        mode = s.value("appearance/theme", "system") or "system"
        index = self.theme_combo.findData(mode)
        self.theme_combo.blockSignals(True)
        self.theme_combo.setCurrentIndex(max(index, 0))
        self.theme_combo.blockSignals(False)
        app = QApplication.instance()
        if app is not None:
            theme.apply(app, self._theme_mode())
            hints = app.styleHints()
            if hasattr(hints, "colorSchemeChanged"):
                hints.colorSchemeChanged.connect(self._system_theme_changed)

        self.ffprobe_row.set_path_quietly(s.value("tools/ffprobe", "") or "")
        self.exiftool_row.set_path_quietly(s.value("tools/exiftool", "") or "")
        self.cat_output.set_path_quietly(s.value("catalog/output", "") or "")
        self.cat_folders.set_paths(_settings_list(s, "catalog/sources"))
        self.org_folders.set_paths(_settings_list(s, "organize/sources"))
        self.fin_dest.set_path_quietly(s.value("finalize/library", "") or "")

        self._set_projects(_settings_list(s, "projects"), save=False)
        library = s.value("organize/library", "") or ""
        self.org_dest.set_path_quietly(library)
        self._library_changed(self.org_dest.path() or "")
        self.org_hint.setVisible(False)

        geometry = s.value("window/geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        else:
            self._default_size()

    def _default_size(self) -> None:
        screen = QApplication.primaryScreen()
        width, height = 1180, 800
        if screen is not None:
            available = screen.availableGeometry()
            width = min(width, int(available.width() * 0.92))
            height = min(height, int(available.height() * 0.92))
        self.resize(width, height)

    def _save_settings(self) -> None:
        s = self.settings
        s.setValue("window/geometry", self.saveGeometry())
        s.setValue("window/page", self.nav.currentRow())
        s.setValue("catalog/sources", self.cat_folders.paths())
        s.setValue("catalog/output", self.cat_output.path() or "")
        s.setValue("organize/sources", self.org_folders.paths())
        s.setValue("organize/library", self.org_dest.path() or "")
        s.setValue("finalize/library", self.fin_dest.path() or "")
        s.setValue("tools/ffprobe", self.ffprobe_row.path() or "")
        s.setValue("tools/exiftool", self.exiftool_row.path() or "")
        s.setValue("projects", self._projects)
        s.sync()

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._running:
            answer = QMessageBox.question(
                self, "Task still running",
                "A task is still running. Stop it and close MetaScraper?\n\n"
                "It stops after the file it's working on, so nothing is left "
                "half-written.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer == QMessageBox.Yes:
                self._close_when_done = True
                self._cancel()
            event.ignore()
            return
        self._save_settings()
        super().closeEvent(event)

    # -- worker plumbing --------------------------------------------------

    def _start(self, job, on_result, status: str) -> None:
        if self._thread is not None:
            self._warn("A task is already running — please wait for it to finish.")
            return
        self._save_settings()
        self._on_result = on_result
        self._set_running(True)
        self.progress.setRange(0, 0)  # busy until the first counted event
        self.status.setText(status)
        self.log.clear()

        self._thread = QThread(self)
        self._worker = Worker(job)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        # Connect to methods of this window (which lives on the UI thread) so
        # Qt queues the calls onto the UI thread. A lambda here would run on
        # the worker thread instead.
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.failed.connect(self._on_worker_failed)
        self._thread.start()

    def _cancel(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self.cancel_button.setEnabled(False)
            self.status.setText("Stopping after the current file…")

    @Slot(object)
    def _on_progress(self, event: Event) -> None:
        if event.kind == "item" and event.current and event.total:
            self.progress.setRange(0, event.total)
            self.progress.setValue(event.current)
            metrics = self.status.fontMetrics()
            text = f"{event.current} of {event.total}   {event.message}"
            self.status.setText(metrics.elidedText(
                text, Qt.ElideMiddle, max(self.status.width(), 200)))
        elif event.kind == "phase" and event.total:
            self.progress.setRange(0, event.total)
            self.progress.setValue(0)
            self._log(event.message)
        elif event.message:
            self._log(event.message if event.kind != "item" else f"    {event.message}")

    @Slot(object)
    def _on_worker_finished(self, result) -> None:
        self._teardown_thread()
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.status.setText("Done")
        on_result, self._on_result = self._on_result, None
        if self._close_when_done:
            self.close()
            return
        try:
            if on_result is not None:
                on_result(result)
        except Exception as exc:  # noqa: BLE001
            self._warn(f"Finished, but displaying the result failed: {exc}")

    @Slot(str)
    def _on_worker_failed(self, message: str) -> None:
        self._teardown_thread()
        self._on_result = None
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.status.setText("Failed")
        self._log(f"Failed: {message}")
        if self._close_when_done:
            self.close()
            return
        self._warn(f"The task failed:\n{message}")

    def _teardown_thread(self) -> None:
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait()
            self._thread.deleteLater()
        # The worker has no parent, so dropping the reference frees it; its
        # thread has stopped, so deleteLater() would never run.
        self._thread = None
        self._worker = None
        self._set_running(False)

    def _set_running(self, running: bool) -> None:
        self._running = running
        self.cancel_button.setVisible(running)
        self.cancel_button.setEnabled(running)
        self._refresh_buttons()

    def _refresh_buttons(self) -> None:
        idle = not self._running
        _files, _assigned, copies = self.org_model.stats()
        for button in (self.cat_run, self.org_scan, self.fin_preview, self.fin_delete):
            button.setEnabled(idle)
        for button in (self.org_preview, self.org_run):
            button.setEnabled(idle and copies > 0)

    # -- small helpers ----------------------------------------------------

    def _log(self, message: str) -> None:
        self.log.appendPlainText(message)

    def _log_failures(self, failures: List[str], header: bool = True) -> None:
        if not failures:
            return
        if header:
            self._log(f"  {len(failures)} item(s) had problems:")
        for failure in failures[:50]:
            self._log(f"    - {failure}")
        if len(failures) > 50:
            self._log(f"    … and {len(failures) - 50} more")

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
    app.setOrganizationName(APP_NAME)
    font = app.font()
    if font.pointSizeF() < 10:
        font.setPointSizeF(10)
        app.setFont(font)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
