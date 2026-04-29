"""
action_footer.py — reusable progress + action button footer for batch-operation pages.

Contract (CLAUDE.md §4.1, §8.1):
    * ActionFooter is a pure UI component. It owns NO worker threads and
      holds NO knowledge of what work is being done. Pages that use it own
      the worker and feed events in via the public API:
          set_state(state, total)
          set_progress(current, percent)
          set_current_item(name, speed, stage)
    * The widget itself is transparent (WA_TranslucentBackground) — it sits
      on the page's body-level acrylic strip, not on the panel_bg surface.
    * Progress bar: custom QWidget (_ProgressBar) with a QPropertyAnimation
      on `_progress_value` (float 0.0–1.0, 200 ms OutCubic). Never use
      QProgressBar — its QSS rounded-fill support is unreliable.
    * Action button labels are intentionally hardcoded English ("Install",
      "Install (N)", "Cancel") — no i18n per agreed strategy.
    * Status label uses two i18n keys (added to translation files later):
          widgets.action_footer.items_ready  — READY state
          widgets.action_footer.done         — DONE state
      Until translations land, tr() returns the key string itself, which is
      acceptable during development.
    * All colors come from the palette dict. No hardcoded hex anywhere.

Visual layout (fixed height 72 px):
    ┌─────────────────────────────────────────────────────────────────────┐
    │  [progress bar — flex grows]                   [action button 36h]  │  top row 36px
    │  17/24 · 12.4 MB/s · Notepad++ · Downloading                        │  status  18px
    └─────────────────────────────────────────────────────────────────────┘
    Outer vertical margins: 8 px top, 8 px bottom. Row spacing: 2 px.
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPropertyAnimation,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.core.logging_setup import get_logger
from app.ui.widgets.themed_widget import LocalizedWidget, ThemedWidget

if TYPE_CHECKING:
    from app.core.i18n import LocaleManager
    from app.core.theming import ThemeManager

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Layout constants
# ---------------------------------------------------------------------------

_FOOTER_H    = 72
_BAR_H       = 36
_RADIUS      = 18      # pill border-radius matches bar height / 2
_BTN_MIN_W   = 140
_BTN_MAX_W   = 200
_ROW_SPACING = 12      # gap between progress bar and button
_ROW_V_PAD   = 8       # vertical padding top and bottom
_ROW_V_GAP   = 2       # gap between top row and status label


# ---------------------------------------------------------------------------
# Internal: custom rounded-pill progress bar
# ---------------------------------------------------------------------------

class _ProgressBar(QWidget):
    """Animated fill-pill. Parent ActionFooter owns the animation object.

    Colors are set externally (no default hex) so hot-swap theme works
    without reconstructing this widget.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._val: float = 0.0
        self._track: str = "#222222"   # overwritten immediately by refresh_theme
        self._fill:  str = "#3b82f6"   # overwritten immediately by refresh_theme
        self.setFixedHeight(_BAR_H)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )

    # ---- Qt property for QPropertyAnimation ----

    def _get_val(self) -> float:
        return self._val

    def _set_val(self, value: float) -> None:
        self._val = max(0.0, min(1.0, float(value)))
        self.update()

    _progress_value = Property(float, _get_val, _set_val)

    # ---- Colour setters (called by ActionFooter.refresh_theme) ----

    def set_track_color(self, color: str) -> None:
        self._track = color
        self.update()

    def set_fill_color(self, color: str) -> None:
        self._fill = color
        self.update()

    def value(self) -> float:
        return self._val

    # ---- Paint ----

    def paintEvent(self, event) -> None:   # noqa: N802, ARG002
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = float(self.width())
        h = float(self.height())
        r = h / 2.0

        # Track (full pill, background)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(self._track))
        p.drawRoundedRect(self.rect(), r, r)

        # Fill (same pill shape, clipped to `_val` fraction of width)
        fill_w = w * self._val
        if fill_w > 0.5:
            p.save()
            p.setClipRect(0.0, 0.0, fill_w, h)
            p.setBrush(QColor(self._fill))
            p.drawRoundedRect(self.rect(), r, r)
            p.restore()

        p.end()


# ---------------------------------------------------------------------------
# ActionFooter — public widget
# ---------------------------------------------------------------------------

class ActionFooter(QWidget, ThemedWidget, LocalizedWidget):
    """Reusable footer with animated progress bar and primary action button.

    Used by Apps, Optimizations, Debloat, and Uninstall pages. The page
    owns the worker thread and pushes events here; this widget never
    reads from winget.py or any system module directly.

    Signals:
        action_clicked — emitted when the user clicks the action button.
            Meaning depends on the current State:
                READY   → start the batch operation
                RUNNING → cancel the batch operation
                DONE    → dismiss / re-run (page decides)
    """

    action_clicked = Signal()

    class State(Enum):
        IDLE           = "idle"            # nothing selected; button disabled
        READY          = "ready"           # items selected; "Install (N)"
        RUNNING        = "running"         # in progress; button shows "Cancel"
        DONE           = "done"            # finished; "Install (N)" again
        WINGET_MISSING = "winget_missing"  # winget absent; "Install winget"
        BOOTSTRAPPING  = "bootstrapping"   # winget bootstrap in progress

    # ---- Construction ---------------------------------------------------

    def __init__(
        self,
        theme_mgr: "ThemeManager",
        locale_mgr: "LocaleManager",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedHeight(_FOOTER_H)

        # Internal state
        self._state:        ActionFooter.State = ActionFooter.State.IDLE
        self._total:        int = 0
        self._current_idx:  int = 0
        self._item_name:    str | None = None
        self._speed:        str | None = None
        self._stage:        str | None = None
        self._failed_count: int = 0
        # Custom action button label (empty = use default "Install")
        self._custom_action_label: str = ""

        self._build_ui()

        # Mixin wiring — calls refresh_theme + refresh_locale immediately
        self.connect_theme(theme_mgr)
        self.connect_locale(locale_mgr)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, _ROW_V_PAD, 0, _ROW_V_PAD)
        root.setSpacing(_ROW_V_GAP)

        # --- Top row: progress bar + button ---
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(_ROW_SPACING)

        self._bar = _ProgressBar(self)
        top.addWidget(self._bar, 1)

        # Animation targets the bar's Qt property
        self._anim = QPropertyAnimation(self._bar, b"_progress_value", self)
        self._anim.setDuration(200)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self._btn = QPushButton(self)
        self._btn.setFixedHeight(_BAR_H)
        self._btn.setMinimumWidth(_BTN_MIN_W)
        self._btn.setMaximumWidth(_BTN_MAX_W)
        self._btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn.setEnabled(False)   # IDLE default

        btn_font = QFont()
        btn_font.setPointSize(13)
        btn_font.setWeight(QFont.Weight.DemiBold)
        self._btn.setFont(btn_font)

        self._btn.clicked.connect(self.action_clicked.emit)
        top.addWidget(self._btn, 0)
        root.addLayout(top)

        # --- Status label ---
        self._status_lbl = QLabel("", self)
        self._status_lbl.setFixedHeight(18)
        lbl_font = QFont()
        lbl_font.setPointSize(11)
        self._status_lbl.setFont(lbl_font)
        self._status_lbl.setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground, True
        )
        root.addWidget(self._status_lbl)

    # ---- Public API -----------------------------------------------------

    def set_state(
        self, state: "ActionFooter.State", total: int = 0
    ) -> None:
        """Switch to a new state.

        ``total`` is the number of items in the current batch — used for
        the button label ("Install (N)") and progress fraction calculation.
        """
        self._state = state
        self._total = total
        self._current_idx = 0
        self._item_name = None
        self._speed = None
        self._stage = None

        S = ActionFooter.State
        if state != S.DONE:
            self._failed_count = 0
        self._btn.setEnabled(state not in (S.IDLE, S.BOOTSTRAPPING))

        if state in (S.IDLE, S.READY, S.WINGET_MISSING):
            self._animate_to(0.0)
        elif state == S.DONE:
            self._animate_to(1.0)
        # RUNNING / BOOTSTRAPPING: bar moves via set_progress() — do not reset

        self._refresh_button_label()
        self._refresh_status_label()
        # Re-apply QSS so enabled/disabled background tracks state change
        self._apply_button_qss(self._theme_mgr.current())

        log.debug(
            "ActionFooter.set_state: %s total=%d", state.value, total
        )

    def state(self) -> "ActionFooter.State":
        return self._state

    def set_failed_count(self, n: int) -> None:
        self._failed_count = n
        self._refresh_status_label()

    def set_progress(
        self, current: int, percent: int | None = None
    ) -> None:
        """Update the item counter and progress bar fill.

        ``current`` is 1-indexed (1 = working on the first item).
        ``percent`` is 0–100 fine-grained progress within the current
        item (e.g. download percent). When None, the bar snaps to the
        coarse ``current / total`` fraction.
        """
        self._current_idx = current

        if self._total > 0:
            if percent is not None:
                # Coarse fraction for completed items + fine fraction for
                # the current item's intra-item progress.
                coarse = (current - 1) / self._total
                fine   = (percent / 100.0) / self._total
                target = coarse + fine
            else:
                target = current / self._total
            self._animate_to(min(1.0, target))

        self._refresh_status_label()

    def set_current_item(
        self,
        name:  str | None,
        speed: str | None = None,
        stage: str | None = None,
    ) -> None:
        """Update the status label fields for the item currently being processed.

        Pass ``None`` to clear a field. Empty strings are also treated as None.
        The label format: ``"{count} · {speed} · {name} · {stage}"`` with
        missing fields collapsed (no trailing/leading dots).
        """
        self._item_name = name  or None
        self._speed     = speed or None
        self._stage     = stage or None
        self._refresh_status_label()

    # ---- Private helpers ------------------------------------------------

    def _animate_to(self, target: float) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._bar.value())
        self._anim.setEndValue(float(target))
        self._anim.start()

    def set_action_label(self, label: str) -> None:
        """Override the default 'Install (N)' text with a custom label.

        Pass empty string to revert to the default 'Install'. Only affects
        READY and DONE states. Call this from the page's sync_footer() or
        via a ``footer_action_label_requested`` signal.
        """
        self._custom_action_label = label
        self._refresh_button_label()

    def _refresh_button_label(self) -> None:
        """Button labels are hardcoded English — not translated."""
        S = ActionFooter.State
        if self._state == S.RUNNING:
            text = "Cancel"
        elif self._state == S.WINGET_MISSING:
            text = "Install winget"
        elif self._state == S.BOOTSTRAPPING:
            text = "Installing..."
        elif self._state in (S.READY, S.DONE) and self._total > 0:
            verb = self._custom_action_label or "Install"
            text = f"{verb} ({self._total})"
        else:
            text = self._custom_action_label or "Install"
        self._btn.setText(text)

    def _refresh_status_label(self) -> None:
        S = ActionFooter.State

        if self._state == S.IDLE:
            self._status_lbl.setText("")
            return

        if self._state == S.READY:
            self._status_lbl.setText(
                self._locale_mgr.tr(
                    "widgets.action_footer.items_ready", n=self._total
                )
            )
            return

        if self._state == S.DONE:
            if self._failed_count > 0:
                succeeded = self._total - self._failed_count
                self._status_lbl.setText(
                    f"{succeeded}/{self._total} done · {self._failed_count} failed"
                )
            else:
                self._status_lbl.setText(
                    self._locale_mgr.tr(
                        "widgets.action_footer.done", n=self._total
                    )
                )
            return

        if self._state == S.WINGET_MISSING:
            # Hardcoded English per agreed strategy.
            self._status_lbl.setText("Winget is not available on this system")
            return

        # RUNNING / BOOTSTRAPPING — assemble only the fields currently set
        parts: list[str] = []
        if self._total > 0 and self._current_idx > 0:
            parts.append(f"{self._current_idx}/{self._total}")
        if self._speed:
            parts.append(self._speed)
        if self._item_name:
            parts.append(self._item_name)
        if self._stage:
            parts.append(self._stage)

        self._status_lbl.setText(" · ".join(parts))   # " · "

    def _apply_button_qss(self, theme: dict[str, str]) -> None:
        """Rebuild the button stylesheet from the current palette.

        Called by refresh_theme AND after every set_state() call so the
        enabled/disabled background colour stays in sync with state.
        """
        enabled = self._btn.isEnabled()
        bg_normal = theme["accent"] if enabled else theme["card"]

        self._btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {bg_normal};
                color: {theme["text_pri"]};
                border: none;
                border-radius: {_RADIUS}px;
                padding: 0 20px;
            }}
            QPushButton:hover:!disabled {{
                background-color: {theme["accent_h"]};
            }}
            QPushButton:disabled {{
                background-color: {theme["card"]};
                color: {theme["text_sec"]};
            }}
        """)

    # ---- Mixin hooks ----------------------------------------------------

    def refresh_theme(self, theme: dict[str, str]) -> None:
        self._bar.set_track_color(theme["card"])
        self._bar.set_fill_color(theme["accent"])
        self._apply_button_qss(theme)
        self._status_lbl.setStyleSheet(
            f"color: {theme['text_sec']}; background: transparent;"
        )

    def refresh_locale(self) -> None:
        # Only the status label uses i18n. Button labels are English-only.
        self._refresh_status_label()


# ---------------------------------------------------------------------------
# Standalone smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from app.core.i18n import LocaleManager
    from app.core.logging_setup import setup_logging
    from app.core.settings import Settings
    from app.core.theming import ThemeManager

    setup_logging()
    qapp = QApplication(sys.argv)

    _settings = Settings()
    _settings.load()
    _theme_mgr  = ThemeManager(_settings)
    _locale_mgr = LocaleManager(_settings)

    # Container — gives the transparent footer a visible surface
    win = QWidget()
    win.setWindowTitle("ActionFooter — smoke test")
    win.setFixedSize(900, 120)

    layout = QVBoxLayout(win)
    layout.setContentsMargins(16, 24, 16, 24)

    footer = ActionFooter(_theme_mgr, _locale_mgr, win)
    layout.addWidget(footer)

    def _apply_win_bg(theme: dict) -> None:
        win.setStyleSheet(f"background-color: {theme['body_bg']};")

    _apply_win_bg(_theme_mgr.current())
    _theme_mgr.themeChanged.connect(_apply_win_bg)

    win.show()

    # Timed state cycle (matches spec §Validation)
    _step = 0
    _timer = QTimer()
    S = ActionFooter.State

    def _tick() -> None:
        global _step
        if _step == 0:
            footer.set_state(S.IDLE)
            print("[0s] IDLE")
        elif _step == 1:
            footer.set_state(S.READY, total=24)
            print("[1s] READY total=24")
        elif _step == 2:
            footer.set_state(S.RUNNING, total=24)
            footer.set_progress(1, 0)
            footer.set_current_item("Notepad++", "12.4 MB/s", "Downloading")
            print("[2s] RUNNING 1/24  Notepad++  Downloading  0%")
        elif _step == 3:
            footer.set_progress(1, 50)
            print("[3s] 1/24  50%")
        elif _step == 4:
            footer.set_progress(1, 100)
            footer.set_current_item("Notepad++", None, "Installing")
            print("[4s] 1/24  100%  Installing")
        elif _step == 5:
            footer.set_progress(2, 0)
            footer.set_current_item("VS Code", "8.1 MB/s", "Downloading")
            print("[5s] 2/24  VS Code  Downloading  0%")
        elif _step == 8:
            footer.set_state(S.DONE, total=24)
            print("[8s] DONE")
            _timer.stop()
        # steps 6 & 7 — no action, window stays open for visual inspection
        _step += 1

    _timer.timeout.connect(_tick)
    _timer.start(1000)
    _tick()   # run step 0 immediately

    sys.exit(qapp.exec())
