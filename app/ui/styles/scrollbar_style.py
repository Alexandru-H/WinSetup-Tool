"""
scrollbar_style.py — shared QSS builder for styled scrollbars.

Contract:
    * Single source of truth for the modern scrollbar style used
      throughout the app. Extracted from main_window.py where the same
      QSS is applied globally to #central; widgets that set their own
      QScrollArea stylesheet (breaking the cascade) must call
      build_scrollbar_qss() explicitly in their refresh_theme() hook.
    * Uses only `text_sec` (handle) and `text_pri` (handle hover) from
      the palette — same tokens as main_window._apply_window_stylesheets.
    * Width: 10 px, border-radius: 4 px, no arrow buttons.
    * Covers both vertical and horizontal axes.
"""

from __future__ import annotations


def build_scrollbar_qss(theme: dict[str, str]) -> str:
    """Return theme-aware QSS for vertical + horizontal scrollbars.

    Paste the result into any widget's setStyleSheet() call alongside
    widget-specific rules.  Example usage in a page's refresh_theme():

        self._scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }\\n"
            + build_scrollbar_qss(theme)
        )
    """
    s = theme["text_sec"]   # handle normal
    p = theme["text_pri"]   # handle hover

    return f"""
        QScrollBar:vertical {{
            background: transparent;
            width: 10px;
            margin: 0px;
            border: none;
        }}
        QScrollBar::handle:vertical {{
            background: {s};
            min-height: 30px;
            border-radius: 4px;
        }}
        QScrollBar::handle:vertical:hover {{
            background: {p};
        }}
        QScrollBar::add-line:vertical,
        QScrollBar::sub-line:vertical {{
            background: transparent;
            height: 0px;
            border: none;
        }}
        QScrollBar::add-page:vertical,
        QScrollBar::sub-page:vertical {{
            background: transparent;
        }}

        QScrollBar:horizontal {{
            background: transparent;
            height: 10px;
            margin: 0px;
            border: none;
        }}
        QScrollBar::handle:horizontal {{
            background: {s};
            min-width: 30px;
            border-radius: 4px;
        }}
        QScrollBar::handle:horizontal:hover {{
            background: {p};
        }}
        QScrollBar::add-line:horizontal,
        QScrollBar::sub-line:horizontal {{
            background: transparent;
            width: 0px;
            border: none;
        }}
        QScrollBar::add-page:horizontal,
        QScrollBar::sub-page:horizontal {{
            background: transparent;
        }}
    """
