"""Enumerate installed applications from registry + winget.

Sources:
    1. HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall
    2. HKCU\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall
    3. HKLM\\SOFTWARE\\WoW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall
    4. winget list (best-effort, used for enrichment only)

Deduplication strategy:
    Primary key: (display_name_normalized, publisher_normalized).
    If two registry entries match on this key, the one with a valid
    UninstallString wins; ties broken by InstallLocation presence;
    ties broken by HKLM > HKCU > WoW6432Node priority.
    winget is used to enrich existing entries with a 'winget_id'
    field when names match — NOT to add new entries.

Filtering (excluded by default):
    - Entries where SystemComponent=1 (DWORD)
    - DisplayName starts with "KB", "Security Update", "Update for"
    - Publisher == "Microsoft Corporation" AND DisplayName matches
      common runtime patterns (VC++, .NET, EdgeWebView2, SDK, Drivers)
    - DisplayName empty/missing (orphaned entries)
    - WindowsInstaller=1 AND ParentDisplayName set (sub-component)
"""

from __future__ import annotations

import re
import subprocess
import winreg
from dataclasses import dataclass
from datetime import datetime

from app.core.logging_setup import get_logger

log = get_logger(__name__)

try:
    CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW
except AttributeError:
    CREATE_NO_WINDOW = 0


@dataclass(frozen=True)
class InstalledApp:
    """A single installed application detected on the system."""
    display_name: str
    publisher: str
    version: str
    install_date: str           # ISO YYYY-MM-DD if parseable, else ""
    install_location: str
    uninstall_string: str
    quiet_uninstall_string: str
    estimated_size_kb: int      # 0 if unknown
    source_hive: str            # "HKLM" / "HKCU" / "HKLM_WOW64"
    registry_key: str           # full path to the uninstall key
    winget_id: str = ""         # filled by enrichment; "" if not found


# ---------------------------------------------------------------------------
# Filter patterns (system noise)
# ---------------------------------------------------------------------------

_RUNTIME_NAME_PATTERNS = [
    re.compile(r"^Microsoft Visual C\+\+ \d{4} Redistributable", re.I),
    re.compile(r"^Microsoft Visual C\+\+ \d{4}-\d{4} Redistributable", re.I),
    re.compile(r"^Microsoft \.NET \d", re.I),
    re.compile(r"^Microsoft Edge WebView2 Runtime", re.I),
    re.compile(r"^Windows SDK", re.I),
    re.compile(r"^Windows Software Development Kit", re.I),
    re.compile(r"^Microsoft Windows Driver", re.I),
]

_KB_PREFIXES = ("KB", "Security Update", "Update for")


def _is_system_component(name: str, publisher: str,
                          system_component_flag: int) -> bool:
    """Heuristic: should this registry entry be hidden from users?"""
    if system_component_flag == 1:
        return True
    if not name:
        return True
    if any(name.startswith(p) for p in _KB_PREFIXES):
        return True
    if publisher == "Microsoft Corporation":
        for pattern in _RUNTIME_NAME_PATTERNS:
            if pattern.search(name):
                return True
    return False


# ---------------------------------------------------------------------------
# Registry enumeration
# ---------------------------------------------------------------------------

_UNINSTALL_PATHS = [
    (winreg.HKEY_LOCAL_MACHINE,
     r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
     "HKLM"),
    (winreg.HKEY_CURRENT_USER,
     r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
     "HKCU"),
    (winreg.HKEY_LOCAL_MACHINE,
     r"SOFTWARE\WoW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
     "HKLM_WOW64"),
]


def _read_str(key, name: str, default: str = "") -> str:
    try:
        v, _ = winreg.QueryValueEx(key, name)
        return str(v) if v is not None else default
    except (FileNotFoundError, OSError):
        return default


def _read_dword(key, name: str, default: int = 0) -> int:
    try:
        v, _ = winreg.QueryValueEx(key, name)
        return int(v) if v is not None else default
    except (FileNotFoundError, OSError):
        return default


def _parse_install_date(raw: str) -> str:
    """Convert registry InstallDate (usually YYYYMMDD) to ISO YYYY-MM-DD."""
    if not raw:
        return ""
    raw = raw.strip()
    if len(raw) == 8 and raw.isdigit():
        try:
            return datetime.strptime(raw, "%Y%m%d").strftime("%Y-%m-%d")
        except ValueError:
            return ""
    return raw


def _enum_one_path(hroot, path: str, hive_label: str) -> list[InstalledApp]:
    apps: list[InstalledApp] = []
    try:
        with winreg.OpenKeyEx(hroot, path, 0, winreg.KEY_READ) as root:
            i = 0
            while True:
                try:
                    subkey_name = winreg.EnumKey(root, i)
                    i += 1
                except OSError:
                    break

                full_subpath = f"{path}\\{subkey_name}"
                try:
                    with winreg.OpenKeyEx(
                        hroot, full_subpath, 0, winreg.KEY_READ
                    ) as sub:
                        name = _read_str(sub, "DisplayName")
                        publisher = _read_str(sub, "Publisher")
                        version = _read_str(sub, "DisplayVersion")
                        install_date = _parse_install_date(
                            _read_str(sub, "InstallDate")
                        )
                        install_location = _read_str(sub, "InstallLocation")
                        uninstall_string = _read_str(sub, "UninstallString")
                        quiet_uninstall = _read_str(sub, "QuietUninstallString")
                        size_kb = _read_dword(sub, "EstimatedSize")
                        sys_comp = _read_dword(sub, "SystemComponent")
                        parent = _read_str(sub, "ParentDisplayName")
                        win_inst = _read_dword(sub, "WindowsInstaller")
                except OSError:
                    continue

                if not name:
                    continue
                if _is_system_component(name, publisher, sys_comp):
                    continue
                if win_inst == 1 and parent:
                    continue

                apps.append(InstalledApp(
                    display_name=name,
                    publisher=publisher,
                    version=version,
                    install_date=install_date,
                    install_location=install_location,
                    uninstall_string=uninstall_string,
                    quiet_uninstall_string=quiet_uninstall,
                    estimated_size_kb=size_kb,
                    source_hive=hive_label,
                    registry_key=full_subpath,
                ))
    except FileNotFoundError:
        log.warning("Uninstall path not found: %s\\%s", hive_label, path)
    except OSError as e:
        log.error("Enum error on %s\\%s: %s", hive_label, path, e)
    return apps


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def _normalize(s: str) -> str:
    """Canonicalize a name for dedup: lowercase, collapse spaces,
    strip trailing version patterns and parentheticals."""
    s = s.lower().strip()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"\s+\d+(\.\d+)+(\s*\(.*?\))?\s*$", "", s)
    s = re.sub(r"\s*\([^)]*\)\s*$", "", s)
    return s.strip()


def _dedup_priority(app: InstalledApp) -> tuple:
    """Lower tuple = higher priority (sorted ascending)."""
    has_uninstall = 0 if app.uninstall_string else 1
    has_location = 0 if app.install_location else 1
    hive_rank = {"HKLM": 0, "HKCU": 1, "HKLM_WOW64": 2}.get(app.source_hive, 3)
    return (has_uninstall, has_location, hive_rank)


def _dedup(apps: list[InstalledApp]) -> list[InstalledApp]:
    """Group by (normalized_name, normalized_publisher); keep best entry."""
    groups: dict[tuple, list[InstalledApp]] = {}
    for a in apps:
        key = (_normalize(a.display_name), _normalize(a.publisher))
        groups.setdefault(key, []).append(a)

    result = []
    for group in groups.values():
        if len(group) == 1:
            result.append(group[0])
        else:
            group.sort(key=_dedup_priority)
            result.append(group[0])
    return result


# ---------------------------------------------------------------------------
# winget enrichment
# ---------------------------------------------------------------------------

def _winget_list() -> dict[str, str]:
    """Run `winget list` and return {normalized_name: winget_id}.
    Best-effort: returns {} if winget is unavailable or parse fails."""
    try:
        result = subprocess.run(
            ["winget", "list", "--accept-source-agreements"],
            capture_output=True, text=True, timeout=20,
            creationflags=CREATE_NO_WINDOW,
            encoding="utf-8", errors="replace",
        )
        if result.returncode != 0:
            return {}
        return _parse_winget_list(result.stdout)
    except Exception as e:
        log.warning("winget list failed: %s", e)
        return {}


def _parse_winget_list(output: str) -> dict[str, str]:
    """Parse `winget list` table output into {normalized_name: id}.
    Locates column offsets from the header line."""
    lines = output.splitlines()
    header_idx = -1
    for i, line in enumerate(lines):
        if "Name" in line and "Id" in line and "Version" in line:
            header_idx = i
            break
    if header_idx < 0 or header_idx + 1 >= len(lines):
        return {}

    header = lines[header_idx]
    try:
        name_col = header.index("Name")
        id_col = header.index("Id")
        version_col = header.index("Version")
    except ValueError:
        return {}

    result = {}
    for line in lines[header_idx + 2:]:  # skip dashes line
        if not line.strip():
            continue
        try:
            name = line[name_col:id_col].strip()
            wid = line[id_col:version_col].strip()
            if name and wid and not wid.startswith("-"):
                result[_normalize(name)] = wid
        except IndexError:
            continue
    return result


def _enrich_with_winget(apps: list[InstalledApp]) -> list[InstalledApp]:
    """Fill in winget_id where name matches winget list. Best-effort."""
    winget_map = _winget_list()
    if not winget_map:
        return apps
    enriched = []
    for app in apps:
        norm = _normalize(app.display_name)
        wid = winget_map.get(norm, "")
        if wid:
            enriched.append(InstalledApp(
                display_name=app.display_name,
                publisher=app.publisher,
                version=app.version,
                install_date=app.install_date,
                install_location=app.install_location,
                uninstall_string=app.uninstall_string,
                quiet_uninstall_string=app.quiet_uninstall_string,
                estimated_size_kb=app.estimated_size_kb,
                source_hive=app.source_hive,
                registry_key=app.registry_key,
                winget_id=wid,
            ))
        else:
            enriched.append(app)
    return enriched


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scan_installed_apps() -> list[InstalledApp]:
    """Enumerate all user-installed applications.

    Returns a deduplicated, filtered list enriched with winget IDs
    where available. Gracefully degrades without admin rights.
    Typical execution time: 1–3 seconds.
    """
    log.info("Scanning installed apps from registry...")
    raw: list[InstalledApp] = []
    for hroot, path, label in _UNINSTALL_PATHS:
        raw.extend(_enum_one_path(hroot, path, label))
    log.info("Raw entries from registry: %d", len(raw))

    deduped = _dedup(raw)
    log.info("After dedup: %d", len(deduped))

    enriched = _enrich_with_winget(deduped)
    matched = sum(1 for a in enriched if a.winget_id)
    log.info("Winget enrichment matched: %d / %d", matched, len(enriched))

    return enriched


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from app.core.logging_setup import setup_logging
    setup_logging()
    apps = scan_installed_apps()
    print(f"Found {len(apps)} installed apps:")
    for a in apps[:20]:
        print(f"  {a.display_name:40s} | {a.publisher:25s} | "
              f"{a.version:15s} | {a.install_date} | "
              f"{a.estimated_size_kb}KB | winget={a.winget_id or '-'}")
    if len(apps) > 20:
        print(f"  ... and {len(apps) - 20} more")
