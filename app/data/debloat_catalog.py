"""debloat_catalog.py — Pure data registry of removable AppX packages.

Contract:
    * AppXEntry is an immutable NamedTuple:
          (key, name, package_name, description, recommended)
      key           — stable identifier used for i18n lookups:
                      "debloat.app.<key>" → display name fallback.
      name          — English display name (fallback when i18n unavailable).
      package_name  — Full or partial AppX Package Family Name accepted by
                      Get-AppxPackage / Remove-AppxPackage. Wildcards are
                      applied by appx.py callers — do NOT include "*" here.
      description   — 6–10 word explanation shown on the card's second line.
      recommended   — True ⟹ "Select Recommended" turns this on automatically.

    * RiskTier is a 3-value Enum: SAFE / MODERATE / AGGRESSIVE.
      Used by the UI to colour the category header and show a warning icon.

    * CategoryEntry mirrors apps_catalog / optimizations_catalog:
          (key, default_expanded, tier, apps)

    * DEBLOAT_CATALOG is an ordered module-level list — 3 CategoryEntries in
      tier order (SAFE first, AGGRESSIVE last).

    * ZERO UI dependencies — no PySide6, no theming, no i18n calls here.

Run standalone for self-validation (no system modifications):
    python -m app.data.debloat_catalog
"""

from __future__ import annotations

from enum import Enum
from typing import NamedTuple


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

class RiskTier(Enum):
    """Visual risk level for the debloat category header."""
    SAFE       = "safe"
    MODERATE   = "moderate"
    AGGRESSIVE = "aggressive"


class AppXEntry(NamedTuple):
    """Descriptor for a single removable AppX package."""
    key:          str
    name:         str
    package_name: str
    description:  str
    recommended:  bool = False


class CategoryEntry(NamedTuple):
    """Grouping of AppXEntries into a risk tier section."""
    key:              str        # i18n: "debloat.category.<key>"
    default_expanded: bool
    tier:             RiskTier
    apps:             list[AppXEntry]


# ---------------------------------------------------------------------------
# Tier 1 — SAFE  (no essential functionality lost; safe to remove universally)
# ---------------------------------------------------------------------------

_SAFE: list[AppXEntry] = [
    AppXEntry(
        "solitaire", "Microsoft Solitaire Collection",
        "Microsoft.MicrosoftSolitaireCollection",
        "Card games bundle (Klondike, Spider, FreeCell)",
        recommended=True,
    ),
    AppXEntry(
        "mahjong", "Microsoft Mahjong",
        "Microsoft.MicrosoftMahjong",
        "Classic tile-matching game",
        recommended=True,
    ),
    AppXEntry(
        "minesweeper", "Microsoft Minesweeper",
        "Microsoft.MicrosoftMinesweeper",
        "Classic minesweeper game",
        recommended=True,
    ),
    AppXEntry(
        "casual_games", "Microsoft Bing Casual Games",
        "Microsoft.MicrosoftCasualGames",
        "Bing-powered word and puzzle games",
        recommended=True,
    ),
    AppXEntry(
        "xbox_game_bar", "Xbox Game Bar",
        "Microsoft.XboxGamingOverlay",
        "Win+G overlay for screenshots and clips",
        recommended=True,
    ),
    AppXEntry(
        "xbox_overlay_plugin", "Xbox Game Bar Plugin",
        "Microsoft.XboxGameOverlay",
        "Companion plugin for Xbox Game Bar",
        recommended=True,
    ),
    AppXEntry(
        "xbox_identity", "Xbox Identity Provider",
        "Microsoft.XboxIdentityProvider",
        "Xbox Live account authentication service",
        recommended=True,
    ),
    AppXEntry(
        "xbox_live", "Xbox Live",
        "Microsoft.XboxLIVE",
        "Legacy Xbox Live integration layer",
        recommended=True,
    ),
    AppXEntry(
        "xbox_speech", "Xbox Speech to Text Overlay",
        "Microsoft.XboxSpeechToTextOverlay",
        "Speech captions overlay during gameplay",
        recommended=True,
    ),
    AppXEntry(
        "mixed_reality", "Mixed Reality Portal",
        "Microsoft.MixedReality.Portal",
        "Windows Mixed Reality VR launcher (deprecated)",
        recommended=True,
    ),
    AppXEntry(
        "3d_viewer", "Microsoft 3D Viewer",
        "Microsoft.Microsoft3DViewer",
        "3D model viewer (deprecated since 2022)",
        recommended=True,
    ),
    AppXEntry(
        "print_3d", "Print 3D",
        "Microsoft.Print3D",
        "3D printing tool (deprecated)",
        recommended=True,
    ),
    AppXEntry(
        "bing_news", "Microsoft News",
        "Microsoft.BingNews",
        "Microsoft News and Bing headlines feed",
        recommended=True,
    ),
    AppXEntry(
        "bing_weather", "Microsoft Weather",
        "Microsoft.BingWeather",
        "Microsoft Weather app (Bing-powered)",
        recommended=True,
    ),
    AppXEntry(
        "bing_search", "Bing Search",
        "Microsoft.BingSearch",
        "Bing search integration app",
        recommended=True,
    ),
    AppXEntry(
        "get_help", "Get Help",
        "Microsoft.GetHelp",
        "Microsoft support contact app",
        recommended=True,
    ),
    AppXEntry(
        "get_started", "Tips",
        "Microsoft.Getstarted",
        "Windows tips and onboarding screens",
        recommended=True,
    ),
    AppXEntry(
        "feedback_hub", "Feedback Hub",
        "Microsoft.WindowsFeedbackHub",
        "Sends diagnostic feedback to Microsoft",
        recommended=True,
    ),
    AppXEntry(
        "office_hub", "Microsoft Office Hub",
        "Microsoft.MicrosoftOfficeHub",
        "Promotional Office launcher / ad tile",
        recommended=True,
    ),
    AppXEntry(
        "skype", "Skype",
        "Microsoft.SkypeApp",
        "Skype consumer client",
        recommended=True,
    ),
]

# ---------------------------------------------------------------------------
# Tier 2 — MODERATE  (useful for some users; review before removing)
# ---------------------------------------------------------------------------

_MODERATE: list[AppXEntry] = [
    AppXEntry(
        "mail_calendar", "Mail and Calendar",
        "microsoft.windowscommunicationsapps",
        "Microsoft's built-in mail and calendar client",
    ),
    AppXEntry(
        "people", "People",
        "Microsoft.People",
        "Contacts manager integrated with Mail",
    ),
    AppXEntry(
        "todo", "Microsoft To Do",
        "Microsoft.Todos",
        "Microsoft's cross-platform task list app",
    ),
    AppXEntry(
        "teams_consumer", "Microsoft Teams (consumer)",
        "MicrosoftTeams",
        "Personal Teams chat — NOT the corporate version",
    ),
    AppXEntry(
        "onenote", "OneNote for Windows 10",
        "Microsoft.Office.OneNote",
        "Microsoft OneNote note-taking app",
    ),
    AppXEntry(
        "movies_tv", "Movies & TV",
        "Microsoft.ZuneVideo",
        "Microsoft's built-in video player",
    ),
    AppXEntry(
        "groove_music", "Groove Music / Media Player",
        "Microsoft.ZuneMusic",
        "Microsoft's built-in music player",
    ),
    AppXEntry(
        "maps", "Maps",
        "Microsoft.WindowsMaps",
        "Microsoft Maps with offline map support",
    ),
    AppXEntry(
        "phone_link", "Phone Link",
        "Microsoft.YourPhone",
        "Sync Android/iPhone with PC",
    ),
    AppXEntry(
        "cortana", "Cortana",
        "Microsoft.549981C3F5F10",
        "Cortana voice assistant app",
    ),
]

# ---------------------------------------------------------------------------
# Tier 3 — AGGRESSIVE  (core system apps; only remove if you have replacements)
# ---------------------------------------------------------------------------

_AGGRESSIVE: list[AppXEntry] = [
    AppXEntry(
        "photos", "Photos",
        "Microsoft.Windows.Photos",
        "Default photo viewer — replace before removing",
    ),
    AppXEntry(
        "camera", "Camera",
        "Microsoft.WindowsCamera",
        "Webcam capture app",
    ),
    AppXEntry(
        "calculator", "Calculator",
        "Microsoft.WindowsCalculator",
        "Built-in Windows calculator",
    ),
    AppXEntry(
        "voice_recorder", "Voice Recorder",
        "Microsoft.WindowsSoundRecorder",
        "Microphone recording app",
    ),
    AppXEntry(
        "sticky_notes", "Sticky Notes",
        "Microsoft.MicrosoftStickyNotes",
        "Desktop sticky notes (syncs with OneNote)",
    ),
    AppXEntry(
        "snipping_tool", "Snipping Tool",
        "Microsoft.ScreenSketch",
        "Screenshot tool — Win+Shift+S shortcut",
    ),
    AppXEntry(
        "paint", "Paint",
        "Microsoft.MSPaint",
        "Built-in bitmap image editor",
    ),
    AppXEntry(
        "edge", "Microsoft Edge",
        "Microsoft.MicrosoftEdge.Stable",
        "WARNING: required by some Windows features",
    ),
    AppXEntry(
        "store", "Microsoft Store",
        "Microsoft.WindowsStore",
        "WARNING: needed to update other AppX apps",
    ),
    AppXEntry(
        "alarms", "Alarms & Clock",
        "Microsoft.WindowsAlarms",
        "Alarms, timers and world clock",
    ),
    AppXEntry(
        "quick_assist", "Quick Assist",
        "MicrosoftCorporationII.QuickAssist",
        "Remote help screen-sharing tool",
    ),
    AppXEntry(
        "family", "Family Safety",
        "MicrosoftCorporationII.MicrosoftFamily",
        "Parental controls and family management",
    ),
    AppXEntry(
        "power_automate", "Power Automate Desktop",
        "Microsoft.PowerAutomateDesktop",
        "Workflow automation (heavy background process)",
    ),
    AppXEntry(
        "clipchamp", "Clipchamp Video Editor",
        "Clipchamp.Clipchamp",
        "Microsoft's built-in browser-based video editor",
    ),
]


# ---------------------------------------------------------------------------
# Public catalog
# ---------------------------------------------------------------------------

DEBLOAT_CATALOG: list[CategoryEntry] = [
    CategoryEntry(
        key="safe",
        default_expanded=True,
        tier=RiskTier.SAFE,
        apps=_SAFE,
    ),
    CategoryEntry(
        key="moderate",
        default_expanded=False,
        tier=RiskTier.MODERATE,
        apps=_MODERATE,
    ),
    CategoryEntry(
        key="aggressive",
        default_expanded=False,
        tier=RiskTier.AGGRESSIVE,
        apps=_AGGRESSIVE,
    ),
]


# ---------------------------------------------------------------------------
# Standalone self-validation  (python -m app.data.debloat_catalog)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    total       = sum(len(c.apps) for c in DEBLOAT_CATALOG)
    recommended = sum(1 for c in DEBLOAT_CATALOG for a in c.apps if a.recommended)

    assert len(DEBLOAT_CATALOG) == 3, f"Expected 3 categories, got {len(DEBLOAT_CATALOG)}"
    assert total == 44, f"Expected 44 apps, got {total}"
    assert recommended == 20, f"Expected 20 recommended, got {recommended}"

    # Every key must be unique across the full catalog
    all_keys = [a.key for c in DEBLOAT_CATALOG for a in c.apps]
    duplicates = {k for k in all_keys if all_keys.count(k) > 1}
    assert not duplicates, f"Duplicate keys found: {duplicates}"

    # Tier ordering
    assert DEBLOAT_CATALOG[0].tier == RiskTier.SAFE
    assert DEBLOAT_CATALOG[1].tier == RiskTier.MODERATE
    assert DEBLOAT_CATALOG[2].tier == RiskTier.AGGRESSIVE

    # All entries have non-empty required fields
    for cat in DEBLOAT_CATALOG:
        for app in cat.apps:
            assert app.key,          f"Empty key in {cat.key}"
            assert app.name,         f"Empty name for key={app.key}"
            assert app.package_name, f"Empty package_name for key={app.key}"
            assert app.description,  f"Empty description for key={app.key}"

    # Count per tier
    counts = {c.key: len(c.apps) for c in DEBLOAT_CATALOG}
    print(
        f"OK: {total} apps in 3 tiers "
        f"(safe={counts['safe']}, moderate={counts['moderate']}, "
        f"aggressive={counts['aggressive']}), "
        f"{recommended} recommended."
    )
