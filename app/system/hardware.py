"""
hardware.py — system introspection: CPU, RAM, GPU, OS, uptime.

Contract (CLAUDE.md §4.1, §6.9):
    * ZERO imports from PySide6 — this is the "system" tier, not UI.
      Returns are plain Python types (dict, list, str, int, float,
      timedelta). The caller (home_page.py later) renders them however
      it likes.
    * Zero side effects at import time. Every metric is produced by an
      explicit function call — no background thread, no module-level
      probe.
    * GPU detection uses the registry enum of the Display adapter class
      ``{4d36e968-e325-11ce-bfc1-08002be10318}`` because ``wmic`` is
      missing on Windows 11 IoT LTSC (see §6.9).
    * Each probe swallows its own errors, logs a warning, and returns
      a safe default. The home page must never crash just because a
      metric momentarily fails to read.

Run standalone for a quick smoke test:
    python -m app.system.hardware
"""

from __future__ import annotations

import json
import platform
import re
import subprocess
import sys
import time as _time
import winreg
from datetime import datetime, timedelta

import psutil

from app.core.logging_setup import get_logger

# Hide the console window when spawning PowerShell / nvidia-smi from a
# GUI process. Without this flag a black cmd.exe flashes on screen for
# the duration of every subprocess call — ugly and distracting.
_HIDDEN_CONSOLE_FLAGS = (
    subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
)

log = get_logger(__name__)


# --------------------------------------------------------------------------
# CPU
# --------------------------------------------------------------------------

# Marketing-suffix trims applied to long CPU names so they fit a
# dashboard card cleanly. Order matters — patterns are tried in
# sequence, each one gated by a 15-char minimum so we never strip the
# name down to something unrecognisable.
_CPU_SUFFIX_PATTERNS = (
    # " 8-Core Processor", " 16-Core Processor", ...
    re.compile(r"\s+\d+-Core\s+Processor$", re.IGNORECASE),
    # " CPU @ 3.80GHz", " CPU @ 2.4 GHz", ...
    re.compile(r"\s+CPU\s*@\s*[\d.]+\s*GHz$", re.IGNORECASE),
    # Trailing bare " Processor"
    re.compile(r"\s+Processor$", re.IGNORECASE),
)


def _trim_cpu_marketing_suffix(name: str) -> str:
    """Strip verbose marketing suffixes if the name is too long to fit.

    Gates:
        * ``len(name) <= 30`` — already short enough, return as-is.
        * A candidate trim that drops the result below 15 chars is
          skipped (we'd rather keep "AMD Ryzen 5" than "AMD R" if a
          regex goes wrong).

    Examples:
        "AMD Ryzen 7 7700X 8-Core Processor"   -> "AMD Ryzen 7 7700X"
        "Intel(R) Core(TM) i7-10700K CPU @ 3.80GHz"
                                              -> "Intel(R) Core(TM) i7-10700K"
        "Intel Core i5"                        -> "Intel Core i5" (unchanged)
    """
    if len(name) <= 30:
        return name

    trimmed = name
    for pattern in _CPU_SUFFIX_PATTERNS:
        candidate = pattern.sub("", trimmed).strip()
        if len(candidate) >= 15:
            trimmed = candidate
    return trimmed


def cpu_name() -> str:
    """Return the friendly CPU name, or "Unknown CPU" if detection fails.

    Order (reversed per 2026-04-20 review):
        1. Registry ``HKLM\\HARDWARE\\DESCRIPTION\\System\\CentralProcessor\\0``
           value ``ProcessorNameString`` — the marketing line, e.g.
           ``"AMD Ryzen 7 7700X 8-Core Processor"``. Long names are
           passed through :func:`_trim_cpu_marketing_suffix` so the
           dashboard card stays legible.
        2. ``platform.processor()`` — on Windows this surfaces the
           engineering ``PROCESSOR_IDENTIFIER`` string
           (``"AMD64 Family 25 Model 97 Stepping 2, AuthenticAMD"``).
           Ugly, but better than "Unknown CPU". Emits a warning so we
           know the primary path failed.
        3. ``"Unknown CPU"`` with a warning.
    """
    # --- Primary: registry marketing name.
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "ProcessorNameString")
            if value:
                raw = str(value).strip()
                if raw:
                    return _trim_cpu_marketing_suffix(raw)
    except OSError as exc:
        log.warning(
            "Could not read ProcessorNameString from registry: %s", exc
        )

    # --- Fallback: engineering string from platform module.
    try:
        name = platform.processor()
        if name and name.strip():
            log.warning(
                "Using fallback CPU name format (platform.processor) — "
                "registry primary path failed or returned empty."
            )
            return name.strip()
    except Exception as exc:   # noqa: BLE001 — platform surface is loose
        log.debug("platform.processor() failed: %s", exc)

    log.warning("CPU name could not be determined; returning 'Unknown CPU'.")
    return "Unknown CPU"


def cpu_percent(interval: float = 0.1) -> float:
    """Overall CPU utilisation percentage. 0.0 on error.

    The small default ``interval`` (100 ms) keeps the home page snappy;
    callers that want a more stable reading can pass a longer window.
    """
    try:
        return float(psutil.cpu_percent(interval=interval))
    except Exception as exc:   # noqa: BLE001 — psutil surface varies
        log.warning("psutil.cpu_percent failed: %s", exc)
        return 0.0


def cpu_core_count() -> dict[str, int]:
    """Physical and logical core counts. 0 for any unknown value."""
    try:
        physical = psutil.cpu_count(logical=False) or 0
        logical = psutil.cpu_count(logical=True) or 0
        return {"physical": int(physical), "logical": int(logical)}
    except Exception as exc:   # noqa: BLE001
        log.warning("psutil.cpu_count failed: %s", exc)
        return {"physical": 0, "logical": 0}


# --------------------------------------------------------------------------
# RAM
# --------------------------------------------------------------------------

_GB = 1024 ** 3


def ram_info() -> dict:
    """Virtual memory snapshot.

    Keys: ``total_gb``, ``used_gb``, ``available_gb`` (floats, 2 decimals),
    ``percent`` (float, psutil-reported usage percentage).
    Zeroed dict on error so the UI can render "0 / 0 GB" rather than
    crashing.
    """
    try:
        vm = psutil.virtual_memory()
        return {
            "total_gb":     round(vm.total     / _GB, 2),
            "used_gb":      round(vm.used      / _GB, 2),
            "available_gb": round(vm.available / _GB, 2),
            "percent":      float(vm.percent),
        }
    except Exception as exc:   # noqa: BLE001
        log.warning("psutil.virtual_memory failed: %s", exc)
        return {
            "total_gb":     0.0,
            "used_gb":      0.0,
            "available_gb": 0.0,
            "percent":      0.0,
        }


# --------------------------------------------------------------------------
# GPU — registry-only enum (wmic is missing on Windows 11 IoT LTSC, §6.9)
# --------------------------------------------------------------------------

_DISPLAY_CLASS_KEY = (
    r"SYSTEM\CurrentControlSet\Control\Class"
    r"\{4d36e968-e325-11ce-bfc1-08002be10318}"
)

# Substrings treated as generic fallback adapters. The registry usually
# lists them alongside the real card; we keep them in the output (a
# machine with ONLY a basic adapter should still show something) but
# push them to the end so the real card reads first.
_GENERIC_HINTS = (
    "microsoft basic display adapter",
    "microsoft remote display adapter",
    "microsoft hyper-v video",
)


def gpu_names() -> list[str]:
    """Enumerate display adapters from the Class key (§6.9).

    We walk ``HKLM\\SYSTEM\\CurrentControlSet\\Control\\Class\\{GUID}``
    and read ``DriverDesc`` from each four-digit instance subkey
    (``0000``, ``0001``, ...). Duplicates are collapsed while preserving
    first-seen order; generic Microsoft fallback adapters are sorted
    to the end.
    """
    names: list[str] = []
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, _DISPLAY_CLASS_KEY
        ) as parent:
            i = 0
            while True:
                try:
                    subkey_name = winreg.EnumKey(parent, i)
                except OSError:
                    break
                i += 1
                # Only the 4-digit instance subkeys carry DriverDesc.
                # Skip "Properties" and any other non-numeric siblings.
                if not (len(subkey_name) == 4 and subkey_name.isdigit()):
                    continue
                try:
                    with winreg.OpenKey(parent, subkey_name) as child:
                        desc, _ = winreg.QueryValueEx(child, "DriverDesc")
                        desc_s = str(desc).strip()
                        if desc_s and desc_s not in names:
                            names.append(desc_s)
                except OSError:
                    # Subkey without DriverDesc — skip silently.
                    continue
    except OSError as exc:
        log.warning("Could not enumerate display adapters: %s", exc)
        return []

    def _is_generic(n: str) -> bool:
        n_low = n.lower()
        return any(h in n_low for h in _GENERIC_HINTS)

    # Stable sort — False (real cards) stays before True (generic).
    names.sort(key=_is_generic)
    return names


# --------------------------------------------------------------------------
# Uptime
# --------------------------------------------------------------------------

def uptime() -> timedelta:
    """Time since last boot. ``timedelta(0)`` on error."""
    try:
        boot = datetime.fromtimestamp(psutil.boot_time())
        return datetime.now() - boot
    except Exception as exc:   # noqa: BLE001
        log.warning("psutil.boot_time failed: %s", exc)
        return timedelta(0)


def uptime_str(td: timedelta | None = None) -> str:
    """Human-readable uptime — days, hours, minutes. No seconds.

    Examples:
        "3d 14h 22m"   (>= 1 day)
        "14h 22m"      (>= 1 hour, < 1 day)
        "22m"          (< 1 hour)
        "0m"           (system just booted or uptime() returned zero)

    If ``td`` is ``None``, calls :func:`uptime` internally.
    """
    if td is None:
        td = uptime()

    total_seconds = int(td.total_seconds())
    if total_seconds < 0:
        total_seconds = 0

    days, rem = divmod(total_seconds, 86_400)
    hours, rem = divmod(rem, 3_600)
    minutes = rem // 60

    if days > 0:
        return f"{days}d {hours}h {minutes}m"
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


# --------------------------------------------------------------------------
# OS
# --------------------------------------------------------------------------

_CURRENT_VERSION_KEY = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion"


def os_info() -> dict:
    """Resolve OS name / version / build via platform + registry.

    * ``version``: ``platform.version()`` — dotted string
      (e.g. ``"10.0.26100"`` on Windows 11 24H2).
    * ``build``: ``CurrentBuild`` from the registry — the bare build
      number users recognise (e.g. ``"26100"``).
    * ``name``: ``ProductName`` from the registry — preserves the
      edition (Pro, IoT LTSC, ...) which ``platform.release()``
      collapses to a single digit.

    Microsoft famously left ``ProductName`` saying "Windows 10" on
    early Windows 11 builds. When ``CurrentBuild >= 22000`` we patch
    the "Windows 10" substring to "Windows 11" so the user sees what
    they actually booted into.
    """
    name = "Windows"
    version = "unknown"
    build = "unknown"

    try:
        v = platform.version()
        if v:
            version = v
    except Exception as exc:   # noqa: BLE001
        log.debug("platform.version() failed: %s", exc)

    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, _CURRENT_VERSION_KEY
        ) as key:
            try:
                product, _ = winreg.QueryValueEx(key, "ProductName")
                if product:
                    name = str(product).strip()
            except OSError:
                pass
            try:
                current_build, _ = winreg.QueryValueEx(key, "CurrentBuild")
                if current_build:
                    build = str(current_build).strip()
            except OSError:
                pass
    except OSError as exc:
        log.warning("Could not read CurrentVersion registry key: %s", exc)

    # Patch the legacy "Windows 10" ProductName on actual Windows 11
    # builds. 22000 is the first Win11 RTM build.
    try:
        if int(build) >= 22_000 and "Windows 10" in name:
            name = name.replace("Windows 10", "Windows 11")
    except ValueError:
        pass

    return {
        "name":    name,
        "version": version,
        "build":   build,
    }


# --------------------------------------------------------------------------
# GPU usage — nvidia-smi probe (None on AMD-only, no driver, etc.)
# --------------------------------------------------------------------------

def gpu_usage_nvidia() -> float | None:
    """First NVIDIA GPU's utilisation %, or ``None`` on any failure.

    Calls ``nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,
    nounits`` with a 2 s timeout. Returns ``None`` (and debug-logs) for
    any of: nvidia-smi not on PATH (AMD-only or driver missing),
    subprocess timeout, non-zero exit, empty output, unparseable value.
    Never raises — this is a best-effort metric the dashboard can skip.
    """
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=2,
            creationflags=_HIDDEN_CONSOLE_FLAGS,
        )
    except FileNotFoundError:
        log.debug(
            "nvidia-smi unavailable — no NVIDIA driver on PATH."
        )
        return None
    except subprocess.TimeoutExpired:
        log.debug("nvidia-smi timed out after 2 s.")
        return None
    except Exception as exc:   # noqa: BLE001
        log.debug("nvidia-smi call failed: %s", exc)
        return None

    if result.returncode != 0:
        log.debug(
            "nvidia-smi exit=%d stderr=%r",
            result.returncode,
            (result.stderr or "").strip(),
        )
        return None

    lines = (result.stdout or "").strip().splitlines()
    if not lines:
        return None
    try:
        return float(lines[0].strip())
    except ValueError as exc:
        log.debug(
            "Could not parse nvidia-smi output %r: %s", lines[0], exc
        )
        return None


# --------------------------------------------------------------------------
# Top processes by RAM (RSS)
# --------------------------------------------------------------------------

_PROCESS_SKIP_NAMES = frozenset({
    "System Idle Process",
})


def top_processes_by_memory(n: int = 3) -> list[dict]:
    """Top ``n`` processes sorted by RSS descending.

    Item shape:
        ``{"name": str, "memory_mb": float, "pid": int}``

    Filters:
        * PID 0 and "System Idle Process" — no useful memory signal.
        * Empty / missing process names.
        * Processes that vanish or deny access mid-iteration (silently
          skipped — no log spam for a transient race).

    ``memory_mb`` precision:
        * ``>= 1000 MB``: rounded to 0 decimals (the UI rescales to GB).
        * ``< 1000 MB``: rounded to 1 decimal.
    """
    if n <= 0:
        return []

    # Collect (rss_bytes, pid, name) tuples first; sorting strings costs
    # nothing compared to sort-after-formatting.
    procs: list[tuple[int, int, str]] = []
    for proc in psutil.process_iter(attrs=["pid", "name", "memory_info"]):
        try:
            info = proc.info
            pid = int(info.get("pid") or 0)
            name = (info.get("name") or "").strip()
            mem = info.get("memory_info")
            if pid == 0 or not name or name in _PROCESS_SKIP_NAMES:
                continue
            if mem is None:
                continue
            procs.append((int(mem.rss), pid, name))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        except Exception as exc:   # noqa: BLE001
            log.debug("process_iter entry failed: %s", exc)
            continue

    procs.sort(key=lambda t: t[0], reverse=True)

    result: list[dict] = []
    for rss_bytes, pid, name in procs[:n]:
        mb = rss_bytes / (1024 * 1024)
        mb_rounded = round(mb, 0) if mb >= 1000 else round(mb, 1)
        result.append({
            "name":      name,
            "memory_mb": float(mb_rounded),
            "pid":       pid,
        })
    return result


# --------------------------------------------------------------------------
# Drive info — psutil partitions + PowerShell Get-PhysicalDisk (cached 30s)
# --------------------------------------------------------------------------

_PHYSICAL_DISK_PS_CMD = (
    "Get-PhysicalDisk "
    "| Select-Object DeviceId, MediaType, BusType, HealthStatus "
    "| ConvertTo-Json -Compress"
)

# Module-level time-gated cache so repeat callers inside the 30 s window
# never re-hit PowerShell. The worker also gates by tick, so this is
# belt-and-suspenders — still valuable because the smoke test and any
# future caller won't know about the worker's cadence.
_drive_cache: tuple[float, list[dict]] | None = None
_DRIVE_CACHE_TTL_S: float = 30.0


def _primary_physical_disk_info() -> dict:
    """Single PowerShell call → ``{"type": ..., "health": ...}``.

    Rules:
        * Skip any disk where ``BusType == "USB"`` (removable media
          shouldn't dictate the "system drive" identity).
        * Picks the FIRST remaining entry — simplification per spec
          (TODO: proper Get-Partition | Get-Disk mapping for multi-disk
          systems). All drive letters share this type/health value.
        * Type classification:
            - ``BusType == "NVMe"``          → "NVMe"
            - ``MediaType == "SSD"``         → "SSD"
            - ``MediaType == "HDD"``         → "HDD"
            - otherwise                      → "Unknown"
        * ``HealthStatus`` passed through as-is (strings are
          "Healthy" / "Warning" / "Unhealthy" / "Unknown").
    """
    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                _PHYSICAL_DISK_PS_CMD,
            ],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=_HIDDEN_CONSOLE_FLAGS,
        )
    except FileNotFoundError:
        log.warning("powershell.exe not found on PATH.")
        return {"type": "Unknown", "health": "Unknown"}
    except subprocess.TimeoutExpired:
        log.warning("Get-PhysicalDisk timed out after 5 s.")
        return {"type": "Unknown", "health": "Unknown"}
    except Exception as exc:   # noqa: BLE001
        log.warning("Get-PhysicalDisk call failed: %s", exc)
        return {"type": "Unknown", "health": "Unknown"}

    if result.returncode != 0:
        log.warning(
            "Get-PhysicalDisk exit=%d stderr=%r",
            result.returncode,
            (result.stderr or "").strip(),
        )
        return {"type": "Unknown", "health": "Unknown"}

    raw = (result.stdout or "").strip()
    if not raw:
        return {"type": "Unknown", "health": "Unknown"}

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        log.warning("Could not parse Get-PhysicalDisk JSON: %s", exc)
        return {"type": "Unknown", "health": "Unknown"}

    # Single disk: dict. Multiple disks: list of dicts.
    if isinstance(parsed, dict):
        entries = [parsed]
    elif isinstance(parsed, list):
        entries = parsed
    else:
        return {"type": "Unknown", "health": "Unknown"}

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        bus = str(entry.get("BusType") or "").strip()
        if bus.lower() == "usb":
            continue
        media = str(entry.get("MediaType") or "").strip()
        health = str(entry.get("HealthStatus") or "Unknown").strip() or "Unknown"
        if bus.lower() == "nvme":
            dtype = "NVMe"
        elif media.lower() == "ssd":
            dtype = "SSD"
        elif media.lower() == "hdd":
            dtype = "HDD"
        else:
            dtype = "Unknown"
        return {"type": dtype, "health": health}

    return {"type": "Unknown", "health": "Unknown"}


def drive_info() -> list[dict]:
    """Per-letter drive snapshot. 30 s module-level cache.

    Each entry:
        ``{"letter": "C:", "type": "NVMe"|"SSD"|"HDD"|"Unknown",
           "health": "Healthy"|"Warning"|"Unhealthy"|"Unknown",
           "total_gb": float, "used_gb": float, "percent": float}``

    Skipped: drives with ``total_gb < 1`` (empty optical, virtual, ...).
    Duplicate mountpoints are collapsed.
    """
    global _drive_cache
    now = _time.monotonic()
    if (
        _drive_cache is not None
        and now - _drive_cache[0] < _DRIVE_CACHE_TTL_S
    ):
        return list(_drive_cache[1])

    primary = _primary_physical_disk_info()

    drives: list[dict] = []
    try:
        partitions = psutil.disk_partitions(all=False)
    except Exception as exc:   # noqa: BLE001
        log.warning("psutil.disk_partitions failed: %s", exc)
        partitions = []

    seen: set[str] = set()
    for part in partitions:
        mount = part.mountpoint
        if not mount or mount in seen:
            continue
        seen.add(mount)
        try:
            usage = psutil.disk_usage(mount)
        except (PermissionError, OSError) as exc:
            log.debug("disk_usage(%s) skipped: %s", mount, exc)
            continue

        total_gb = usage.total / _GB
        if total_gb < 1.0:
            continue

        # "C:\\" -> "C:" ; fallback: use mount unchanged.
        letter = mount.rstrip("\\/")
        if not letter:
            letter = mount

        drives.append({
            "letter":   letter,
            "type":     primary["type"],
            "health":   primary["health"],
            "total_gb": round(total_gb, 1),
            "used_gb":  round(usage.used / _GB, 1),
            "percent":  float(usage.percent),
        })

    _drive_cache = (now, list(drives))
    return drives


# --------------------------------------------------------------------------
# Standalone smoke test
# --------------------------------------------------------------------------

if __name__ == "__main__":
    from app.core.logging_setup import setup_logging

    setup_logging()
    log.info("Running hardware.py standalone smoke test.")

    print(f"CPU name       : {cpu_name()}")
    print(f"CPU percent    : {cpu_percent()}%")
    print(f"CPU cores      : {cpu_core_count()}")
    print(f"RAM            : {ram_info()}")
    print(f"GPU names      : {gpu_names()}")
    print(f"GPU usage      : {gpu_usage_nvidia()}")
    print(f"Uptime         : {uptime()}")
    print(f"Uptime (str)   : {uptime_str()}")
    print(f"OS info        : {os_info()}")
    print(f"Top processes  :")
    for proc in top_processes_by_memory(3):
        print(f"   - {proc['name']:<30} {proc['memory_mb']:>8} MB  (pid {proc['pid']})")
    print(f"Drives         :")
    for drv in drive_info():
        print(
            f"   - {drv['letter']:<4} {drv['type']:<8} "
            f"{drv['health']:<10} "
            f"{drv['used_gb']:>6.1f} / {drv['total_gb']:<6.1f} GB "
            f"({drv['percent']}%)"
        )
