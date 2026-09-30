"""Light and dark themes for the desktop app.

By default the theme follows the operating system's light/dark setting and
switches live when it changes; it can also be pinned to light or dark. Colors
are defined once as tokens and used for both the Qt palette (which Fusion uses
for checkboxes, scrollbars and the like) and the stylesheet.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPalette, QPen, QPixmap
from PySide6.QtWidgets import QApplication

MODES = ("system", "light", "dark")
MODE_LABELS = {"system": "Follow system", "light": "Light", "dark": "Dark"}


@dataclass(frozen=True)
class Tokens:
    dark: bool
    window: str
    surface: str
    sidebar: str
    field: str
    border: str
    border_strong: str
    text: str
    muted: str
    accent: str
    accent_hover: str
    accent_text: str
    accent_soft: str
    danger: str
    danger_hover: str
    ok: str
    ok_bg: str
    err: str
    err_bg: str
    selection: str
    alt_row: str
    header_bg: str


LIGHT = Tokens(
    dark=False,
    window="#F4F5F7", surface="#FFFFFF", sidebar="#ECEEF2", field="#FFFFFF",
    border="#E1E4EA", border_strong="#C9CED8",
    text="#1D2430", muted="#667085",
    accent="#2563EB", accent_hover="#1D4ED8", accent_text="#FFFFFF",
    accent_soft="#E3EBFD",
    danger="#D92D20", danger_hover="#B42318",
    ok="#157F3D", ok_bg="#E4F5EA", err="#B42318", err_bg="#FDECEA",
    selection="#D6E3FD", alt_row="#F8F9FB", header_bg="#F1F3F6",
)

DARK = Tokens(
    dark=True,
    window="#15171C", surface="#1E2128", sidebar="#191B21", field="#23262E",
    border="#2D313A", border_strong="#3C414C",
    text="#E6E8EC", muted="#9AA3B2",
    accent="#5B8DEF", accent_hover="#7AA3F3", accent_text="#FFFFFF",
    accent_soft="#243352",
    danger="#E5484D", danger_hover="#F2555A",
    ok="#4CC38A", ok_bg="#16301F", err="#F2767A", err_bg="#3A1D1F",
    selection="#2B4270", alt_row="#20232A", header_bg="#252830",
)


def system_is_dark(app: QApplication) -> bool:
    """True when the OS reports a dark color scheme (Qt 6.5+)."""
    hints = app.styleHints()
    scheme = getattr(hints, "colorScheme", None)
    if scheme is None:
        return False
    return scheme() == Qt.ColorScheme.Dark


def resolve(mode: str, app: QApplication) -> Tokens:
    if mode == "dark":
        return DARK
    if mode == "light":
        return LIGHT
    return DARK if system_is_dark(app) else LIGHT


def palette_for(t: Tokens) -> QPalette:
    p = QPalette()
    c = QColor
    roles = {
        QPalette.Window: t.window,
        QPalette.WindowText: t.text,
        QPalette.Base: t.field,
        QPalette.AlternateBase: t.alt_row,
        QPalette.Text: t.text,
        QPalette.Button: t.surface,
        QPalette.ButtonText: t.text,
        QPalette.BrightText: t.accent_text,
        QPalette.Highlight: t.accent,
        QPalette.HighlightedText: t.accent_text,
        QPalette.PlaceholderText: t.muted,
        QPalette.ToolTipBase: t.surface,
        QPalette.ToolTipText: t.text,
        QPalette.Link: t.accent,
        QPalette.Light: t.surface,
        QPalette.Midlight: t.border,
        QPalette.Mid: t.border_strong,
        QPalette.Dark: t.border_strong,
        QPalette.Shadow: "#000000" if t.dark else "#9AA0AA",
    }
    for role, value in roles.items():
        p.setColor(role, c(value))
    for role in (QPalette.Text, QPalette.WindowText, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, c(t.muted))
    return p


def _checkmark_image(color: str) -> str:
    """Write a checkmark PNG (plus @2x/@3x for high-DPI screens) and return the
    base path in the form a stylesheet url() expects."""
    folder = os.path.join(tempfile.gettempdir(), "metascraper-theme")
    os.makedirs(folder, exist_ok=True)
    base = os.path.join(folder, f"check-{color.lstrip('#').lower()}")
    for scale, suffix in ((1, ""), (2, "@2x"), (3, "@3x")):
        path = f"{base}{suffix}.png"
        if os.path.exists(path):
            continue
        size = 16 * scale
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor(color), 2.2 * scale)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        tick = QPainterPath(QPointF(3.8 * scale, 8.4 * scale))
        tick.lineTo(6.8 * scale, 11.3 * scale)
        tick.lineTo(12.2 * scale, 5.0 * scale)
        painter.drawPath(tick)
        painter.end()
        pixmap.save(path, "PNG")
    return (base + ".png").replace("\\", "/")


def _indicator_rules(t: Tokens) -> str:
    try:
        check = _checkmark_image(t.accent_text)
    except OSError:
        check = ""
    # Quoted, since the temp folder can contain spaces (C:/Users/Jo Smith/...).
    image = f'image: url("{check}");' if check else ""
    return f"""
QCheckBox::indicator, QTableView::indicator {{
    width: 14px; height: 14px; border-radius: 4px;
    border: 1px solid {t.muted}; background: {t.field};
}}
QCheckBox::indicator:hover, QTableView::indicator:hover {{ border-color: {t.accent}; }}
QCheckBox::indicator:checked, QTableView::indicator:checked {{
    background: {t.accent}; border-color: {t.accent}; {image}
}}
QCheckBox::indicator:disabled {{ background: {t.border}; border-color: {t.border}; }}
QCheckBox {{ spacing: 7px; }}
"""


def stylesheet(t: Tokens) -> str:
    return _indicator_rules(t) + f"""
QMainWindow, QWidget#root {{ background: {t.window}; }}
QWidget {{ color: {t.text}; }}
QWidget#pageBody {{ background: transparent; }}
QScrollArea {{ border: none; background: transparent; }}

QFrame#header {{ background: {t.surface}; border: none; border-bottom: 1px solid {t.border}; }}
QLabel#appTitle {{ font-size: 15pt; font-weight: 600; }}
QLabel#pageTitle {{ font-size: 17pt; font-weight: 600; }}
QLabel#muted {{ color: {t.muted}; }}
QLabel#hint {{ color: {t.err}; }}

QListWidget#nav {{
    background: {t.sidebar}; border: none; border-right: 1px solid {t.border};
    padding: 12px 8px; outline: 0;
}}
QListWidget#nav::item {{
    padding: 9px 12px; margin: 2px 0; border-radius: 6px; color: {t.muted};
}}
QListWidget#nav::item:hover {{ background: {t.accent_soft}; color: {t.text}; }}
QListWidget#nav::item:selected {{
    background: {t.accent_soft}; color: {t.accent}; font-weight: 600;
}}

QFrame#card {{
    background: {t.surface}; border: 1px solid {t.border}; border-radius: 10px;
}}
QLabel#cardTitle {{ font-size: 11pt; font-weight: 600; }}
QLabel#step {{
    background: {t.accent_soft}; color: {t.accent}; border-radius: 10px;
    font-weight: 700; min-width: 20px; max-width: 20px;
    min-height: 20px; max-height: 20px; qproperty-alignment: AlignCenter;
}}

QPushButton {{
    background: {t.surface}; border: 1px solid {t.border_strong};
    border-radius: 6px; padding: 6px 14px;
}}
QPushButton:hover {{ border-color: {t.accent}; }}
QPushButton:pressed {{ background: {t.accent_soft}; }}
QPushButton:disabled {{ color: {t.muted}; border-color: {t.border}; }}
QPushButton::menu-indicator {{ subcontrol-position: right center; right: 6px; }}
QPushButton[variant="primary"] {{
    background: {t.accent}; color: {t.accent_text}; border: 1px solid {t.accent};
    font-weight: 600; padding: 8px 20px;
}}
QPushButton[variant="primary"]:hover {{
    background: {t.accent_hover}; border-color: {t.accent_hover};
}}
QPushButton[variant="primary"]:disabled {{
    background: {t.border}; border-color: {t.border}; color: {t.muted};
}}
QPushButton[variant="danger"] {{
    background: {t.danger}; color: #FFFFFF; border: 1px solid {t.danger};
    font-weight: 600; padding: 8px 20px;
}}
QPushButton[variant="danger"]:hover {{
    background: {t.danger_hover}; border-color: {t.danger_hover};
}}
QPushButton[variant="danger"]:disabled {{
    background: {t.border}; border-color: {t.border}; color: {t.muted};
}}
QPushButton[variant="link"] {{
    background: transparent; border: none; color: {t.accent};
    padding: 2px 4px; font-weight: 600;
}}
QPushButton[variant="link"]:hover {{ text-decoration: underline; }}

QLineEdit, QPlainTextEdit, QListWidget, QTableView {{
    background: {t.field}; border: 1px solid {t.border_strong}; border-radius: 6px;
    selection-background-color: {t.selection}; selection-color: {t.text};
}}
QLineEdit {{ padding: 6px 8px; }}
QLineEdit:focus, QPlainTextEdit:focus, QListWidget:focus, QTableView:focus {{
    border-color: {t.accent};
}}
QListWidget {{ padding: 4px; }}
QListWidget::item {{ padding: 5px 6px; border-radius: 4px; }}
QListWidget::item:selected {{ background: {t.selection}; color: {t.text}; }}

QTableView {{
    gridline-color: {t.border}; alternate-background-color: {t.alt_row};
}}
QTableView::item {{ padding: 0 6px; }}
QTableView::item:selected {{ background: {t.selection}; color: {t.text}; }}
QHeaderView::section {{
    background: {t.header_bg}; color: {t.muted}; font-weight: 600;
    border: none; border-bottom: 1px solid {t.border}; border-right: 1px solid {t.border};
    padding: 6px 8px;
}}
QTableCornerButton::section {{ background: {t.header_bg}; border: none; }}

QProgressBar {{
    background: {t.field}; border: 1px solid {t.border}; border-radius: 6px;
    text-align: center; min-height: 18px; max-height: 18px;
}}
QProgressBar::chunk {{ background: {t.accent}; border-radius: 5px; }}

QFrame#banner {{
    background: {t.err_bg}; border: 1px solid {t.err}; border-radius: 8px;
}}
QFrame#banner QLabel {{ color: {t.err}; }}
QLabel#pill {{ border-radius: 11px; padding: 3px 11px; font-weight: 600; }}
QLabel#pill[state="ok"] {{ background: {t.ok_bg}; color: {t.ok}; }}
QLabel#pill[state="bad"] {{ background: {t.err_bg}; color: {t.err}; }}
QLabel#pill[state="off"] {{ background: {t.header_bg}; color: {t.muted}; }}

QPlainTextEdit#log {{
    font-family: "Cascadia Mono", Consolas, Menlo, "DejaVu Sans Mono", monospace;
    font-size: 9pt;
}}
QSplitter::handle {{ background: transparent; }}
QToolTip {{ background: {t.surface}; color: {t.text}; border: 1px solid {t.border}; }}
QMenu {{ background: {t.surface}; border: 1px solid {t.border}; padding: 4px; }}
QMenu::item {{ padding: 6px 22px; border-radius: 4px; }}
QMenu::item:selected {{ background: {t.accent_soft}; color: {t.text}; }}
QMenu::item:disabled {{ color: {t.muted}; }}
"""


def apply(app: QApplication, mode: str = "system") -> Tokens:
    """Apply the theme for ``mode`` ("system", "light" or "dark") to the app."""
    tokens = resolve(mode, app)
    sheet = stylesheet(tokens)
    if app.styleSheet() == sheet:
        return tokens  # already applied; re-applying re-styles every widget
    app.setStyle("Fusion")
    app.setPalette(palette_for(tokens))
    app.setStyleSheet(sheet)
    return tokens
