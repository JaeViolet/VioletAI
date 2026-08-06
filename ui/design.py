"""Shared visual design tokens and lightweight vector icons."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap


class Colors:
    BLACK = "#050505"
    SURFACE = "#111111"
    PANEL = "#171717"
    PANEL_HOVER = "#202020"
    PANEL_ACTIVE = "#2a2a2a"
    COMPOSER = "#202020"
    USER_BUBBLE = "#2f2f2f"
    CODE = "#181818"
    CODE_HEADER = "#242424"
    BORDER = "#2f2f2f"
    BORDER_STRONG = "#3d3d3d"
    TEXT = "#f4f4f4"
    TEXT_MUTED = "#a6a6a6"
    TEXT_FAINT = "#747474"
    ACCENT = "#8b5cf6"
    ACCENT_HOVER = "#9b6dff"
    ERROR = "#ffb4ab"
    ERROR_STRONG = "#ff5a4f"
    SETTINGS_NAV = "#0a0a0a"
    SETTINGS_BORDER = "#232323"
    SETTINGS_HOVER = "#1a1a1a"
    SETTINGS_ACTIVE = "#262626"


class Radius:
    SM = 7
    MD = 10
    LG = 16
    XL = 24


class Spacing:
    XS = 4
    SM = 8
    MD = 12
    LG = 16
    XL = 24


class Motion:
    FAST = 110
    NORMAL = 160
    STREAM_INTERVAL = 40


ICON_SIZE = 18
PNG_CONTROL_ICON_SIZE = 21
ICON_ASSETS_DIR = Path(__file__).resolve().parent / "icons"
PNG_ICON_NAMES = {
    "copy": "copy.png",
    "regen": "regenerate.png",
    "regenerate": "regenerate.png",
    "memory": "memory.png",
    "send": "send.png",
    "settings": "settings.png",
    "stop": "stop.png",
    "theme": "theme.png",
}


def asset_icon_path(name: str) -> Path:
    return ICON_ASSETS_DIR / PNG_ICON_NAMES[name]


def png_icon(name: str, size: int = ICON_SIZE) -> QIcon:
    asset_name = PNG_ICON_NAMES.get(name)
    if asset_name is None:
        return QIcon()
    pixmap = QPixmap(str(ICON_ASSETS_DIR / asset_name))
    if pixmap.isNull():
        return QIcon()
    scaled = pixmap.scaled(
        size,
        size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    return QIcon(scaled)


def icon(name: str, color: str = Colors.TEXT, size: int = ICON_SIZE, right_pad: int = 0) -> QIcon:
    if name in PNG_ICON_NAMES:
        return _pad_icon(png_icon(name, size), size, right_pad)

    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    stroke = max(1.45, size * 0.085)
    pen = QPen(QColor(color), stroke)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    rect = QRectF(3, 3, size - 6, size - 6)
    center = QPointF(size / 2, size / 2)

    if name == "new":
        painter.drawLine(center.x(), 4, center.x(), size - 4)
        painter.drawLine(4, center.y(), size - 4, center.y())
    elif name == "search":
        painter.drawEllipse(QRectF(3, 3, 8, 8))
        painter.drawLine(10, 10, 15, 15)
    elif name == "collapse":
        painter.drawLine(11, 4, 5, center.y())
        painter.drawLine(5, center.y(), 11, size - 4)
    elif name == "expand":
        painter.drawLine(7, 4, 13, center.y())
        painter.drawLine(13, center.y(), 7, size - 4)
    elif name == "copy":
        back = QRectF(size * 0.39, size * 0.22, size * 0.42, size * 0.52)
        front = QRectF(size * 0.20, size * 0.38, size * 0.42, size * 0.52)
        radius = size * 0.10
        painter.drawRoundedRect(back, radius, radius)
        painter.drawRoundedRect(front, radius, radius)
    elif name == "regen":
        arc_rect = QRectF(size * 0.20, size * 0.20, size * 0.60, size * 0.60)
        painter.drawArc(arc_rect, 35 * 16, 285 * 16)
        tip = QPointF(size * 0.79, size * 0.31)
        painter.drawLine(tip, QPointF(size * 0.80, size * 0.52))
        painter.drawLine(tip, QPointF(size * 0.60, size * 0.35))
    elif name == "pin":
        painter.drawLine(8, 3, 13, 8)
        painter.drawLine(5, 9, 9, 13)
        painter.drawLine(7, 5, 13, 11)
        painter.drawLine(5, 9, 9, 13)
        painter.drawLine(9, 13, 5, 17)
    elif name == "rename":
        painter.drawLine(4, 14, 14, 4)
        painter.drawLine(12, 4, 14, 6)
        painter.drawLine(4, 14, 3, 16)
    elif name == "delete":
        painter.drawLine(5, 6, 13, 6)
        painter.drawLine(7, 8, 7, 14)
        painter.drawLine(11, 8, 11, 14)
        painter.drawRoundedRect(QRectF(6, 6, 6, 10), 1, 1)
    elif name == "send":
        painter.drawLine(center.x(), size * 0.74, center.x(), size * 0.29)
        painter.drawLine(center.x(), size * 0.29, size * 0.30, size * 0.49)
        painter.drawLine(center.x(), size * 0.29, size * 0.70, size * 0.49)
    elif name == "stop":
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        radius = size * 0.06
        painter.drawRoundedRect(
            QRectF(size * 0.34, size * 0.34, size * 0.32, size * 0.32),
            radius,
            radius,
        )
    elif name == "close":
        painter.drawLine(5, 5, 13, 13)
        painter.drawLine(13, 5, 5, 13)
    else:
        painter.drawEllipse(rect)

    painter.end()
    return _pad_icon(QIcon(pixmap), size, right_pad)


def _pad_icon(source: QIcon, size: int, right_pad: int) -> QIcon:
    if right_pad <= 0:
        return source
    total = size + right_pad
    pixmap = QPixmap(total, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.drawPixmap(0, 0, source.pixmap(size, size))
    painter.end()
    return QIcon(pixmap)
