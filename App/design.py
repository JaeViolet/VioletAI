"""Shared visual design tokens and lightweight vector icons."""

from __future__ import annotations

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


def icon(name: str, color: str = Colors.TEXT, size: int = ICON_SIZE) -> QIcon:
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
    return QIcon(pixmap)


def app_stylesheet() -> str:
    return f"""
        * {{ font-family: "Segoe UI Variable", "Segoe UI"; }}
        QMainWindow, #centralWidget, #mainPanel, #messageContainer {{
            background: {Colors.SURFACE}; color: {Colors.TEXT};
        }}
        #sidebar {{
            background: {Colors.BLACK}; border-right: 1px solid {Colors.BORDER};
        }}
        #sidebarTopTitle {{ color: {Colors.TEXT}; font-size: 22px; font-weight: 1; margin-left: 8px; }}
        #sidebarIconButton, #headerIconButton, #collapsedSidebarButton {{
            background: transparent; border: none; color: {Colors.TEXT_MUTED};
            margin: 6px 4px;
            border-radius: {Radius.SM}px; padding: 5px; min-width: 28px; min-height: 28px;
        }}
        #collapsedSidebarButton {{
            margin: 0; padding: 5px; min-width: 32px; min-height: 32px;
            max-width: 32px; max-height: 32px;
        }}
        #sidebarIconButton:hover, #headerIconButton:hover, #collapsedSidebarButton:hover {{ background: {Colors.PANEL_HOVER}; }}
        QMenu {{
            background: {Colors.COMPOSER}; color: {Colors.TEXT};
            border: 1px solid {Colors.BORDER_STRONG}; border-radius: {Radius.SM}px;
            padding: 6px;
        }}
        QMenu::item {{ padding: 7px 28px 7px 10px; border-radius: 5px; }}
        QMenu::item:selected {{ background: {Colors.PANEL_ACTIVE}; }}
        #sidebarNewChat {{
            background: transparent; color: {Colors.TEXT}; border: none;
            margin-top: 40px;
            border-radius: {Radius.SM}px; padding: 15px 15px; text-align: left;
            font-size: 13px;
        }}
        #sidebarNewChat:hover {{ background: {Colors.PANEL_HOVER}; }}
        #sidebarScroll, #sidebarList, #searchResults {{ background: transparent; border: none; }}
        #conversationGroup {{
            color: {Colors.TEXT_MUTED}; font-size: 11px; font-weight: 600;
            padding: 14px 8px 5px 8px;
        }}
        #conversationRow {{ background: transparent; border-radius: {Radius.MD}px;}}
        #conversationRow:hover {{ background: {Colors.PANEL_HOVER}; }}
        #conversationRow[active="true"] {{ background: {Colors.PANEL_ACTIVE}; }}
        #conversationTitle {{ color: {Colors.TEXT}; font-size: 13px; }}
        #searchOverlay {{
            background: {Colors.COMPOSER}; border: 1px solid {Colors.BORDER_STRONG};
            border-radius: {Radius.LG}px;
        }}
        #overlaySearchInput {{
            background: transparent; color: {Colors.TEXT}; border: none;
            font-size: 18px; padding: 10px 4px;
        }}
        #overlayHint {{ color: {Colors.TEXT_MUTED}; font-size: 12px; }}
        #chatScroll {{ background: {Colors.SURFACE}; border: none; }}
        QScrollBar:vertical {{ background: transparent; width: 8px; margin: 3px; }}
        QScrollBar::handle:vertical {{ background: #4a4a4a; border-radius: 4px; min-height: 36px; }}
        QScrollBar::handle:vertical:hover {{ background: #626262; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
        #welcomeIcon {{ color: {Colors.TEXT}; font-size: 28px; font-weight: 650; }}
        #welcomeTitle {{ color: {Colors.TEXT}; font-size: 25px; font-weight: 620; }}
        #welcomeSubtitle {{ color: {Colors.TEXT_MUTED}; font-size: 13px; }}
        #userBubble {{
            background: {Colors.USER_BUBBLE}; border: 1px solid #3a3a3a;
            border-radius: {Radius.LG}px;
        }}
        #assistantBubble, #errorBubble {{ background: transparent; border: none; }}
        #errorBubble {{ color: {Colors.ERROR}; }}
        #markdownView {{
            background: transparent; color: {Colors.TEXT}; border: none;
            font-size: 15px; selection-background-color: #5b78a6;
        }}
        #thinkingDots {{ color: {Colors.TEXT_MUTED}; font-size: 24px; letter-spacing: 2px; }}
        #codeBlock {{ background: {Colors.CODE}; border: none; border-radius: {Radius.MD}px; }}
        #codeHeader {{ background: {Colors.CODE_HEADER}; border-top-left-radius: {Radius.MD}px; border-top-right-radius: {Radius.MD}px; }}
        #codeLanguage {{ color: {Colors.TEXT_MUTED}; font-size: 11px; font-weight: 600; }}
        #copiedLabel {{ color: {Colors.TEXT_MUTED}; font-size: 11px; padding-right: 4px; }}
        #copyButton, #actionButton {{
            background: transparent; border: none; color: {Colors.TEXT_MUTED};
            padding: 3px; min-width: 24px; min-height: 24px; border-radius: 5px;
        }}
        #copyButton:hover, #actionButton:hover {{ color: white; background: {Colors.PANEL_ACTIVE}; }}
        #codeEditor {{
            background: {Colors.CODE}; color: #e6e6e6; border: none;
            padding: 0; selection-background-color: #425775;
        }}
        #inputPanel {{ background: {Colors.SURFACE}; }}
        #composer {{
            background: {Colors.COMPOSER}; border: 1px solid {Colors.BORDER_STRONG};
            border-radius: 28px;
        }}
        #composerToolbar {{ background: transparent; border: none; }}
        #messageInput {{
            background: transparent; color: {Colors.TEXT}; border: none;
            padding: 0px 3px; font-size: 15px; selection-background-color: #58719a;
        }}
        #messageInput:disabled {{ color: #8b8b8b; }}
        #toolsButton {{
            background: transparent; color: {Colors.TEXT_MUTED}; border: none;
            padding: 4px; min-width: 32px; min-height: 32px; max-width: 32px; max-height: 32px;
            border-radius: 16px;
        }}
        #toolsButton:hover {{ background: {Colors.PANEL_ACTIVE}; color: {Colors.TEXT}; }}
        #toolsButton::menu-indicator {{ image: none; width: 0px; }}
        #modelSelector {{
            background: transparent; color: {Colors.TEXT_MUTED}; border: none;
            padding: 3px 8px 3px 4px; min-height: 24px; max-height: 24px; font-size: 13px;
        }}
        #modelSelector::drop-down {{
            width: 16px; border: none;
        }}
        #modelSelector::down-arrow {{
            width: 7px; height: 7px;
        }}
        #modelSelector QAbstractItemView {{
            background: {Colors.COMPOSER}; color: {Colors.TEXT};
            border: 1px solid {Colors.BORDER_STRONG}; outline: none;
            padding: 5px; selection-background-color: {Colors.PANEL_ACTIVE};
        }}
        #modelSelector QAbstractItemView::item {{
            min-height: 30px; padding: 7px 12px;
        }}
        #modelSelector QAbstractItemView::item:hover {{
            background: {Colors.PANEL_HOVER};
        }}
        #modelSelector:hover {{ color: {Colors.TEXT}; }}
        #modelSelector:disabled {{ color: {Colors.TEXT_FAINT}; }}
        #sendButton {{
            background: {Colors.ACCENT}; color: white; border: none;
            border-radius: 19px; min-width: 38px; min-height: 38px; max-width: 38px; max-height: 38px;
            margin-right: 0px;
        }}
        #sendButton:hover {{ background: {Colors.ACCENT_HOVER}; }}
        #sendButton:pressed {{ background: #7947e8; }}
        #sendButton:disabled {{ background: #555; color: #8c8c8c; }}
        #footerStatus {{ color: {Colors.TEXT_FAINT}; font-size: 10px; padding-bottom: 1px; }}
    """
