"""Optimizations catalog — pure data registry of Windows tweaks.

Contract:
    * TweakEntry is an immutable NamedTuple (key, name, apply_fn, check_fn,
      recommended, requires_restart, win10_only, win11_only).
    * apply_fn returns bool (True = success).  check_fn returns bool
      (True = tweak is currently active).  Both are CALLABLES imported
      from app.system.tweaks.* — never strings.
    * key is stable and used for i18n: "optimizations.tweak.<key>" for the
      display name, "optimizations.tweak.<key>.desc" for the description.
    * name is a fallback English display name when i18n is unavailable.
    * CategoryEntry mirrors the AppsCatalog pattern: (key, default_expanded,
      tweaks).
    * OPTIMIZATIONS_CATALOG is an ordered module-level list — order is
      semantic, not alphabetical.
    * ZERO UI dependencies (no PySide6, no theming, no i18n calls).

Run standalone for self-validation (read-only, no system modifications):
    python -m app.data.optimizations_catalog
"""

from __future__ import annotations

from typing import Callable, NamedTuple

from app.system.tweaks import bloatware, explorer, performance, privacy
from app.system.tweaks import services as svc


# --------------------------------------------------------------------------
# Data types
# --------------------------------------------------------------------------

class TweakEntry(NamedTuple):
    key: str
    name: str
    apply_fn: Callable[[], bool]
    check_fn: Callable[[], bool]
    recommended: bool = False
    requires_restart: bool = False
    win10_only: bool = False
    win11_only: bool = False


class CategoryEntry(NamedTuple):
    key: str
    default_expanded: bool
    tweaks: list[TweakEntry]


# --------------------------------------------------------------------------
# Catalog
# --------------------------------------------------------------------------

OPTIMIZATIONS_CATALOG: list[CategoryEntry] = [

    # -----------------------------------------------------------------------
    # 1. Privacy & Telemetry  (8 tweaks, all recommended)
    # -----------------------------------------------------------------------
    CategoryEntry("privacy", True, [
        TweakEntry(
            "disable_telemetry",
            "Disable telemetry",
            privacy.apply_disable_telemetry,
            privacy.check_disable_telemetry,
            recommended=True,
        ),
        TweakEntry(
            "disable_cortana",
            "Disable Cortana",
            privacy.apply_disable_cortana,
            privacy.check_disable_cortana,
            recommended=True,
        ),
        TweakEntry(
            "disable_bing_search",
            "Disable Bing search in Start",
            privacy.apply_disable_bing_search,
            privacy.check_disable_bing_search,
            recommended=True,
        ),
        TweakEntry(
            "disable_web_search",
            "Disable web search suggestions",
            privacy.apply_disable_web_search,
            privacy.check_disable_web_search,
            recommended=True,
        ),
        TweakEntry(
            "disable_advertising_id",
            "Disable advertising ID",
            privacy.apply_disable_advertising_id,
            privacy.check_disable_advertising_id,
            recommended=True,
        ),
        TweakEntry(
            "disable_activity_history",
            "Disable activity history",
            privacy.apply_disable_activity_history,
            privacy.check_disable_activity_history,
            recommended=True,
        ),
        TweakEntry(
            "disable_location",
            "Disable location access",
            privacy.apply_disable_location,
            privacy.check_disable_location,
            recommended=True,
        ),
        TweakEntry(
            "disable_feedback",
            "Disable feedback requests",
            privacy.apply_disable_feedback,
            privacy.check_disable_feedback,
            recommended=True,
        ),
    ]),

    # -----------------------------------------------------------------------
    # 2. Bloatware & Content Delivery  (7 tweaks, 4 recommended)
    # -----------------------------------------------------------------------
    CategoryEntry("bloatware", True, [
        TweakEntry(
            "disable_consumer_features",
            "Disable consumer features (auto-installs)",
            bloatware.apply_disable_consumer_features,
            bloatware.check_disable_consumer_features,
            recommended=True,
        ),
        TweakEntry(
            "disable_lock_screen_ads",
            "Disable lock screen ads",
            bloatware.apply_disable_lock_screen_ads,
            bloatware.check_disable_lock_screen_ads,
            recommended=True,
        ),
        TweakEntry(
            "disable_start_suggestions",
            "Disable Start menu app suggestions",
            bloatware.apply_disable_start_suggestions,
            bloatware.check_disable_start_suggestions,
            recommended=True,
        ),
        TweakEntry(
            "disable_settings_suggestions",
            "Disable Settings page suggestions",
            bloatware.apply_disable_settings_suggestions,
            bloatware.check_disable_settings_suggestions,
            recommended=False,
        ),
        TweakEntry(
            "disable_news_widgets",
            "Disable News & Interests widget",
            bloatware.apply_disable_news_widgets,
            bloatware.check_disable_news_widgets,
            recommended=True,
        ),
        TweakEntry(
            "disable_tips",
            "Disable Windows tips & tricks",
            bloatware.apply_disable_tips,
            bloatware.check_disable_tips,
            recommended=False,
        ),
        TweakEntry(
            "disable_spotlight",
            "Disable Windows Spotlight on lock screen",
            bloatware.apply_disable_spotlight,
            bloatware.check_disable_spotlight,
            recommended=False,
        ),
    ]),

    # -----------------------------------------------------------------------
    # 3. Explorer & Shell  (7 tweaks, 2 recommended)
    # -----------------------------------------------------------------------
    CategoryEntry("explorer", False, [
        TweakEntry(
            "show_extensions",
            "Show file extensions",
            explorer.apply_show_extensions,
            explorer.check_show_extensions,
            recommended=True,
        ),
        TweakEntry(
            "show_hidden_files",
            "Show hidden files and folders",
            explorer.apply_show_hidden_files,
            explorer.check_show_hidden_files,
            recommended=False,
        ),
        TweakEntry(
            "dark_mode_default",
            "Enable dark mode",
            explorer.apply_dark_mode_default,
            explorer.check_dark_mode_default,
            recommended=True,
        ),
        TweakEntry(
            "classic_context_menu",
            "Restore classic right-click menu",
            explorer.apply_classic_context_menu,
            explorer.check_classic_context_menu,
            recommended=False,
            win11_only=True,
        ),
        TweakEntry(
            "disable_search_suggestions",
            "Disable search box suggestions in Explorer",
            explorer.apply_disable_search_suggestions,
            explorer.check_disable_search_suggestions,
            recommended=False,
        ),
        TweakEntry(
            "small_taskbar_icons",
            "Use small taskbar icons",
            explorer.apply_small_taskbar_icons,
            explorer.check_small_taskbar_icons,
            recommended=False,
            win10_only=True,
        ),
        TweakEntry(
            "explorer_open_thispc",
            "Open Explorer to This PC instead of Quick Access",
            explorer.apply_explorer_open_thispc,
            explorer.check_explorer_open_thispc,
            recommended=False,
        ),
    ]),

    # -----------------------------------------------------------------------
    # 4. Performance & Power  (6 tweaks, 0 recommended)
    # -----------------------------------------------------------------------
    CategoryEntry("performance", False, [
        TweakEntry(
            "power_high_performance",
            "Set High Performance power plan",
            performance.apply_power_high_performance,
            performance.check_power_high_performance,
            recommended=False,
        ),
        TweakEntry(
            "disable_visual_effects",
            "Disable visual effects (best performance)",
            performance.apply_disable_visual_effects,
            performance.check_disable_visual_effects,
            recommended=False,
            requires_restart=True,
        ),
        TweakEntry(
            "disable_hibernation",
            "Disable hibernation",
            performance.apply_disable_hibernation,
            performance.check_disable_hibernation,
            recommended=False,
        ),
        TweakEntry(
            "disable_sysmain",
            "Disable SysMain (Superfetch)",
            performance.apply_disable_sysmain,
            performance.check_disable_sysmain,
            recommended=False,
        ),
        TweakEntry(
            "disable_xbox_gamebar",
            "Disable Xbox Game Bar & DVR",
            performance.apply_disable_xbox_gamebar,
            performance.check_disable_xbox_gamebar,
            recommended=False,
        ),
        TweakEntry(
            "gpu_hw_scheduling",
            "Enable GPU hardware-accelerated scheduling",
            performance.apply_gpu_hw_scheduling,
            performance.check_gpu_hw_scheduling,
            recommended=False,
            requires_restart=True,
        ),
    ]),

    # -----------------------------------------------------------------------
    # 5. Services & Network  (4 tweaks, 1 recommended)
    # -----------------------------------------------------------------------
    CategoryEntry("services", False, [
        TweakEntry(
            "disable_diagtrack",
            "Disable Connected User Experiences & Telemetry (DiagTrack)",
            svc.apply_disable_diagtrack,
            svc.check_disable_diagtrack,
            recommended=True,
        ),
        TweakEntry(
            "disable_insider_service",
            "Disable Windows Insider Service",
            svc.apply_disable_insider_service,
            svc.check_disable_insider_service,
            recommended=False,
        ),
        TweakEntry(
            "disable_remote_registry",
            "Disable Remote Registry",
            svc.apply_disable_remote_registry,
            svc.check_disable_remote_registry,
            recommended=False,
        ),
        TweakEntry(
            "disable_ipv6",
            "Disable IPv6",
            svc.apply_disable_ipv6,
            svc.check_disable_ipv6,
            recommended=False,
            requires_restart=True,
        ),
    ]),
]


# --------------------------------------------------------------------------
# Standalone self-validation (read-only — never calls apply_fn)
# --------------------------------------------------------------------------

if __name__ == "__main__":
    all_tweaks: list[TweakEntry] = [
        t for cat in OPTIMIZATIONS_CATALOG for t in cat.tweaks
    ]

    errors: list[str] = []

    # 1. Total tweak count
    if len(all_tweaks) != 32:
        errors.append(f"CHECK 1 FAILED — expected 32 tweaks, got {len(all_tweaks)}")

    # 2. Category count
    if len(OPTIMIZATIONS_CATALOG) != 5:
        errors.append(
            f"CHECK 2 FAILED — expected 5 categories, got {len(OPTIMIZATIONS_CATALOG)}"
        )

    # 3. Recommended count
    rec = [t for t in all_tweaks if t.recommended]
    if len(rec) != 15:
        errors.append(
            f"CHECK 3 FAILED — expected 15 recommended, got {len(rec)}: "
            + str([t.key for t in rec])
        )

    # 4. Exactly 2 categories default_expanded=True (privacy, bloatware)
    expanded = [c.key for c in OPTIMIZATIONS_CATALOG if c.default_expanded]
    if expanded != ["privacy", "bloatware"]:
        errors.append(f"CHECK 4 FAILED — expanded categories: {expanded}")

    # 5. All tweak keys unique
    seen: set[str] = set()
    dupes: list[str] = []
    for t in all_tweaks:
        if t.key in seen:
            dupes.append(t.key)
        seen.add(t.key)
    if dupes:
        errors.append(f"CHECK 5 FAILED — duplicate tweak keys: {dupes}")

    if errors:
        for e in errors:
            print(e)
        raise SystemExit(1)

    # 6. Call every check_fn — must not raise; logs are expected, exceptions are not
    print("Running check_fn() on all 32 tweaks (read-only)...")
    check_errors: list[str] = []
    for cat in OPTIMIZATIONS_CATALOG:
        for t in cat.tweaks:
            try:
                result = t.check_fn()
                status = "active" if result else "inactive"
                print(f"  [{cat.key}] {t.key}: {status}")
            except Exception as exc:
                check_errors.append(f"  [{cat.key}] {t.key}: RAISED {exc!r}")

    if check_errors:
        print("\nCHECK 6 FAILED — check_fn raised exceptions:")
        for e in check_errors:
            print(e)
        raise SystemExit(1)

    # 7. Summary
    print()
    print("All checks passed.")
    print(f"  Categories : {len(OPTIMIZATIONS_CATALOG)}")
    print(f"  Total tweaks: {len(all_tweaks)}")
    print(f"  Recommended : {len(rec)}")
    print(f"  Expanded    : {expanded}")
    print()
    print("Recommended tweaks:")
    for t in all_tweaks:
        if t.recommended:
            print(f"  *  {t.key}")
