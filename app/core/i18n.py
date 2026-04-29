"""
Locale manager — runtime-switchable translations without restart.

Contract (CLAUDE.md §7.4, §8.4):
    * LocaleManager(QObject) is constructed once in main.py and injected
      into widgets that need translations — no module-level singleton.
    * localeChanged(str) fires on successful set_locale(), carrying the
      new locale code. Widgets connect and re-apply their text.
    * Both JSON files are loaded eagerly at __init__. Translations live in
      RAM (a few KB total), so tr() and full-UI refresh are instant.
    * JSON key layout is FLAT with dot-separated segments purely for human
      readability:
            {"nav.home": "Home", "nav.apps": "Apps"}
      Do NOT nest: {"nav": {"home": ...}}.
    * Missing key returns the key itself (so designers immediately see
      "nav.fooBar" on screen and know to add it) plus a log warning.
    * A corrupt translation file is treated as an empty dict with an error
      log — the app still starts, lookups fall through to the fallback.
"""

from __future__ import annotations

import json
from typing import Any

from PySide6.QtCore import QObject, Signal

from app.core.logging_setup import get_logger
from app.core.paths import RESOURCES_DIR
from app.core.settings import Settings

log = get_logger(__name__)

SUPPORTED_LOCALES: tuple[str, ...] = ("ro", "en")
FALLBACK_LOCALE: str = "en"
_TRANSLATIONS_SUBDIR = "translations"


class LocaleManager(QObject):
    """Holds translation tables and the currently active locale."""

    localeChanged = Signal(str)

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self._settings = settings
        self._translations: dict[str, dict[str, str]] = {}
        self._load_all()

        requested = settings.get("locale", FALLBACK_LOCALE)
        if requested not in SUPPORTED_LOCALES:
            log.warning(
                "Settings locale %r is not supported — falling back to %r.",
                requested,
                FALLBACK_LOCALE,
            )
            requested = FALLBACK_LOCALE
        self._current: str = requested

    # ------------------------------------------------------------------ load

    def _load_all(self) -> None:
        base = RESOURCES_DIR / _TRANSLATIONS_SUBDIR
        for code in SUPPORTED_LOCALES:
            path = base / f"{code}.json"
            try:
                with path.open("r", encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, json.JSONDecodeError) as exc:
                log.error(
                    "Could not load translation file %s (%s) — "
                    "treating as empty.",
                    path,
                    exc,
                )
                self._translations[code] = {}
                continue

            if not isinstance(data, dict):
                log.error(
                    "Translation file %s is not a JSON object — "
                    "treating as empty.",
                    path,
                )
                self._translations[code] = {}
                continue

            self._translations[code] = {k: str(v) for k, v in data.items()}
            log.debug(
                "Loaded %d translation keys for %r.",
                len(self._translations[code]),
                code,
            )

    # ------------------------------------------------------------------- api

    def current(self) -> str:
        return self._current

    def available(self) -> list[str]:
        return list(SUPPORTED_LOCALES)

    def tr(self, key: str, **fmt: Any) -> str:
        """Translate a key, with optional str.format substitutions."""
        current_dict = self._translations.get(self._current, {})
        template = current_dict.get(key)

        if template is None:
            fallback_dict = self._translations.get(FALLBACK_LOCALE, {})
            template = fallback_dict.get(key)
            if template is None:
                log.warning(
                    "Missing translation for key %r (locale=%r).",
                    key,
                    self._current,
                )
                return key
            log.debug(
                "Translation for %r missing in %r — using %r fallback.",
                key,
                self._current,
                FALLBACK_LOCALE,
            )

        if not fmt:
            return template

        try:
            return template.format(**fmt)
        except KeyError as exc:
            log.error(
                "Format substitution failed for key %r "
                "(template=%r, fmt=%r): missing %s",
                key,
                template,
                fmt,
                exc,
            )
            return template

    def set_locale(self, code: str) -> None:
        """Switch to another locale, persist to settings, emit localeChanged."""
        if code == self._current:
            return
        if code not in SUPPORTED_LOCALES:
            log.error(
                "Cannot switch to unknown locale %r (supported: %s).",
                code,
                ", ".join(SUPPORTED_LOCALES),
            )
            return

        self._current = code
        self._settings.set("locale", code)
        self._settings.save()
        log.info("Locale switched to %r.", code)
        self.localeChanged.emit(code)
