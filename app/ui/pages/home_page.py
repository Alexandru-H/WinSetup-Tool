"""
home_page.py — live hardware dashboard.

Layout (CLAUDE.md §4, §8.1):

    ┌───────────────────────────────────────────────────────────────┐
    │ Home                                            Up for 5d 12h │
    │ Sistemul tău într-o privire · Windows 11 IoT LTSC · Build 26… │
    │                                                               │
    │  ┌─── CPU ────────────┐   ┌─── GPU ───────────────┐           │
    │  │ Ryzen 7 7800X3D  14%│   │ RTX 5070 Ti         42%│           │  Row 1 (160px)
    │  │ 8c/16t             │   │ + AMD Radeon iGPU   │              │
    │  │ ▓▓░░░░░░░░░░░░░░░ │   │ ▓▓▓▓▓░░░░░░░░░░░ │              │
    │  └────────────────────┘   └───────────────────────┘           │
    │                                                               │
    │  ┌── RAM ───────────────────────────────────── 44% ─┐         │
    │  │ ▓▓▓▓▓▓▓▓▓░░░░░░░░░░░░░░░░░░░░░░░░░░░░░     │         │
    │  │ 13.7 / 31.1 GB                                    │         │  Row 2 (220px)
    │  │ ───────────────────────────────────────────    │         │
    │  │ Top 3 procese:                                    │         │
    │  │ Chrome                                     2.1 GB │         │
    │  │ Code                                       450 MB │         │
    │  │ explorer                                   89  MB │         │
    │  └───────────────────────────────────────────────┘         │
    │                                                               │
    │  ┌── C: NVMe ──┐  ┌── D: NVMe ──┐  ┌── X: SSD ──┐            │
    │  │ ● Healthy   │  │ ● Healthy   │  │ ● Healthy  │            │  Row 3 (140px)
    │  │ ▓▓░░░░░░░░ │  │ ▓▓▓▓▓░░░░░ │  │ ▓▓░░░░░░░  │            │
    │  │ 80 / 930 GB │  │ 400/930 GB  │  │ 78/931 GB  │            │
    │  └─────────────┘  └─────────────┘  └────────────┘            │
    └───────────────────────────────────────────────────────────────┘

The whole content area is wrapped in a ``QScrollArea`` so the dashboard
grows gracefully with more drives / future cards without the top row
clipping.

Threading (CLAUDE.md §4.4):
    * ``HardwareWorker`` polls every 2 s on a background ``QThread``.
    * Static metrics (CPU name, cores, GPU names, OS info) cached on
      first tick — the registry doesn't need re-query every 2 s.
    * Drives cached at tick level (refresh every 15 ticks = 30 s);
      ``hardware.drive_info`` itself also caches 30 s at module level,
      belt-and-suspenders.
    * Dynamic metrics (cpu_percent, gpu_usage, ram, uptime, top
      processes) refresh on every tick.
    * Thread starts in ``showEvent`` (first visible paint), stops on
      ``QApplication.aboutToQuit``.

Hot-swap:
    * ``refresh_theme`` re-styles page-level labels (title, subtitle,
      uptime). Cards and sub-widgets refresh via their own mixin
      connections.
    * ``refresh_locale`` reflows the title/subtitle/card titles/health
      labels. Hardware names (CPU, GPU, OS) are proper nouns and stay
      as-is across languages. The subtitle re-composes with OS info
      appended after the translated prefix.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from PySide6.QtCore import (
    QObject,
    QThread,
    Qt,
    Signal,
    Slot,
)
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.core.logging_setup import get_logger
from app.system import hardware
from app.ui.styles.scrollbar_style import build_scrollbar_qss
from app.ui.widgets.themed_widget import LocalizedWidget, ThemedWidget

if TYPE_CHECKING:
    from app.core.i18n import LocaleManager
    from app.core.settings import Settings
    from app.core.theming import ThemeManager

log = get_logger(__name__)


# =========================================================================
# Font helpers
# =========================================================================

def _primary_font() -> QFont:
    f = QFont()
    f.setPointSize(15)
    f.setWeight(QFont.Weight.Medium)
    return f


def _secondary_font() -> QFont:
    f = QFont()
    f.setPointSize(13)
    return f


def _small_medium_font() -> QFont:
    f = QFont()
    f.setPointSize(11)
    f.setWeight(QFont.Weight.Medium)
    return f


def _big_percent_font() -> QFont:
    f = QFont()
    f.setPointSize(20)
    f.setWeight(QFont.Weight.Medium)
    return f


# =========================================================================
# HardwareWorker — background polling via QObject + moveToThread
# =========================================================================

class HardwareWorker(QObject):
    """2-second snapshot loop. See module docstring for the schema.

    Attribute layering:
        * ``_static_cache`` — populated on first tick, never refreshed.
        * ``_drives_snapshot`` — refreshed every ``_DRIVES_EVERY_N_TICKS``
          ticks (15 × 2 s = 30 s).
        * All other fields re-sampled every tick.
    """

    snapshotReady = Signal(dict)
    error = Signal(str)

    POLL_INTERVAL_S = 2.0
    _SLEEP_CHUNK_S = 0.1                  # stop() latency ceiling
    _DRIVES_EVERY_N_TICKS = 15            # drives refresh every 30 s

    def __init__(self) -> None:
        super().__init__()
        self._running = False
        self._tick: int = 0
        self._static_cache: dict | None = None
        self._drives_snapshot: list[dict] | None = None

    # ---- Slots (invoked on the worker thread) ---------------------------

    @Slot()
    def start(self) -> None:
        self._running = True
        try:
            while self._running:
                snapshot = self._build_snapshot()
                if self._running:
                    self.snapshotReady.emit(snapshot)
                slept = 0.0
                while self._running and slept < self.POLL_INTERVAL_S:
                    time.sleep(self._SLEEP_CHUNK_S)
                    slept += self._SLEEP_CHUNK_S
        except Exception as exc:   # noqa: BLE001
            log.exception("HardwareWorker crashed")
            self.error.emit(str(exc))

    def stop(self) -> None:
        """Flag-based stop — Python attribute writes are GIL-atomic."""
        self._running = False

    # ---- Snapshot composition -------------------------------------------

    def _build_snapshot(self) -> dict:
        if self._static_cache is None:
            self._static_cache = {
                "cpu_name":  hardware.cpu_name(),
                "cpu_cores": hardware.cpu_core_count(),
                "gpu_names": hardware.gpu_names(),
                "os_info":   hardware.os_info(),
            }

        # Drives: fetch on first tick and then every Nth tick. The
        # hardware module also caches 30 s internally — this just keeps
        # the worker from paying the call cost.
        if (
            self._drives_snapshot is None
            or self._tick % self._DRIVES_EVERY_N_TICKS == 0
        ):
            self._drives_snapshot = hardware.drive_info()

        snap = dict(self._static_cache)
        snap["cpu_percent"]    = hardware.cpu_percent(interval=0.1)
        snap["gpu_usage"]      = hardware.gpu_usage_nvidia()
        snap["ram"]            = hardware.ram_info()
        snap["uptime_str"]     = hardware.uptime_str()
        snap["top_processes"] = hardware.top_processes_by_memory(3)
        snap["drives"]         = list(self._drives_snapshot)

        self._tick += 1
        return snap


# =========================================================================
# _ElidedLabel — single-line label with right-ellipsis on resize
# =========================================================================

class _ElidedLabel(QLabel):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full_text = ""
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.setWordWrap(False)
        self.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )

    def setFullText(self, text: str) -> None:
        self._full_text = text or ""
        self.setToolTip(self._full_text)
        self._relayout()

    def fullText(self) -> str:
        return self._full_text

    def resizeEvent(self, event) -> None:   # noqa: N802 (Qt naming)
        super().resizeEvent(event)
        self._relayout()

    def _relayout(self) -> None:
        if not self._full_text:
            super().setText("")
            return
        fm = QFontMetrics(self.font())
        avail = max(0, self.width() - 2)
        super().setText(
            fm.elidedText(
                self._full_text, Qt.TextElideMode.ElideRight, avail
            )
        )


# =========================================================================
# _UsageBar — themed progress pill (used by CPU, GPU, RAM, drives)
# =========================================================================

class _UsageBar(QWidget, ThemedWidget):
    """A track + fill pill, themed. Track = border, fill = accent.

    Bar geometry is fully parametrised (height, radius) so the same
    class drives the 8 px CPU/GPU/RAM bars and the 6 px drive bars.
    """

    def __init__(
        self,
        theme_mgr: "ThemeManager",
        *,
        height: int = 8,
        radius: int | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._track_h = int(height)
        self._radius = int(radius) if radius is not None else max(1, height // 2)
        self.setFixedHeight(self._track_h)
        self._percent: float = 0.0
        self._theme: dict[str, str] = {}
        self.connect_theme(theme_mgr)

    def set_percent(self, p: float) -> None:
        p = max(0.0, min(100.0, float(p)))
        if p == self._percent:
            return
        self._percent = p
        self.update()

    def refresh_theme(self, theme: dict[str, str]) -> None:
        self._theme = theme
        self.update()

    def paintEvent(self, event) -> None:   # noqa: ARG002
        if not self._theme:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)

        # Track
        p.setBrush(QColor(self._theme["border"]))
        p.drawRoundedRect(self.rect(), self._radius, self._radius)

        # Fill
        if self._percent > 0.0:
            fill_w = int(round(self.width() * (self._percent / 100.0)))
            if fill_w >= self._radius * 2:
                p.setBrush(QColor(self._theme["accent"]))
                p.drawRoundedRect(
                    0, 0, fill_w, self.height(),
                    self._radius, self._radius,
                )
        p.end()


# =========================================================================
# _HealthDot — 10 px circle in ok/warn/err/text_sec
# =========================================================================

class _HealthDot(QWidget, ThemedWidget):
    """Status indicator for drive SMART health.

    Filled circle for Healthy/Warning/Unhealthy (ok/warn/err palette);
    outlined circle (text_sec) for Unknown — the "we don't know yet"
    signal the user can learn to recognise at a glance.
    """

    _SIZE = 10

    def __init__(
        self,
        theme_mgr: "ThemeManager",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setFixedSize(self._SIZE, self._SIZE)
        self._health: str = "Unknown"
        self._theme: dict[str, str] = {}
        self.connect_theme(theme_mgr)

    def set_health(self, health: str) -> None:
        h = (health or "Unknown").strip()
        if h == self._health:
            return
        self._health = h
        self.update()

    def refresh_theme(self, theme: dict[str, str]) -> None:
        self._theme = theme
        self.update()

    def paintEvent(self, event) -> None:   # noqa: ARG002
        if not self._theme:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        h = self._health.lower()
        if h == "healthy":
            color = QColor(self._theme["ok"])
            filled = True
        elif h == "warning":
            color = QColor(self._theme["warn"])
            filled = True
        elif h == "unhealthy":
            color = QColor(self._theme["err"])
            filled = True
        else:
            color = QColor(self._theme["text_sec"])
            filled = False

        rect = self.rect().adjusted(1, 1, -1, -1)
        if filled:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color)
        else:
            pen = QPen(color)
            pen.setWidthF(1.5)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(rect)
        p.end()


# =========================================================================
# _Divider — 1 px horizontal separator in theme["border"]
# =========================================================================

class _Divider(QFrame, ThemedWidget):
    def __init__(
        self,
        theme_mgr: "ThemeManager",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setFixedHeight(1)
        self._theme: dict[str, str] = {}
        self.connect_theme(theme_mgr)

    def refresh_theme(self, theme: dict[str, str]) -> None:
        self._theme = theme
        self.setStyleSheet(
            f"background-color: {theme['border']}; border: none;"
        )


# =========================================================================
# _ProcessRow — name (elided, left) + size (right)
# =========================================================================

class _ProcessRow(QWidget):
    """One "Top process" row: name left, memory right.

    Sizing format:
        * ``>= 1000 MB``: "{gb:.1f} GB" (1 decimal — "2.1 GB").
        * ``<  1000 MB``: "{mb:.0f} MB" (no decimals — "450 MB").
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedHeight(20)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self._name = _ElidedLabel(self)
        self._name.setFont(_secondary_font())
        self._name.setProperty("role", "primary")

        self._size = QLabel(self)
        self._size.setFont(_secondary_font())
        self._size.setProperty("role", "secondary")
        self._size.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self._size.setMinimumWidth(68)

        layout.addWidget(self._name, 1)
        layout.addWidget(self._size, 0)

    def set_process(self, name: str, memory_mb: float) -> None:
        self._name.setFullText(name)
        if memory_mb >= 1000:
            self._size.setText(f"{memory_mb / 1024:.1f} GB")
        else:
            self._size.setText(f"{memory_mb:.0f} MB")

    def clear(self) -> None:
        self._name.setFullText("")
        self._size.setText("")


# =========================================================================
# MetricCard — generic card (title + optional right widget + content)
# =========================================================================

class MetricCard(QFrame, ThemedWidget):
    _RADIUS = 12
    _OBJECT_NAME = "metricCard"
    _PADDING = 16
    _TITLE_PT = 13

    def __init__(
        self,
        theme_mgr: "ThemeManager",
        title: str,
        *,
        height: int = 140,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName(self._OBJECT_NAME)
        self.setFixedHeight(int(height))
        self._theme: dict[str, str] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(
            self._PADDING, self._PADDING, self._PADDING, self._PADDING,
        )
        root.setSpacing(8)

        self._header = QHBoxLayout()
        self._header.setContentsMargins(0, 0, 0, 0)
        self._header.setSpacing(8)

        self._title_label = QLabel(title)
        tf = QFont()
        tf.setPointSize(self._TITLE_PT)
        tf.setWeight(QFont.Weight.Medium)
        self._title_label.setFont(tf)
        self._title_label.setProperty("role", "secondary")
        self._header.addWidget(self._title_label)
        self._header.addStretch(1)

        self._right_widget: QWidget | None = None
        root.addLayout(self._header)

        self._content_widget: QWidget | None = None
        self._content_container = QWidget(self)
        self._content_container.setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground, True
        )
        self._content_layout = QVBoxLayout(self._content_container)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(6)
        root.addWidget(self._content_container, 1)

        self.connect_theme(theme_mgr)

    # ---- Public API -----------------------------------------------------

    def set_title(self, title: str) -> None:
        self._title_label.setText(title)

    def set_right_widget(self, widget: QWidget | None) -> None:
        """Replace the optional top-right widget. Pass ``None`` to clear."""
        if self._right_widget is not None:
            self._header.removeWidget(self._right_widget)
            self._right_widget.deleteLater()
            self._right_widget = None
        if widget is not None:
            self._right_widget = widget
            self._header.addWidget(widget)
        self._repolish_labels()

    def right_widget(self) -> QWidget | None:
        return self._right_widget

    def set_content(self, widget: QWidget) -> None:
        if self._content_widget is not None:
            self._content_layout.removeWidget(self._content_widget)
            self._content_widget.deleteLater()
        self._content_widget = widget
        self._content_layout.addWidget(widget)
        self._repolish_labels()

    # ---- Themed hook ----------------------------------------------------

    def refresh_theme(self, theme: dict[str, str]) -> None:
        self._theme = theme
        self.setStyleSheet(
            f"""
            QFrame#{self._OBJECT_NAME} {{
                background-color: {theme['card']};
                border-radius: {self._RADIUS}px;
            }}
            QFrame#{self._OBJECT_NAME} QLabel {{
                background: transparent;
                color: {theme['text_sec']};
            }}
            QFrame#{self._OBJECT_NAME} QLabel[role="primary"] {{
                color: {theme['text_pri']};
            }}
            """
        )
        self._repolish_labels()

    # ---- Internal -------------------------------------------------------

    def _repolish_labels(self) -> None:
        for lbl in self.findChildren(QLabel):
            s = lbl.style()
            s.unpolish(lbl)
            s.polish(lbl)


# =========================================================================
# _CpuContent — name + cores + bar
# =========================================================================

class _CpuContent(QWidget):
    def __init__(
        self,
        theme_mgr: "ThemeManager",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._name = _ElidedLabel(self)
        self._name.setFont(_primary_font())
        self._name.setProperty("role", "primary")

        self._cores = QLabel(self)
        self._cores.setFont(_secondary_font())
        self._cores.setProperty("role", "secondary")

        layout.addWidget(self._name)
        layout.addWidget(self._cores)
        layout.addStretch(1)
        self._bar = _UsageBar(theme_mgr, height=8, parent=self)
        layout.addWidget(self._bar)

    def update_snapshot(self, snap: dict) -> float:
        self._name.setFullText(snap.get("cpu_name", "Unknown CPU"))
        cores = snap.get("cpu_cores", {}) or {}
        physical = int(cores.get("physical", 0))
        logical = int(cores.get("logical", 0))
        self._cores.setText(f"{physical}c/{logical}t")
        percent = float(snap.get("cpu_percent", 0.0))
        self._bar.set_percent(percent)
        return percent


# =========================================================================
# _GpuContent — name(s) + bar (or "Usage unavailable")
# =========================================================================

class _GpuContent(QWidget):
    _MAX_VISIBLE_NAMES = 2

    def __init__(
        self,
        theme_mgr: "ThemeManager",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._primary = _ElidedLabel(self)
        self._primary.setFont(_primary_font())
        self._primary.setProperty("role", "primary")

        self._secondary = _ElidedLabel(self)
        self._secondary.setFont(_secondary_font())
        self._secondary.setProperty("role", "secondary")

        layout.addWidget(self._primary)
        layout.addWidget(self._secondary)
        layout.addStretch(1)

        self._bar = _UsageBar(theme_mgr, height=8, parent=self)
        layout.addWidget(self._bar)

        # Shown in the bar's slot when gpu_usage is None (no NVIDIA).
        self._status = QLabel(self)
        self._status.setFont(_secondary_font())
        self._status.setProperty("role", "secondary")
        self._status.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        status_font = self._status.font()
        status_font.setItalic(True)
        self._status.setFont(status_font)
        self._status.setVisible(False)
        layout.addWidget(self._status)

        self._unavailable_text: str = "Usage unavailable"

    # ---- Localised text plumbing ----------------------------------------

    def set_unavailable_text(self, text: str) -> None:
        self._unavailable_text = text or "Usage unavailable"
        if self._status.isVisible():
            self._status.setText(self._unavailable_text)

    # ---- Snapshot update ------------------------------------------------

    def update_snapshot(self, snap: dict) -> float | None:
        """Returns the GPU usage (0..100) or ``None`` if unavailable."""
        names: list[str] = list(snap.get("gpu_names", []) or [])
        if not names:
            self._primary.setFullText("—")
            self._secondary.setVisible(False)
        else:
            self._primary.setFullText(names[0])
            if len(names) >= 2:
                self._secondary.setVisible(True)
                extra = f"+ {names[1]}"
                if len(names) > self._MAX_VISIBLE_NAMES:
                    extra += f"  (+{len(names) - self._MAX_VISIBLE_NAMES} more)"
                self._secondary.setFullText(extra)
            else:
                self._secondary.setVisible(False)

        usage = snap.get("gpu_usage")
        if usage is None:
            self._bar.setVisible(False)
            self._status.setVisible(True)
            self._status.setText(self._unavailable_text)
            return None
        else:
            self._status.setVisible(False)
            self._bar.setVisible(True)
            self._bar.set_percent(float(usage))
            return float(usage)


# =========================================================================
# _RamContent — bar + usage text + divider + top 3 processes
# =========================================================================

class _RamContent(QWidget):
    _TOP_N = 3

    def __init__(
        self,
        theme_mgr: "ThemeManager",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # --- Top: bar + usage text
        self._bar = _UsageBar(theme_mgr, height=8, parent=self)
        self._usage_text = QLabel(self)
        self._usage_text.setFont(_secondary_font())
        self._usage_text.setProperty("role", "secondary")

        layout.addWidget(self._bar)
        layout.addWidget(self._usage_text)

        # --- Divider
        layout.addSpacing(4)
        self._divider = _Divider(theme_mgr, self)
        layout.addWidget(self._divider)
        layout.addSpacing(4)

        # --- Top processes label + N rows
        self._top_label = QLabel("Top 3:", self)
        self._top_label.setFont(_small_medium_font())
        self._top_label.setProperty("role", "secondary")
        layout.addWidget(self._top_label)

        self._process_rows: list[_ProcessRow] = []
        for _ in range(self._TOP_N):
            row = _ProcessRow(self)
            self._process_rows.append(row)
            layout.addWidget(row)

        layout.addStretch(1)

    def set_top_label_text(self, text: str) -> None:
        self._top_label.setText(text)

    def update_snapshot(self, snap: dict) -> float:
        ram = snap.get("ram", {}) or {}
        used = float(ram.get("used_gb", 0.0))
        total = float(ram.get("total_gb", 0.0))
        percent = float(ram.get("percent", 0.0))
        self._bar.set_percent(percent)
        self._usage_text.setText(f"{used:.1f} / {total:.1f} GB")

        procs = snap.get("top_processes", []) or []
        for i, row in enumerate(self._process_rows):
            if i < len(procs):
                pinfo = procs[i]
                row.set_process(
                    str(pinfo.get("name", "")),
                    float(pinfo.get("memory_mb", 0.0)),
                )
            else:
                row.clear()

        return percent


# =========================================================================
# _DriveContent — health dot + label, bar, used/total
# =========================================================================

class _DriveContent(QWidget):
    """Populated via ``set_drive`` + ``set_health_text``. The drive card
    title is set separately via ``MetricCard.set_title`` because that
    string is not localised ("C: NVMe")."""

    def __init__(
        self,
        theme_mgr: "ThemeManager",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # Dot + health label row
        health_row = QHBoxLayout()
        health_row.setContentsMargins(0, 0, 0, 0)
        health_row.setSpacing(8)

        self._dot = _HealthDot(theme_mgr, self)
        self._health_label = QLabel(self)
        self._health_label.setFont(_secondary_font())
        self._health_label.setProperty("role", "primary")

        health_row.addWidget(self._dot, 0, Qt.AlignmentFlag.AlignVCenter)
        health_row.addWidget(self._health_label, 1)
        layout.addLayout(health_row)

        layout.addStretch(1)

        # 6 px bar per spec.
        self._bar = _UsageBar(theme_mgr, height=6, parent=self)
        layout.addWidget(self._bar)

        self._usage_text = QLabel(self)
        self._usage_text.setFont(_secondary_font())
        self._usage_text.setProperty("role", "secondary")
        layout.addWidget(self._usage_text)

    def set_drive(self, drive: dict) -> None:
        self._dot.set_health(str(drive.get("health", "Unknown")))
        self._bar.set_percent(float(drive.get("percent", 0.0)))
        used = float(drive.get("used_gb", 0.0))
        total = float(drive.get("total_gb", 0.0))
        self._usage_text.setText(f"{used:.0f} / {total:.0f} GB")

    def set_health_text(self, text: str) -> None:
        self._health_label.setText(text)


# =========================================================================
# HomePage
# =========================================================================

class HomePage(QWidget, ThemedWidget, LocalizedWidget):
    """Live dashboard (CPU / GPU / RAM+top procs / drives)."""

    _OUTER_MARGIN = 32
    _HEADER_TO_GRID = 24
    _ROW_SPACING = 12

    _TITLE_PT = 24
    _SUBTITLE_PT = 13
    _UPTIME_PT = 13
    _SUBTITLE_SEP = "  \u00b7  "          # " · "

    _CPU_CARD_HEIGHT = 160
    _GPU_CARD_HEIGHT = 160
    _RAM_CARD_HEIGHT = 220
    _DRIVE_CARD_HEIGHT = 140

    _MAX_DRIVES_VISIBLE = 3

    _HEALTH_KEY_MAP = {
        "healthy":   "home.health.healthy",
        "warning":   "home.health.warning",
        "unhealthy": "home.health.unhealthy",
    }   # anything else falls through to "home.health.unknown"

    def __init__(
        self,
        settings: "Settings",
        theme_mgr: "ThemeManager",
        locale_mgr: "LocaleManager",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._theme_mgr = theme_mgr
        self._locale_mgr = locale_mgr

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        # ---- Threading scaffolding --------------------------------------
        self._thread = QThread(self)
        self._worker = HardwareWorker()
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.start)
        self._worker.snapshotReady.connect(self._on_snapshot)
        self._worker.error.connect(self._on_worker_error)
        self._started = False
        self._last_snapshot: dict | None = None
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._shutdown_worker)

        # ---- UI construction --------------------------------------------
        self._build_ui()

        # ---- Mixin wiring (initial refresh_* runs once) -----------------
        self.connect_theme(theme_mgr)
        self.connect_locale(locale_mgr)

    # ---- UI build --------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(
            self._OUTER_MARGIN, self._OUTER_MARGIN,
            self._OUTER_MARGIN, self._OUTER_MARGIN,
        )
        root.setSpacing(8)

        # --- Header row ---------------------------------------------------
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(12)

        self._title = QLabel(self)
        title_font = QFont()
        title_font.setPointSize(self._TITLE_PT)
        title_font.setWeight(QFont.Weight.DemiBold)
        self._title.setFont(title_font)

        self._uptime = QLabel(self)
        uptime_font = QFont()
        uptime_font.setPointSize(self._UPTIME_PT)
        self._uptime.setFont(uptime_font)
        self._uptime.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )

        header.addWidget(self._title)
        header.addStretch(1)
        header.addWidget(self._uptime)
        root.addLayout(header)

        # --- Subtitle (with OS info appended at runtime) -----------------
        self._subtitle = QLabel(self)
        sub_font = QFont()
        sub_font.setPointSize(self._SUBTITLE_PT)
        self._subtitle.setFont(sub_font)
        root.addWidget(self._subtitle)

        root.addSpacing(self._HEADER_TO_GRID)

        # --- Scroll area ------------------------------------------------
        # The content grows with drive count; a scroll area keeps the
        # top row visible on short windows without clipping drives off.
        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground, True
        )
        self._scroll.viewport().setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground, True
        )
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        body = QWidget()
        body.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(self._ROW_SPACING)

        # --- Row 1: CPU + GPU --------------------------------------------
        row1 = QHBoxLayout()
        row1.setContentsMargins(0, 0, 0, 0)
        row1.setSpacing(self._ROW_SPACING)

        # CPU card
        self._cpu_card = MetricCard(
            self._theme_mgr, "CPU", height=self._CPU_CARD_HEIGHT, parent=body,
        )
        self._cpu_content = _CpuContent(self._theme_mgr, self._cpu_card)
        self._cpu_card.set_content(self._cpu_content)
        self._cpu_percent_label = self._make_big_percent_label(self._cpu_card)
        self._cpu_card.set_right_widget(self._cpu_percent_label)
        row1.addWidget(self._cpu_card, 1)

        # GPU card
        self._gpu_card = MetricCard(
            self._theme_mgr, "GPU", height=self._GPU_CARD_HEIGHT, parent=body,
        )
        self._gpu_content = _GpuContent(self._theme_mgr, self._gpu_card)
        self._gpu_card.set_content(self._gpu_content)
        self._gpu_percent_label = self._make_big_percent_label(self._gpu_card)
        self._gpu_card.set_right_widget(self._gpu_percent_label)
        row1.addWidget(self._gpu_card, 1)

        body_layout.addLayout(row1)

        # --- Row 2: RAM (wide) -------------------------------------------
        self._ram_card = MetricCard(
            self._theme_mgr, "RAM", height=self._RAM_CARD_HEIGHT, parent=body,
        )
        self._ram_content = _RamContent(self._theme_mgr, self._ram_card)
        self._ram_card.set_content(self._ram_content)
        self._ram_percent_label = self._make_big_percent_label(self._ram_card)
        self._ram_card.set_right_widget(self._ram_percent_label)
        body_layout.addWidget(self._ram_card)

        # --- Row 3: Drives (up to MAX visible) ---------------------------
        self._drives_row = QHBoxLayout()
        self._drives_row.setContentsMargins(0, 0, 0, 0)
        self._drives_row.setSpacing(self._ROW_SPACING)

        self._drive_cards: list[MetricCard] = []
        self._drive_contents: list[_DriveContent] = []
        for _ in range(self._MAX_DRIVES_VISIBLE):
            card = MetricCard(
                self._theme_mgr, "—",
                height=self._DRIVE_CARD_HEIGHT, parent=body,
            )
            content = _DriveContent(self._theme_mgr, card)
            card.set_content(content)
            card.setVisible(False)   # shown when a drive fills it in
            self._drive_cards.append(card)
            self._drive_contents.append(content)
            self._drives_row.addWidget(card, 1)

        # Trailing stretch so 1–2 drives don't balloon across full width
        # to N/MAX-sized slots — they stay a sensible width.
        self._drives_row.addStretch(1)
        body_layout.addLayout(self._drives_row)

        body_layout.addStretch(1)

        self._scroll.setWidget(body)
        root.addWidget(self._scroll, 1)

    def _make_big_percent_label(self, parent: QWidget) -> QLabel:
        lbl = QLabel("0%", parent)
        lbl.setFont(_big_percent_font())
        lbl.setProperty("role", "primary")
        lbl.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        return lbl

    # ---- Mixin hooks -----------------------------------------------------

    def refresh_theme(self, theme: dict[str, str]) -> None:
        # Page-level labels (title / subtitle / uptime) are plain
        # QLabels not covered by MetricCard's scoped stylesheet — colour
        # them directly.
        self._title.setStyleSheet(
            f"color: {theme['text_pri']}; background: transparent;"
        )
        self._subtitle.setStyleSheet(
            f"color: {theme['text_sec']}; background: transparent;"
        )
        self._uptime.setStyleSheet(
            f"color: {theme['text_sec']}; background: transparent;"
        )

        # Scroll bar: shared style (text_sec handle, text_pri hover).
        self._scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }\n"
            + build_scrollbar_qss(theme)
        )
        # Everything else — cards, bars, dots, divider — refreshes via
        # its own themeChanged connection.

    def refresh_locale(self) -> None:
        tr = self._locale_mgr.tr
        self._title.setText(tr("home.title"))
        self._subtitle.setText(self._compose_subtitle())

        self._cpu_card.set_title(tr("home.card.cpu"))
        self._gpu_card.set_title(tr("home.card.gpu"))
        self._ram_card.set_title(tr("home.card.ram"))

        # Top-processes label (11pt Medium) lives inside _RamContent.
        self._ram_content.set_top_label_text(tr("home.top_processes"))

        # GPU "unavailable" fallback text.
        self._gpu_content.set_unavailable_text(tr("home.gpu_usage_na"))

        # Uptime — pre-snapshot: prefix + "—". Post-snapshot: re-compose.
        if self._last_snapshot is None:
            self._uptime.setText(f"{tr('home.uptime_prefix')} —")
        else:
            self._apply_uptime_text(
                self._last_snapshot.get("uptime_str", "—")
            )

        # Drive cards — refresh health labels from the last snapshot
        # (titles like "C: NVMe" are not localised, so nothing else to
        # re-apply on a locale flip).
        if self._last_snapshot is not None:
            self._apply_drives(
                self._last_snapshot.get("drives", []) or []
            )

    # ---- Worker slots ----------------------------------------------------

    @Slot(dict)
    def _on_snapshot(self, snap: dict) -> None:
        first_snapshot = self._last_snapshot is None
        self._last_snapshot = snap

        # --- CPU
        cpu_pct = self._cpu_content.update_snapshot(snap)
        self._cpu_percent_label.setText(f"{cpu_pct:.0f}%")

        # --- GPU (may return None)
        gpu_pct = self._gpu_content.update_snapshot(snap)
        if gpu_pct is None:
            self._gpu_percent_label.setText("")
            self._gpu_percent_label.setVisible(False)
        else:
            self._gpu_percent_label.setVisible(True)
            self._gpu_percent_label.setText(f"{gpu_pct:.0f}%")

        # --- RAM
        ram_pct = self._ram_content.update_snapshot(snap)
        self._ram_percent_label.setText(f"{ram_pct:.0f}%")

        # --- Uptime
        self._apply_uptime_text(snap.get("uptime_str", "—"))

        # --- Drives
        self._apply_drives(snap.get("drives", []) or [])

        # --- Subtitle: OS info arrives with the first snapshot — rebuild
        # the subtitle once we have it.
        if first_snapshot:
            self._subtitle.setText(self._compose_subtitle())

    @Slot(str)
    def _on_worker_error(self, msg: str) -> None:
        log.error("HardwareWorker reported error: %s", msg)
        # Keep the last good snapshot visible — don't wipe back to "—".

    # ---- Helpers ---------------------------------------------------------

    def _apply_uptime_text(self, uptime_str: str) -> None:
        prefix = self._locale_mgr.tr("home.uptime_prefix")
        self._uptime.setText(f"{prefix} {uptime_str}")

    def _compose_subtitle(self) -> str:
        tr = self._locale_mgr.tr
        base = tr("home.subtitle")
        if self._last_snapshot is None:
            return base
        os_info = self._last_snapshot.get("os_info", {}) or {}
        os_name = str(os_info.get("name") or "").strip()
        build = str(os_info.get("build") or "").strip()
        parts = [base]
        if os_name:
            parts.append(os_name)
        if build and build.lower() != "unknown":
            parts.append(f"Build {build}")
        return self._SUBTITLE_SEP.join(parts)

    def _apply_drives(self, drives: list[dict]) -> None:
        """Populate up to ``_MAX_DRIVES_VISIBLE`` drive cards; hide the
        rest. If more drives exist, log — we add an overflow UI later."""
        tr = self._locale_mgr.tr
        shown = 0
        for i, card in enumerate(self._drive_cards):
            if i < len(drives):
                drive = drives[i]
                letter = str(drive.get("letter", "—"))
                dtype = str(drive.get("type", "Unknown"))
                card.set_title(f"{letter} {dtype}")

                content = self._drive_contents[i]
                content.set_drive(drive)

                health = str(drive.get("health", "Unknown")).strip()
                key = self._HEALTH_KEY_MAP.get(
                    health.lower(), "home.health.unknown"
                )
                content.set_health_text(tr(key))

                card.setVisible(True)
                shown += 1
            else:
                card.setVisible(False)

        if len(drives) > self._MAX_DRIVES_VISIBLE:
            log.info(
                "HomePage: %d drives detected, showing %d (overflow UI TBD).",
                len(drives),
                self._MAX_DRIVES_VISIBLE,
            )

    # ---- Lifecycle -------------------------------------------------------

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._started:
            return
        self._started = True
        log.info("HomePage: starting HardwareWorker thread.")
        self._thread.start()

    def _shutdown_worker(self) -> None:
        if not self._started:
            return
        log.info("HomePage: shutting down HardwareWorker thread.")
        self._worker.stop()
        self._thread.quit()
        if not self._thread.wait(3000):
            log.warning(
                "HardwareWorker thread did not exit within 3 s — forcing."
            )
            self._thread.terminate()
            self._thread.wait(1000)
