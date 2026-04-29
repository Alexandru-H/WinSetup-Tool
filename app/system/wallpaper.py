"""
wallpaper.py — desktop wallpaper control.

Contract (CLAUDE.md §4, §6):
    * ZERO imports from PySide6 — this is the "system" tier. Returns
      are booleans or plain Python values; the UI layer decides how
      to surface them.
    * Every public function swallows its own OSError / WinError
      surface, logs a warning, and returns a safe default so the UI
      can't crash on a transient registry glitch.
    * No desktop side effects at import time. The standalone smoke
      test at the bottom deliberately does NOT call set_wallpaper.

Applying a wallpaper is a two-step operation that mirrors the
Windows Settings flow exactly:

    1. Write the fit mode to ``HKCU\\Control Panel\\Desktop``
       (``WallpaperStyle`` + ``TileWallpaper``, both REG_SZ). These
       values MUST be set before the SPI call — the shell reads them
       during its WM_SETTINGCHANGE handling, not on a schedule.
    2. Call ``SystemParametersInfoW(SPI_SETDESKWALLPAPER)`` with
       ``SPIF_UPDATEINIFILE | SPIF_SENDCHANGE`` so Explorer repaints
       immediately and the choice survives a reboot — no logout
       required.

Run standalone for a read-only smoke test (does NOT change your
desktop):

    python -m app.system.wallpaper
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import winreg
from pathlib import Path

from app.core.logging_setup import get_logger

log = get_logger(__name__)


# --------------------------------------------------------------------------
# Win32 bindings
# --------------------------------------------------------------------------

# ``use_last_error=True`` makes ctypes.get_last_error() actually reflect
# the Win32 GetLastError value after the call. Without this flag the
# CRT overwrites it and we'd log misleading numbers on failure.
_user32 = ctypes.WinDLL("user32", use_last_error=True)

_SystemParametersInfoW = _user32.SystemParametersInfoW
_SystemParametersInfoW.argtypes = (wt.UINT, wt.UINT, wt.LPVOID, wt.UINT)
_SystemParametersInfoW.restype = wt.BOOL

_SPI_SETDESKWALLPAPER = 0x0014
_SPIF_UPDATEINIFILE   = 0x01
_SPIF_SENDCHANGE      = 0x02

_DESKTOP_REG_PATH = r"Control Panel\Desktop"


# --------------------------------------------------------------------------
# Fit mode ↔ registry mapping
# --------------------------------------------------------------------------

# mode_key -> (WallpaperStyle, TileWallpaper), both REG_SZ.
# Reference: the mapping used by the Personalization control panel;
# WallpaperStyle alone is ambiguous for "0" (could be Center or Tile),
# which is why TileWallpaper is also required.
_FIT_MODES: dict[str, tuple[str, str]] = {
    "fill":    ("10", "0"),
    "fit":     ("6",  "0"),
    "stretch": ("2",  "0"),
    "tile":    ("0",  "1"),
    "center":  ("0",  "0"),
    "span":    ("22", "0"),
}

_FIT_MODES_REVERSE: dict[tuple[str, str], str] = {
    v: k for k, v in _FIT_MODES.items()
}

_IMAGE_EXTENSIONS = frozenset({
    ".jpg", ".jpeg", ".png", ".bmp", ".webp",
})


# --------------------------------------------------------------------------
# Apply wallpaper
# --------------------------------------------------------------------------

def set_wallpaper(image_path: str | Path, fit_mode: str = "fill") -> bool:
    """Set the desktop wallpaper.

    Parameters
    ----------
    image_path:
        Absolute or relative path to a .jpg / .jpeg / .png / .bmp /
        .webp file. Paths are resolved with ``Path.resolve`` before
        being handed to Win32. Extension is NOT validated here —
        Windows itself silently ignores unsupported formats, which is
        the behaviour we mirror.
    fit_mode:
        One of ``"fill" | "fit" | "stretch" | "tile" | "center" | "span"``.
        Unknown values are logged and fall back to ``"fill"`` rather
        than failing — the caller has already committed to applying
        *some* wallpaper and a bad fit key shouldn't abort the flow.

    Returns
    -------
    True on success. False on any failure (missing file, registry
    write denied, SPI call refused). Errors are logged at WARNING so
    the UI can surface a generic "Failed" state without needing to
    parse specifics.
    """
    # --- Resolve and validate the path.
    try:
        path = Path(image_path).resolve(strict=False)
    except (OSError, ValueError) as exc:
        log.warning(
            "Cannot resolve wallpaper path %r: %s", image_path, exc
        )
        return False

    if not path.is_file():
        log.warning(
            "Wallpaper path does not exist or is not a file: %s", path
        )
        return False

    # --- Normalise the fit mode.
    mode_key = (fit_mode or "").strip().lower()
    if mode_key not in _FIT_MODES:
        log.warning(
            "Unknown fit mode %r, falling back to 'fill'. Valid: %s",
            fit_mode, ", ".join(sorted(_FIT_MODES)),
        )
        mode_key = "fill"

    style, tile = _FIT_MODES[mode_key]

    # --- Step 1: write the fit mode before the SPI call.
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            _DESKTOP_REG_PATH,
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            winreg.SetValueEx(key, "WallpaperStyle", 0, winreg.REG_SZ, style)
            winreg.SetValueEx(key, "TileWallpaper",  0, winreg.REG_SZ, tile)
    except OSError as exc:
        log.warning(
            "Could not write wallpaper fit mode to HKCU\\%s: %s",
            _DESKTOP_REG_PATH, exc,
        )
        return False

    # --- Step 2: push the image via SPI.
    try:
        ok = _SystemParametersInfoW(
            _SPI_SETDESKWALLPAPER,
            0,
            ctypes.c_wchar_p(str(path)),
            _SPIF_UPDATEINIFILE | _SPIF_SENDCHANGE,
        )
    except OSError as exc:
        log.warning("SystemParametersInfoW call raised: %s", exc)
        return False

    if not ok:
        err = ctypes.get_last_error()
        log.warning(
            "SystemParametersInfoW(SPI_SETDESKWALLPAPER) failed "
            "(GetLastError=%d) for %s", err, path,
        )
        return False

    log.info("Wallpaper applied: %s (mode=%s)", path, mode_key)
    return True


# --------------------------------------------------------------------------
# Read current state
# --------------------------------------------------------------------------

def get_current_wallpaper() -> str | None:
    """Return the path of the current wallpaper, or ``None`` on error.

    Notes
    -----
    The ``Wallpaper`` registry value is sometimes an empty string when
    the user has picked a solid colour instead of an image. We treat
    that as "no wallpaper" and return ``None``.
    """
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            _DESKTOP_REG_PATH,
        ) as key:
            value, _ = winreg.QueryValueEx(key, "Wallpaper")
    except OSError as exc:
        log.warning(
            "Could not read current wallpaper from HKCU\\%s: %s",
            _DESKTOP_REG_PATH, exc,
        )
        return None

    path = str(value or "").strip()
    return path or None


def get_current_fit_mode() -> str:
    """Return the current fit mode key. Defaults to ``"fill"`` on error.

    The registry combination ``(WallpaperStyle, TileWallpaper)`` is
    reverse-mapped through :data:`_FIT_MODES_REVERSE`. Anything we
    don't recognise logs a warning and falls back — the UI still needs
    *some* valid key to pre-select in its dropdown.
    """
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            _DESKTOP_REG_PATH,
        ) as key:
            style_v, _ = winreg.QueryValueEx(key, "WallpaperStyle")
            tile_v,  _ = winreg.QueryValueEx(key, "TileWallpaper")
    except OSError as exc:
        log.warning(
            "Could not read fit mode from HKCU\\%s: %s — defaulting to 'fill'.",
            _DESKTOP_REG_PATH, exc,
        )
        return "fill"

    pair = (str(style_v).strip(), str(tile_v).strip())
    mode = _FIT_MODES_REVERSE.get(pair)
    if mode is None:
        log.warning(
            "Unrecognised fit registry pair "
            "(WallpaperStyle=%r, TileWallpaper=%r) — defaulting to 'fill'.",
            style_v, tile_v,
        )
        return "fill"
    return mode


# --------------------------------------------------------------------------
# Folder helpers
# --------------------------------------------------------------------------

def list_images(folder: str | Path) -> list[Path]:
    """List image files in ``folder`` (non-recursive), sorted A→Z.

    Returns an empty list — never raises — when:
        * the argument is empty / falsy,
        * the path is not a directory,
        * iteration is denied (``PermissionError``),
        * any other OS error surfaces during scan.

    Sorting is case-insensitive so ``alpha.jpg`` and ``Beta.png`` order
    the way a human reads them.
    """
    if not folder:
        return []

    try:
        path = Path(folder)
    except TypeError:
        log.warning("list_images: invalid folder argument %r", folder)
        return []

    if not path.is_dir():
        log.debug("list_images: %s is not a directory.", path)
        return []

    results: list[Path] = []
    try:
        for entry in path.iterdir():
            try:
                if not entry.is_file():
                    continue
            except OSError:
                continue
            if entry.suffix.lower() in _IMAGE_EXTENSIONS:
                results.append(entry)
    except (PermissionError, OSError) as exc:
        log.warning("list_images: cannot scan %s: %s", path, exc)
        return []

    results.sort(key=lambda p: p.name.lower())
    return results


def default_wallpapers_folder() -> Path:
    """Preferred starting folder for the wallpaper gallery.

    Priority:
        1. ``%USERPROFILE%\\Pictures\\Wallpapers`` if it exists
        2. ``%USERPROFILE%\\Pictures`` if it exists
        3. ``%USERPROFILE%``

    We intentionally don't resolve ``FOLDERID_Pictures`` via
    SHGetKnownFolderPath — the added complexity isn't worth it for a
    "starting hint" that the user can override via the Browse button.
    """
    home = Path.home()
    try:
        candidate = home / "Pictures" / "Wallpapers"
        if candidate.is_dir():
            return candidate
        candidate = home / "Pictures"
        if candidate.is_dir():
            return candidate
    except OSError as exc:
        log.debug("default_wallpapers_folder probe failed: %s", exc)
    return home


# --------------------------------------------------------------------------
# Standalone smoke test (read-only — will NOT change your desktop)
# --------------------------------------------------------------------------

if __name__ == "__main__":
    from app.core.logging_setup import setup_logging

    setup_logging()
    log.info("Running wallpaper.py standalone smoke test (read-only).")

    print(f"Current wallpaper : {get_current_wallpaper()}")
    print(f"Current fit mode  : {get_current_fit_mode()}")

    folder = default_wallpapers_folder()
    print(f"Default folder    : {folder}")

    images = list_images(folder)
    print(f"Images in folder  : {len(images)}")
    for img in images[:10]:
        print(f"   - {img.name}")
    if len(images) > 10:
        print(f"   ... and {len(images) - 10} more.")
