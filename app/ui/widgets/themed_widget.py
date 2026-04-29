"""
Theme + locale refresh mixins — the foundation of hot-swap without restart.

Widgets combine these mixins with any QWidget subclass via multiple
inheritance. The mixins themselves deliberately do NOT inherit from
QObject or QWidget: doing so would collide with QFrame / QPushButton /
QLabel on MRO and duplicate Qt's metaclass machinery. They are plain
Python classes whose only job is to connect a signal and delegate to a
`refresh_*` hook the subclass implements.

Usage:

    class MyCard(QFrame, ThemedWidget, LocalizedWidget):
        def __init__(self, theme_mgr, locale_mgr):
            super().__init__()
            self.connect_theme(theme_mgr)
            self.connect_locale(locale_mgr)

        def refresh_theme(self, theme: dict[str, str]) -> None:
            self.setStyleSheet(f"background: {theme['card']};")

        def refresh_locale(self) -> None:
            self._title.setText(self._locale_mgr.tr("card.title"))

Disconnect lifecycle:
    Qt automatically severs connections when the receiver widget is
    destroyed — the signal is owned by the manager, which holds only a
    weak functional reference to the slot, so there is no leak in the
    normal teardown path. Explicit disconnect() in closeEvent is a TODO
    for edge cases (reparenting, unusual teardown ordering) if any
    surface later.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.i18n import LocaleManager
    from app.core.theming import ThemeManager


class ThemedWidget:
    """Mixin: subscribe to theme changes and re-style when they arrive."""

    def connect_theme(self, theme_mgr: ThemeManager) -> None:
        """Wire this widget to theme_mgr and apply the current palette now."""
        self._theme_mgr = theme_mgr
        theme_mgr.themeChanged.connect(self._on_theme_changed)
        self.refresh_theme(theme_mgr.current())

    def _on_theme_changed(self, theme: dict[str, str]) -> None:
        """Signal adapter — lets subclasses override refresh_theme freely."""
        self.refresh_theme(theme)

    def refresh_theme(self, theme: dict[str, str]) -> None:
        """Override this to re-apply stylesheets using the new color dict."""
        raise NotImplementedError(
            "Subclasses of ThemedWidget must implement refresh_theme(theme: dict)."
        )


class LocalizedWidget:
    """Mixin: subscribe to locale changes and re-apply text when they arrive."""

    def connect_locale(self, locale_mgr: LocaleManager) -> None:
        """Wire this widget to locale_mgr and apply the current locale now."""
        self._locale_mgr = locale_mgr
        locale_mgr.localeChanged.connect(self._on_locale_changed)
        self.refresh_locale()

    def _on_locale_changed(self, code: str) -> None:
        """Signal adapter — lets subclasses override refresh_locale freely."""
        self.refresh_locale()

    def refresh_locale(self) -> None:
        """Override this to re-apply translated strings to child widgets."""
        raise NotImplementedError(
            "Subclasses of LocalizedWidget must implement refresh_locale()."
        )
