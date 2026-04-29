"""Detect residual files and registry keys after an app is uninstalled.

Strategy:
    1. Build candidate path list from app metadata (display_name,
       publisher) using common Windows installation conventions.
    2. Check existence of each candidate inside the safe parent dirs.
    3. Return categorized results: filesystem paths + registry keys.

Safety constraints:
    - Only scans inside %AppData%, %LocalAppData%, %ProgramData%.
      Never touches Windows, System32, or Program Files sub-trees.
    - Filesystem candidates must exactly match app name or publisher
      (case-insensitive). No fuzzy matching.
    - Candidate path must resolve to a path that starts with the safe
      parent (symlink / traversal guard).
    - Registry: only HKCU\\Software\\<name> and HKLM\\Software\\<name>.
      Skips keys with > 50 sub-keys (likely a publisher-level key that
      would contain many unrelated apps).
"""

from __future__ import annotations

import os
import re
import winreg
from dataclasses import dataclass
from pathlib import Path

from app.core.logging_setup import get_logger
from app.system.uninstaller.scanner import InstalledApp

log = get_logger(__name__)


@dataclass(frozen=True)
class LeftoverItem:
    """One leftover detected after uninstall."""
    kind: str           # "folder" | "file" | "regkey"
    path: str           # filesystem path or registry key path
    size_bytes: int     # summed size; 0 for registry keys
    description: str
    risky: bool         # True if removal might affect other apps


# ---------------------------------------------------------------------------
# Filesystem scan
# ---------------------------------------------------------------------------

def _safe_parent_dirs() -> list[Path]:
    """Returns safe parent directories to scan. Never includes system paths."""
    parents = []
    for env_var in ("APPDATA", "LOCALAPPDATA", "PROGRAMDATA"):
        val = os.getenv(env_var, "")
        if val:
            p = Path(val)
            if p.exists():
                parents.append(p)
    return parents


def _name_candidates(app: InstalledApp) -> list[str]:
    """Generate exact-match candidate folder/key names from app metadata."""
    candidates: set[str] = set()

    if app.display_name:
        base = re.sub(r"\s+\(.+?\)$", "", app.display_name).strip()
        base = re.sub(r"\s+v?\d+(\.\d+)*$", "", base).strip()
        if base:
            candidates.add(base)

    if app.publisher:
        candidates.add(app.publisher.strip())

    _BLACKLIST = {
        "microsoft", "google", "the", "company", "inc", "ltd",
        "corp", "corporation", "software",
    }
    return sorted(
        c for c in candidates
        if c and c.lower() not in _BLACKLIST and len(c) >= 3
    )


def _folder_size_bytes(path: Path, max_files: int = 5000) -> int:
    """Sum file sizes under path, capped at max_files to avoid hangs."""
    total = 0
    count = 0
    try:
        for root, _dirs, files in os.walk(path):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
                count += 1
                if count >= max_files:
                    return total
    except OSError:
        pass
    return total


def _scan_filesystem(app: InstalledApp) -> list[LeftoverItem]:
    items: list[LeftoverItem] = []
    parents = _safe_parent_dirs()
    candidates = _name_candidates(app)

    for parent in parents:
        for name in candidates:
            target = parent / name
            try:
                if not target.exists():
                    continue
            except OSError:
                continue

            # Guard against symlink traversal or malformed paths.
            try:
                resolved = target.resolve()
                resolved_parent = parent.resolve()
                if not str(resolved).startswith(str(resolved_parent)):
                    log.warning("Skipping suspicious path: %s", target)
                    continue
            except OSError:
                continue

            if target.is_dir():
                size = _folder_size_bytes(target)
                items.append(LeftoverItem(
                    kind="folder",
                    path=str(target),
                    size_bytes=size,
                    description=f"User data folder for {app.display_name}",
                    risky=False,
                ))
            elif target.is_file():
                try:
                    size = target.stat().st_size
                except OSError:
                    size = 0
                items.append(LeftoverItem(
                    kind="file",
                    path=str(target),
                    size_bytes=size,
                    description=f"File for {app.display_name}",
                    risky=False,
                ))
    return items


# ---------------------------------------------------------------------------
# Registry scan
# ---------------------------------------------------------------------------

_SAFE_REG_PARENTS = [
    (winreg.HKEY_CURRENT_USER, r"Software"),
    (winreg.HKEY_LOCAL_MACHINE, r"Software"),
    (winreg.HKEY_LOCAL_MACHINE, r"Software\WoW6432Node"),
]

# Publisher names whose Software subkey would be a broad shared namespace.
# Matching at this level would return all sub-apps as one giant leftover.
_GENERIC_REG_PARENTS = {
    "microsoft", "google", "adobe", "intel", "amd", "nvidia",
    "valve", "classes", "policies", "wow6432node", "windows",
}


def _count_subkeys(key, max_count: int = 60) -> int:
    count = 0
    try:
        while count < max_count:
            try:
                winreg.EnumKey(key, count)
                count += 1
            except OSError:
                break
    except OSError:
        pass
    return count


def _scan_registry(app: InstalledApp) -> list[LeftoverItem]:
    items: list[LeftoverItem] = []
    candidates = _name_candidates(app)

    for hroot, parent_path in _SAFE_REG_PARENTS:
        for name in candidates:
            if name.lower() in _GENERIC_REG_PARENTS:
                continue

            full_path = f"{parent_path}\\{name}"
            try:
                with winreg.OpenKeyEx(hroot, full_path, 0, winreg.KEY_READ) as key:
                    subkey_count = _count_subkeys(key)
                    if subkey_count > 50:
                        log.info(
                            "Skipping reg %s — too many subkeys (%d)",
                            full_path, subkey_count,
                        )
                        continue

                    hive_name = (
                        "HKCU" if hroot == winreg.HKEY_CURRENT_USER else "HKLM"
                    )
                    items.append(LeftoverItem(
                        kind="regkey",
                        path=f"{hive_name}\\{full_path}",
                        size_bytes=0,
                        description=f"Registry key with {subkey_count} subkeys",
                        risky=(subkey_count > 10),
                    ))
            except FileNotFoundError:
                continue
            except OSError:
                continue
    return items


# ---------------------------------------------------------------------------
# Removal
# ---------------------------------------------------------------------------

def _delete_reg_tree(hroot, path: str) -> None:
    """Recursively delete a registry key tree."""
    try:
        with winreg.OpenKeyEx(hroot, path, 0, winreg.KEY_READ) as key:
            sub_names = []
            i = 0
            while True:
                try:
                    sub_names.append(winreg.EnumKey(key, i))
                    i += 1
                except OSError:
                    break
        for sub in sub_names:
            _delete_reg_tree(hroot, f"{path}\\{sub}")
        winreg.DeleteKey(hroot, path)
    except FileNotFoundError:
        pass


def remove_leftover(item: LeftoverItem) -> bool:
    """Remove a single leftover item. Returns True on success or if already gone."""
    import shutil

    log.info("Removing leftover: [%s] %s", item.kind, item.path)
    try:
        if item.kind == "folder":
            shutil.rmtree(item.path, ignore_errors=False)
        elif item.kind == "file":
            os.remove(item.path)
        elif item.kind == "regkey":
            parts = item.path.split("\\", 1)
            if len(parts) != 2:
                log.error("Malformed regkey path: %s", item.path)
                return False
            hive_str, sub = parts
            hroot = (
                winreg.HKEY_CURRENT_USER
                if hive_str == "HKCU"
                else winreg.HKEY_LOCAL_MACHINE
            )
            _delete_reg_tree(hroot, sub)
        else:
            log.warning("Unknown leftover kind: %s", item.kind)
            return False
        return True
    except FileNotFoundError:
        return True  # already gone
    except Exception as e:
        log.error("Failed to remove %s: %s", item.path, e)
        return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def find_leftovers(app: InstalledApp) -> list[LeftoverItem]:
    """Scan for residual files and registry entries after uninstall of `app`.

    Returns items sorted: folders first (largest first), then files,
    then registry keys.
    """
    log.info("Scanning leftovers for: %s", app.display_name)
    items = _scan_filesystem(app) + _scan_registry(app)

    def _sort_key(item: LeftoverItem) -> tuple:
        kind_order = {"folder": 0, "file": 1, "regkey": 2}.get(item.kind, 3)
        return (kind_order, -item.size_bytes)

    items.sort(key=_sort_key)
    log.info("Found %d leftover items", len(items))
    return items


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from app.core.logging_setup import setup_logging
    from app.system.uninstaller.scanner import scan_installed_apps
    setup_logging()
    apps = scan_installed_apps()
    if not apps:
        print("No installed apps found — cannot test leftover scanner.")
    else:
        sample = apps[0]
        print(f"Scanning leftovers for: {sample.display_name}")
        leftovers = find_leftovers(sample)
        if not leftovers:
            print("  (no leftovers found — expected on clean system)")
        for item in leftovers:
            kb = item.size_bytes // 1024 if item.size_bytes else 0
            print(f"  [{item.kind:6s}] {kb:8d} KB  {item.path}")
