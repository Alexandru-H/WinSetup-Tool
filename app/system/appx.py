"""appx.py — AppX package management via PowerShell cmdlets.

Contract (CLAUDE.md §4.1):
    * ZERO PySide6 / UI imports.  All operations are pure Python + subprocess.
    * is_installed(package_name) → bool
          Returns True if at least one matching package exists for the current
          user OR as a provisioned (all-user) package.
          package_name is matched with wildcards: "*<name>*".
    * remove_package(package_name) → bool
          Two-step removal:
            1. Remove-AppxPackage  — uninstalls for the current user session.
            2. Remove-AppxProvisionedPackage  — deprovisions so it doesn't
               reinstall on new user accounts (requires admin; best-effort).
          Returns True when step 1 succeeds (or the package was absent).
          Step 2 failure is non-fatal and only logged at INFO level.
    * All errors are LOGGED, never raised to the caller.
    * PowerShell calls use CREATE_NO_WINDOW and -ErrorAction SilentlyContinue
      to avoid console windows and exception spam.
    * Timeouts: 15 s for reads, 60 s for writes (Remove ops can be slow on
      spinning disks or large packages).

Run standalone for a read-only smoke test (no packages are removed):
    python -m app.system.appx
"""

from __future__ import annotations

import subprocess

from app.core.logging_setup import get_logger

log = get_logger(__name__)

try:
    _CREATE_NO_WINDOW: int = subprocess.CREATE_NO_WINDOW
except AttributeError:
    _CREATE_NO_WINDOW = 0  # non-Windows CI / dev machine fallback


# ---------------------------------------------------------------------------
# Internal PowerShell runner
# ---------------------------------------------------------------------------

def _ps(command: str, timeout: int = 30) -> tuple[int, str, str]:
    """Run a single PowerShell expression.

    Args:
        command: A PowerShell expression string (no newlines needed).
        timeout: Seconds before the process is abandoned.

    Returns:
        (returncode, stdout, stderr) — all strings, UTF-8, errors replaced.
    """
    try:
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy", "Bypass",
                "-Command", command,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=_CREATE_NO_WINDOW,
            encoding="utf-8",
            errors="replace",
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        log.warning("PowerShell timed out (%ds): %.80s", timeout, command)
        return -1, "", "timeout"
    except FileNotFoundError:
        log.error("powershell.exe not found — is this a Windows system?")
        return -1, "", "powershell not found"
    except Exception as exc:
        log.error("PowerShell runner exception: %s", exc)
        return -1, "", str(exc)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def is_installed(package_name: str) -> bool:
    """Return True if the AppX package is installed (user or provisioned).

    Checks in two passes:
      1. Get-AppxPackage  — current user's installed packages.
      2. Get-AppxProvisionedPackage  — provisioned all-user packages.
         (This call may require admin; failure is silently treated as False.)

    Args:
        package_name: Partial Package Family Name.  Wildcards are added by
                      this function — do NOT pass "*" in the argument.
    """
    # --- Pass 1: current user --------------------------------------------
    cmd_user = (
        f'$pkg = Get-AppxPackage -Name "*{package_name}*" '
        f'-ErrorAction SilentlyContinue; '
        f'if ($pkg) {{ "true" }} else {{ "false" }}'
    )
    rc, out, _ = _ps(cmd_user, timeout=15)
    if rc == 0 and out.lower() == "true":
        log.debug("is_installed(%s): found (user install)", package_name)
        return True

    # --- Pass 2: provisioned (all users) ---------------------------------
    cmd_prov = (
        f'$pkg = Get-AppxProvisionedPackage -Online '
        f'-ErrorAction SilentlyContinue | '
        f'Where-Object {{ $_.DisplayName -like "*{package_name}*" }}; '
        f'if ($pkg) {{ "true" }} else {{ "false" }}'
    )
    rc, out, _ = _ps(cmd_prov, timeout=15)
    if rc == 0 and out.lower() == "true":
        log.debug("is_installed(%s): found (provisioned)", package_name)
        return True

    log.debug("is_installed(%s): not found", package_name)
    return False


def remove_package(package_name: str) -> bool:
    """Remove an AppX package for the current user and deprovision it.

    Step 1 (critical): Remove-AppxPackage — uninstalls for the current user.
    Step 2 (best-effort): Remove-AppxProvisionedPackage — prevents the
        package from being reinstalled for new user accounts.  Requires
        admin rights.  If this step fails we log at INFO and return True
        anyway (user removal succeeded, which is the primary goal).

    Args:
        package_name: Partial Package Family Name (no wildcards).

    Returns:
        True  — step 1 succeeded or package was already absent.
        False — step 1 failed (package could not be removed for current user).
    """
    log.info("remove_package: starting removal of %s", package_name)

    # ---- Step 1: remove for current user --------------------------------
    cmd_user = (
        f'Get-AppxPackage -Name "*{package_name}*" '
        f'-ErrorAction SilentlyContinue | '
        f'Remove-AppxPackage -ErrorAction SilentlyContinue'
    )
    rc1, _, err1 = _ps(cmd_user, timeout=60)

    if rc1 != 0:
        log.warning(
            "remove_package: step 1 FAILED for %s (rc=%d): %.200s",
            package_name, rc1, err1,
        )
        return False

    log.info("remove_package: step 1 OK for %s", package_name)

    # ---- Step 2: deprovision (best-effort, admin required) --------------
    cmd_prov = (
        f'Get-AppxProvisionedPackage -Online '
        f'-ErrorAction SilentlyContinue | '
        f'Where-Object {{ $_.DisplayName -like "*{package_name}*" }} | '
        f'Remove-AppxProvisionedPackage -Online '
        f'-ErrorAction SilentlyContinue | Out-Null'
    )
    rc2, _, err2 = _ps(cmd_prov, timeout=60)

    if rc2 != 0:
        log.info(
            "remove_package: step 2 (deprovision) skipped/failed for %s "
            "(non-fatal, rc=%d): %.100s",
            package_name, rc2, err2,
        )
    else:
        log.info("remove_package: step 2 (deprovision) OK for %s", package_name)

    return True


# ---------------------------------------------------------------------------
# Bulk detection helper
# ---------------------------------------------------------------------------

def bulk_detect(package_names: list[str]) -> dict[str, bool]:
    """Check multiple AppX packages in a single PowerShell invocation.

    Issues ONE Get-AppxPackage call to retrieve all installed names, then
    does substring matching in Python for each requested package_name.
    Much faster than N individual is_installed() calls on page load.

    Args:
        package_names: List of partial Package Family Names (no wildcards).
                       Same values stored in AppXEntry.package_name.

    Returns:
        Dict mapping each input package_name → True (installed) / False (absent).
        If the PS call fails entirely, all entries default to False.
    """
    if not package_names:
        return {}

    # Retrieve all currently installed package names in one call
    cmd = (
        "Get-AppxPackage -ErrorAction SilentlyContinue "
        "| Select-Object -ExpandProperty Name"
    )
    rc, out, err = _ps(cmd, timeout=20)

    installed_lower: set[str] = set()
    if rc == 0 and out:
        installed_lower = {
            line.strip().lower()
            for line in out.splitlines()
            if line.strip()
        }
    elif rc != 0:
        log.warning(
            "bulk_detect: Get-AppxPackage failed (rc=%d): %.100s", rc, err
        )

    result: dict[str, bool] = {}
    for pkg in package_names:
        pkg_lower = pkg.lower()
        # Wildcard match: any installed name that contains pkg_lower as substring
        result[pkg] = any(pkg_lower in name for name in installed_lower)

    found = sum(1 for v in result.values() if v)
    log.debug(
        "bulk_detect: %d/%d packages present (from %d installed names).",
        found, len(package_names), len(installed_lower),
    )
    return result


# ---------------------------------------------------------------------------
# Standalone smoke test  (python -m app.system.appx)
# Reads only — does NOT remove anything.
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    from app.core.logging_setup import setup_logging

    setup_logging()

    # Packages expected on a fresh Windows 11 install
    KNOWN_PACKAGES = [
        ("MicrosoftSolitaireCollection", "Solitaire Collection"),
        ("BingNews",                     "Microsoft News"),
        ("XboxGamingOverlay",            "Xbox Game Bar"),
        ("ZuneMusic",                    "Groove / Media Player"),
        ("Microsoft.People",             "People"),
    ]
    FAKE_PACKAGE = "DefinitelyNotInstalled12345"

    print("AppX smoke test — read-only, no packages removed")
    print("-" * 56)

    for pkg_name, label in KNOWN_PACKAGES:
        present = is_installed(pkg_name)
        status  = "INSTALLED   " if present else "not present "
        print(f"  {status}  {label}  ({pkg_name})")

    # Verify fake package returns False
    fake_result = is_installed(FAKE_PACKAGE)
    if fake_result:
        print(f"  UNEXPECTED: {FAKE_PACKAGE} reported as installed!")
        sys.exit(1)
    else:
        print(f"  not present   (expected) {FAKE_PACKAGE}")

    print("-" * 56)
    print("Smoke test complete — no removals performed.")
