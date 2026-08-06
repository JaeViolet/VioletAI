"""Stylesheet definitions for VioletAI."""

from __future__ import annotations

from PySide6.QtGui import QColor

from ui.design import Colors, Radius


def lighten(color: str, percent: int = 115) -> str:
    return QColor(color).lighter(percent).name()


def darken(color: str, percent: int = 118) -> str:
    return QColor(color).darker(percent).name()


def app_stylesheet(accent: str = Colors.ACCENT) -> str:
    accent_hover = lighten(accent, 115)
    accent_pressed = darken(accent, 118)
    return f"""
        * {{ font-family: "Segoe UI Variable", "Segoe UI"; }}
        QMainWindow, #centralWidget, #mainPanel, #messageContainer {{
            background: {Colors.SURFACE}; color: {Colors.TEXT};
        }}
        #sidebar {{
            background: {Colors.BLACK}; border-right: 1px solid {Colors.BORDER};
        }}
        #sidebarTopTitle {{ color: {accent}; font-size: 22px; font-weight: 1; margin-left: 8px; }}
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
        #conversationRow {{ background: transparent; border: none; border-radius: 0px; }}
        #conversationRow:hover {{ background: {Colors.SETTINGS_HOVER}; }}
        #conversationRow[active="true"] {{ background: {Colors.SETTINGS_ACTIVE}; }}
        #conversationTitle {{ color: {Colors.TEXT}; font-size: 13px; }}
        #conversationTitle[active="true"] {{ color: {accent}; }}
        #searchOverlay {{
            background: {Colors.COMPOSER}; border: 1px solid {Colors.BORDER_STRONG};
            border-radius: {Radius.LG}px;
        }}
        #settingsPanel {{
            background: {Colors.BLACK}; border: 1px solid {Colors.SETTINGS_BORDER};
            border-radius: 0px;
        }}
        #settingsNav {{
            background: {Colors.SETTINGS_NAV};
            border-right: 1px solid {Colors.SETTINGS_BORDER};
            border-radius: 0px;
        }}
        #settingsNavBrand {{
            color: {Colors.TEXT}; font-size: 15px; font-weight: 650;
            padding: 6px 18px 16px 18px;
        }}
        #settingsTab {{
            background: transparent; color: {Colors.TEXT_MUTED}; border: none;
            margin: 0px; padding: 10px 18px; text-align: left;
            border-radius: 0px; font-size: 14px; font-weight: 500;
        }}
        #settingsTab:hover {{ background: {Colors.SETTINGS_HOVER}; color: {Colors.TEXT}; }}
        #settingsTab[active="true"] {{ background: {Colors.SETTINGS_ACTIVE}; color: {accent}; }}
        #settingsHeader {{
            border-bottom: 1px solid {Colors.SETTINGS_BORDER};
        }}
        #settingsHeaderTitle {{ color: {Colors.TEXT}; font-size: 20px; font-weight: 600; }}
        #settingsTabsScroll, #settingsScroll, #settingsStack, #settingsPage {{
            background: transparent; border: none;
        }}
        #settingsTabsScroll > QWidget > QWidget, #settingsScroll > QWidget > QWidget {{
            background: transparent; border: none;
        }}
        #settingsPlaceholderText {{ color: {Colors.TEXT_FAINT}; font-size: 14px; }}
        #settingsSearchInput {{
            background: #0d0d0d; color: {Colors.TEXT}; border: 1px solid {Colors.SETTINGS_BORDER};
            padding: 9px 12px; font-size: 14px; border-radius: 0px;
        }}
        #settingsSearchInput:focus {{ border-color: {accent}; }}
        #settingsSearchInput::placeholder {{ color: {Colors.TEXT_FAINT}; }}
        #settingsActionButton {{
            background: transparent; color: {Colors.TEXT_MUTED};
            border: 1px solid {Colors.SETTINGS_BORDER};
            padding: 5px 12px; font-size: 12px; border-radius: 0px;
        }}
        #settingsActionButton:hover {{ background: {Colors.SETTINGS_HOVER}; color: {Colors.TEXT}; }}
        #settingsClearButton {{
            background: transparent; color: {Colors.ERROR_STRONG};
            border: 1px solid #4a2828; padding: 5px 12px; font-size: 12px; border-radius: 0px;
        }}
        #settingsClearButton:hover {{ background: #301414; color: {Colors.ERROR_STRONG}; }}
        #settingsDangerButton {{
            background: #2a1212; color: {Colors.ERROR_STRONG};
            border: 1px solid #4a2828; padding: 5px 12px; font-size: 12px; border-radius: 0px;
        }}
        #settingsDangerButton:hover {{ background: #3a1818; color: {Colors.ERROR_STRONG}; }}
        #settingsConfirm {{ background: #160b0b; border: 1px solid #2a1515; border-radius: 0px; }}
        #settingsConfirmTitle {{ color: {Colors.TEXT}; font-size: 14px; font-weight: 600; }}
        #settingsConfirmText {{ color: {Colors.TEXT_FAINT}; font-size: 12px; }}
        #settingsErrorText {{ color: {Colors.ERROR_STRONG}; font-size: 12px; }}
        #confirmBackdrop {{ background: rgba(5, 5, 5, 175); border: none; }}
        #confirmCard {{ background: #141414; border: 1px solid {Colors.BORDER_STRONG}; border-radius: 0px; }}
        #confirmCardTitle {{ color: {Colors.TEXT}; font-size: 16px; font-weight: 600; }}
        #confirmCardText {{ color: {Colors.TEXT_MUTED}; font-size: 13px; }}
        #conversationEditor {{
            background: #0d0d0d; color: {Colors.TEXT};
            border: 1px solid {Colors.BORDER_STRONG}; padding: 2px 4px;
            font-size: 13px; border-radius: 0px;
        }}
        #settingsSectionTitle {{
            color: {Colors.TEXT_MUTED}; font-size: 11px; font-weight: 650;
            letter-spacing: 1px; text-transform: uppercase;
        }}
        #memoryValue {{ color: {Colors.TEXT}; font-size: 14px; }}
        #memoryMeta {{ color: {Colors.TEXT_FAINT}; font-size: 12px; }}
        #settingsMemoryRow {{ background: transparent; }}
        #settingsMemoryRow:hover {{ background: #111111; }}
        #themePreset {{ background: #101010; border: 1px solid #262626; border-radius: 0px; }}
        #themePreset:hover {{ background: #171717; }}
        #themePreset[active="true"] {{ border: 1px solid {accent}; }}
        #themePresetName {{ color: {Colors.TEXT}; font-size: 13px; }}
        #themeValueLabel {{ color: {Colors.TEXT_FAINT}; font-size: 12px; min-width: 28px; }}
        #themeSlider::groove:horizontal {{ height: 4px; background: #2a2a2a; border-radius: 2px; }}
        #themeSlider::sub-page:horizontal {{ background: {accent}; border-radius: 2px; }}
        #themeSlider::add-page:horizontal {{ background: #2a2a2a; border-radius: 2px; }}
        #themeSlider::handle:horizontal {{
            background: {Colors.TEXT}; width: 12px; height: 12px;
            margin: -4px 0; border-radius: 6px;
        }}
        #overlaySearchInput {{
            background: transparent; color: {Colors.TEXT}; border: none;
            font-size: 18px; padding: 10px 4px;
        }}
        #overlayHint {{ color: {Colors.TEXT_MUTED}; font-size: 12px; }}
        #chatScroll {{ background: {Colors.SURFACE}; border: none; }}
        QScrollBar:vertical {{ background: transparent; width: 8px; margin: 3px; }}
        QScrollBar::handle:vertical {{ background: #4a4a4a; border-radius: 4px; min-height: 36px; }}
        QScrollBar::handle:vertical:hover {{ background: {accent}; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
        #welcomeIcon {{ color: {accent}; font-size: 28px; font-weight: 650; }}
        #welcomeTitle {{ color: {Colors.TEXT}; font-size: 25px; font-weight: 620; }}
        #welcomeSubtitle {{ color: {Colors.TEXT_MUTED}; font-size: 13px; }}
        #userBubble {{
            background: {Colors.USER_BUBBLE}; color: {Colors.TEXT}; border: none;
            border-radius: {Radius.LG}px;
        }}
        #userBubble #markdownView {{
            color: {Colors.TEXT};
            selection-background-color: {accent}; selection-color: #ffffff;
        }}
        #assistantBubble, #errorBubble {{ background: transparent; border: none; }}
        #errorBubble {{ color: {Colors.ERROR}; }}
        #markdownView {{
            background: transparent; color: {Colors.TEXT}; border: none;
            font-size: 15px; selection-background-color: {accent};
        }}
        #thinkingDots {{ color: {accent}; font-size: 24px; letter-spacing: 2px; }}
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
            padding: 0; selection-background-color: {accent};
        }}
        #inputPanel {{ background: {Colors.SURFACE}; }}
        #composer {{
            background: {Colors.COMPOSER}; border: 1px solid {Colors.BORDER_STRONG};
            border-radius: 28px;
        }}
        #composer[compact="true"] {{
            border-radius: 25px;
        }}
        #composerToolbar {{ background: transparent; border: none; }}
        #messageInput {{
            background: transparent; color: {Colors.TEXT}; border: none;
            padding: 0px 3px; font-size: 15px; selection-background-color: {accent};
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
        #modelSelector QAbstractItemView::item:selected {{
            background: {accent}; color: white;
        }}
        #modelSelector:hover {{ color: {Colors.TEXT}; }}
        #modelSelector:disabled {{ color: {Colors.TEXT_FAINT}; }}
        #sendButton {{
            background: {accent}; color: white; border: none;
            border-radius: 19px; min-width: 38px; min-height: 38px; max-width: 38px; max-height: 38px;
            margin-right: 0px;
        }}
        #sendButton:hover {{ background: {accent_hover}; }}
        #sendButton:pressed {{ background: {accent_pressed}; }}
        #sendButton:disabled {{ background: #555; color: #8c8c8c; }}
        #footerStatus {{ color: {Colors.TEXT_FAINT}; font-size: 10px; padding-bottom: 1px; }}
    """
