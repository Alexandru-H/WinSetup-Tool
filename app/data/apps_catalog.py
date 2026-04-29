"""
apps_catalog.py — pure data registry of installable apps grouped by category.

Contract (CLAUDE.md §4.1):
    * AppEntry is an immutable NamedTuple: (name, winget_id, recommended).
      ``name``        — display name (brand name, used in UI as-is; not
                        translated — "Google Chrome" stays "Google Chrome"
                        in every locale).
      ``winget_id``   — exact package ID for ``winget install --id <id>``.
      ``recommended`` — True on apps included in the "Select Recommended"
                        quick-install preset (18 apps total).

    * CategoryEntry is an immutable NamedTuple: (key, default_expanded, apps).
      ``key``               — snake_case i18n key; UI resolves the display
                              name at render time via
                              ``tr(f"apps.category.{key}")``.
      ``default_expanded``  — whether the category accordion opens expanded
                              on first paint of the Apps page.
      ``apps``              — ordered list of AppEntry for this category.

    * APPS_CATALOG is a module-level ordered list of CategoryEntry.
      Ordering is semantic: browsers first, hardware/drivers last.
      Do NOT re-sort alphabetically.

    * ZERO UI dependencies — no PySide6, no theming, no i18n calls.
      Import-safe from system modules, tests, and anywhere else.

Run standalone for a self-validation smoke test (no side effects):
    python -m app.data.apps_catalog
"""

from __future__ import annotations

import re
from typing import NamedTuple


# --------------------------------------------------------------------------
# Data types
# --------------------------------------------------------------------------

class AppEntry(NamedTuple):
    name: str
    winget_id: str
    recommended: bool


class CategoryEntry(NamedTuple):
    key: str
    default_expanded: bool
    apps: list[AppEntry]


# --------------------------------------------------------------------------
# Catalog
# --------------------------------------------------------------------------

APPS_CATALOG: list[CategoryEntry] = [

    # -----------------------------------------------------------------------
    # 1. Browsers
    # -----------------------------------------------------------------------
    CategoryEntry("browsers", True, [
        AppEntry("Google Chrome",  "Google.Chrome",                    True),
        AppEntry("Mozilla Firefox","Mozilla.Firefox",                   False),
        AppEntry("Brave",          "Brave.Brave",                      False),
        AppEntry("Opera",          "Opera.Opera",                      False),
        AppEntry("Opera GX",       "Opera.OperaGX",                    False),
        AppEntry("Vivaldi",        "VivaldiTechnologies.Vivaldi",      False),
        AppEntry("Tor Browser",    "TorProject.TorBrowser",            False),
        AppEntry("LibreWolf",      "LibreWolf.LibreWolf",              False),
        AppEntry("Zen Browser",    "Zen-Team.Zen-Browser",             False),
    ]),

    # -----------------------------------------------------------------------
    # 2. Communication
    # -----------------------------------------------------------------------
    CategoryEntry("communication", True, [
        AppEntry("Discord",          "Discord.Discord",                 True),
        AppEntry("Telegram Desktop", "Telegram.TelegramDesktop",        False),
        AppEntry("WhatsApp",         "WhatsApp.WhatsApp",               False),
        AppEntry("Signal",           "OpenWhisperSystems.Signal",       False),
        AppEntry("Slack",            "SlackTechnologies.Slack",         False),
        AppEntry("Microsoft Teams",  "Microsoft.Teams",                 False),
        AppEntry("Zoom",             "Zoom.Zoom",                       False),
        AppEntry("Viber",            "Viber.Viber",                     False),
        AppEntry("Element",          "Element.Element",                 False),
        AppEntry("Skype",            "Microsoft.Skype",                 False),
    ]),

    # -----------------------------------------------------------------------
    # 3. Email
    # -----------------------------------------------------------------------
    CategoryEntry("email", False, [
        AppEntry("Mozilla Thunderbird", "Mozilla.Thunderbird",          False),
        AppEntry("Mailbird",            "Mailbird.Mailbird",            False),
        AppEntry("eM Client",           "eMClient.eMClient",            False),
    ]),

    # -----------------------------------------------------------------------
    # 4. Cloud Storage
    # -----------------------------------------------------------------------
    CategoryEntry("cloud_storage", False, [
        AppEntry("Google Drive", "Google.GoogleDrive",                  False),
        AppEntry("Dropbox",      "Dropbox.Dropbox",                     False),
        AppEntry("MEGA",         "Mega.MEGASync",                       False),
        AppEntry("pCloud",       "pCloud.pCloudDrive",                  False),
        AppEntry("Proton Drive", "Proton.ProtonDrive",                  False),
    ]),

    # -----------------------------------------------------------------------
    # 5. Notes & PKM
    # -----------------------------------------------------------------------
    CategoryEntry("notes_pkm", False, [
        AppEntry("Obsidian",       "Obsidian.Obsidian",                 False),
        AppEntry("Notion",         "Notion.Notion",                     False),
        AppEntry("Joplin",         "Joplin.Joplin",                     False),
        AppEntry("Logseq",         "Logseq.Logseq",                     False),
        AppEntry("Standard Notes", "StandardNotes.StandardNotes",       False),
        AppEntry("Evernote",       "Evernote.Evernote",                 False),
    ]),

    # -----------------------------------------------------------------------
    # 6. Office & Productivity
    # -----------------------------------------------------------------------
    CategoryEntry("office_productivity", False, [
        AppEntry("LibreOffice",   "TheDocumentFoundation.LibreOffice",  False),
        AppEntry("WPS Office",    "Kingsoft.WPSOffice",                 False),
        AppEntry("OnlyOffice",    "ONLYOFFICE.DesktopEditors",          False),
        AppEntry("Notepad++",     "Notepad++.Notepad++",                True),
        AppEntry("Microsoft 365", "Microsoft.Office",                   False),
        AppEntry("Trello",        "Trello.Trello",                      False),
        AppEntry("Todoist",       "Doist.Todoist",                      False),
    ]),

    # -----------------------------------------------------------------------
    # 7. PDF Reading
    # -----------------------------------------------------------------------
    CategoryEntry("pdf_reading", False, [
        AppEntry("Adobe Acrobat Reader", "Adobe.Acrobat.Reader.64-bit", True),
        AppEntry("SumatraPDF",           "SumatraPDF.SumatraPDF",       False),
        AppEntry("Foxit Reader",         "Foxit.FoxitReader",           False),
        AppEntry("PDF24 Creator",        "PDF24.PDF24Creator",          False),
        AppEntry("Calibre",              "calibre.calibre",             False),
    ]),

    # -----------------------------------------------------------------------
    # 8. Programming — IDEs & Editors
    # -----------------------------------------------------------------------
    CategoryEntry("programming_ide", False, [
        AppEntry("Visual Studio Code",          "Microsoft.VisualStudioCode",             True),
        AppEntry("Visual Studio Community 2022","Microsoft.VisualStudio.2022.Community",  False),
        AppEntry("Cursor",                      "Anysphere.Cursor",                       False),
        AppEntry("JetBrains Toolbox",           "JetBrains.Toolbox",                      False),
        AppEntry("IntelliJ IDEA Community",     "JetBrains.IntelliJIDEA.Community",       False),
        AppEntry("PyCharm Community",           "JetBrains.PyCharm.Community",            False),
        AppEntry("Sublime Text",                "SublimeHQ.SublimeText.4",                False),
        AppEntry("Neovim",                      "Neovim.Neovim",                          False),
        AppEntry("Android Studio",              "Google.AndroidStudio",                   False),
    ]),

    # -----------------------------------------------------------------------
    # 9. Programming — Runtimes & SDKs
    # -----------------------------------------------------------------------
    CategoryEntry("programming_runtime", False, [
        AppEntry("Python 3.12",            "Python.Python.3.12",                    False),
        AppEntry("Python 3.13",            "Python.Python.3.13",                    False),
        AppEntry("Node.js LTS",            "OpenJS.NodeJS.LTS",                     False),
        AppEntry("Node.js Current",        "OpenJS.NodeJS",                         False),
        AppEntry("Java 21 (Temurin JDK)",  "EclipseAdoptium.Temurin.21.JDK",       False),
        AppEntry("Go",                     "GoLang.Go",                             False),
        AppEntry("Rust (rustup)",          "Rustlang.Rustup",                       False),
        AppEntry(".NET 8 SDK",             "Microsoft.DotNet.SDK.8",                False),
        AppEntry(".NET 8 Desktop Runtime", "Microsoft.DotNet.DesktopRuntime.8",     True),
        AppEntry("VC++ Redist 2015-2022",  "Microsoft.VCRedist.2015+.x64",          True),
    ]),

    # -----------------------------------------------------------------------
    # 10. Databases
    # -----------------------------------------------------------------------
    CategoryEntry("databases", False, [
        AppEntry("DBeaver Community", "dbeaver.dbeaver",                False),
        AppEntry("HeidiSQL",          "HeidiSQL.HeidiSQL",              False),
        AppEntry("MySQL Workbench",   "Oracle.MySQLWorkbench",          False),
        AppEntry("pgAdmin 4",         "PostgreSQL.pgAdmin",             False),
        AppEntry("MongoDB Compass",   "MongoDB.Compass.Community",      False),
    ]),

    # -----------------------------------------------------------------------
    # 11. Terminal & Shell
    # -----------------------------------------------------------------------
    CategoryEntry("terminal_shell", False, [
        AppEntry("Windows Terminal", "Microsoft.WindowsTerminal",       True),
        AppEntry("PowerShell 7",     "Microsoft.PowerShell",            True),
        AppEntry("Tabby",            "Eugeny.Tabby",                    False),
        AppEntry("Alacritty",        "Alacritty.Alacritty",             False),
        AppEntry("WezTerm",          "wez.wezterm",                     False),
        AppEntry("ConEmu",           "Maximus5.ConEmu",                 False),
    ]),

    # -----------------------------------------------------------------------
    # 12. DevOps & API
    # -----------------------------------------------------------------------
    CategoryEntry("devops_api", False, [
        AppEntry("Docker Desktop", "Docker.DockerDesktop",              False),
        AppEntry("Postman",        "Postman.Postman",                   False),
        AppEntry("Insomnia",       "Insomnia.Insomnia",                 False),
        AppEntry("Bruno",          "Bruno.Bruno",                       False),
        AppEntry("WinSCP",         "WinSCP.WinSCP",                     False),
        AppEntry("FileZilla",      "TimKosse.FileZilla.Client",         False),
    ]),

    # -----------------------------------------------------------------------
    # 13. Version Control
    # -----------------------------------------------------------------------
    CategoryEntry("version_control", False, [
        AppEntry("Git",             "Git.Git",                          True),
        AppEntry("GitHub Desktop",  "GitHub.GitHubDesktop",             False),
        AppEntry("GitKraken",       "Axosoft.GitKraken",                False),
        AppEntry("Sourcetree",      "Atlassian.Sourcetree",             False),
    ]),

    # -----------------------------------------------------------------------
    # 14. AI & LLM
    # -----------------------------------------------------------------------
    CategoryEntry("ai_llm", False, [
        AppEntry("Claude Desktop",  "Anthropic.Claude",                 False),
        AppEntry("ChatGPT Desktop", "OpenAI.ChatGPT",                   False),
        AppEntry("Ollama",          "Ollama.Ollama",                    False),
        AppEntry("LM Studio",       "ElementLabs.LMStudio",             False),
    ]),

    # -----------------------------------------------------------------------
    # 15. Media Player
    # -----------------------------------------------------------------------
    CategoryEntry("media_player", False, [
        AppEntry("VLC",        "VideoLAN.VLC",                          True),
        AppEntry("mpv",        "mpv-player.mpv",                        False),
        AppEntry("PotPlayer",  "Daum.PotPlayer",                        False),
        AppEntry("MPC-HC",     "clsid2.mpc-hc",                         False),
        AppEntry("Spotify",    "Spotify.Spotify",                       False),
        AppEntry("foobar2000", "PeterPawlowski.foobar2000",             False),
        AppEntry("MusicBee",   "MusicBee.MusicBee",                     False),
        AppEntry("Plex",       "Plex.Plex",                             False),
    ]),

    # -----------------------------------------------------------------------
    # 16. Audio Editing
    # -----------------------------------------------------------------------
    CategoryEntry("audio_editing", False, [
        AppEntry("Audacity",  "Audacity.Audacity",                      False),
        AppEntry("Reaper",    "Cockos.REAPER",                          False),
        AppEntry("FL Studio", "Image-Line.FLStudio",                    False),
        AppEntry("LMMS",      "LMMS.LMMS",                              False),
        AppEntry("Cakewalk",  "BandLab.Cakewalk",                       False),
    ]),

    # -----------------------------------------------------------------------
    # 17. Video Editing
    # -----------------------------------------------------------------------
    CategoryEntry("video_editing", False, [
        AppEntry("DaVinci Resolve", "Blackmagic.DaVinciResolve",        False),
        AppEntry("HandBrake",       "HandBrake.HandBrake",              False),
        AppEntry("Shotcut",         "Meltytech.Shotcut",                False),
        AppEntry("OpenShot",        "OpenShot.OpenShot",                False),
        AppEntry("Kdenlive",        "KDE.Kdenlive",                     False),
    ]),

    # -----------------------------------------------------------------------
    # 18. Streaming & Capture
    # -----------------------------------------------------------------------
    CategoryEntry("streaming_capture", False, [
        AppEntry("OBS Studio",  "OBSProject.OBSStudio",                 False),
        AppEntry("Streamlabs",  "Streamlabs.Streamlabs",                False),
        AppEntry("ShareX",      "ShareX.ShareX",                        True),
        AppEntry("ScreenToGif", "NickeManarin.ScreenToGif",             False),
    ]),

    # -----------------------------------------------------------------------
    # 19. Image Editing
    # -----------------------------------------------------------------------
    CategoryEntry("image_editing", False, [
        AppEntry("GIMP",       "GIMP.GIMP",                             False),
        AppEntry("Krita",      "KDE.Krita",                             False),
        AppEntry("Inkscape",   "Inkscape.Inkscape",                     False),
        AppEntry("Paint.NET",  "dotPDN.PaintDotNet",                    False),
        AppEntry("darktable",  "darktable.darktable",                   False),
        AppEntry("IrfanView",  "IrfanSkiljan.IrfanView",                False),
        AppEntry("XnView MP",  "XnSoft.XnViewMP",                       False),
        AppEntry("Greenshot",  "Greenshot.Greenshot",                   False),
    ]),

    # -----------------------------------------------------------------------
    # 20. CAD & 3D
    # -----------------------------------------------------------------------
    CategoryEntry("cad_3d", False, [
        AppEntry("Blender",             "BlenderFoundation.Blender",    False),
        AppEntry("FreeCAD",             "FreeCAD.FreeCAD",              False),
        AppEntry("Unity Hub",           "Unity.UnityHub",               False),
        AppEntry("Autodesk Fusion 360", "Autodesk.Fusion360",           False),
    ]),

    # -----------------------------------------------------------------------
    # 21. Gaming
    # -----------------------------------------------------------------------
    CategoryEntry("gaming", False, [
        AppEntry("Steam",                 "Valve.Steam",                              True),
        AppEntry("Epic Games Launcher",   "EpicGames.EpicGamesLauncher",              False),
        AppEntry("GOG Galaxy",            "GOG.Galaxy",                               False),
        AppEntry("EA Desktop",            "ElectronicArts.EADesktop",                 False),
        AppEntry("Ubisoft Connect",       "Ubisoft.Connect",                          False),
        AppEntry("Battle.net",            "Blizzard.BattleNet",                       False),
        AppEntry("Rockstar Launcher",     "RockstarGames.RockstarGamesLauncher",      False),
        AppEntry("Playnite",              "Playnite.Playnite",                        False),
        AppEntry("Heroic Games Launcher", "HeroicGamesLauncher.HeroicGamesLauncher",  False),
    ]),

    # -----------------------------------------------------------------------
    # 22. Archiving
    # -----------------------------------------------------------------------
    CategoryEntry("archiving", False, [
        AppEntry("7-Zip",   "7zip.7zip",                                True),
        AppEntry("WinRAR",  "RARLab.WinRAR",                            False),
        AppEntry("PeaZip",  "Giorgiotani.Peazip",                       False),
        AppEntry("NanaZip", "M2Team.NanaZip",                           False),
    ]),

    # -----------------------------------------------------------------------
    # 23. Download & Torrent
    # -----------------------------------------------------------------------
    CategoryEntry("download_torrent", False, [
        AppEntry("qBittorrent",           "qBittorrent.qBittorrent",    True),
        AppEntry("µTorrent",              "BitTorrent.uTorrent",        False),
        AppEntry("Transmission",          "Transmission.Transmission",  False),
        AppEntry("Free Download Manager", "FreeDownloadManager.FDM",    False),
        AppEntry("JDownloader",           "AppWork.JDownloader",        False),
        AppEntry("yt-dlp",                "yt-dlp.yt-dlp",              False),
    ]),

    # -----------------------------------------------------------------------
    # 24. Network & Remote
    # -----------------------------------------------------------------------
    CategoryEntry("network_remote", False, [
        AppEntry("AnyDesk",             "AnyDeskSoftware.AnyDesk",              False),
        AppEntry("TeamViewer",          "TeamViewer.TeamViewer",                 False),
        AppEntry("Parsec",              "Parsec.Parsec",                         False),
        AppEntry("Tailscale",           "tailscale.tailscale",                   False),
        AppEntry("WireGuard",           "WireGuard.WireGuard",                   False),
        AppEntry("OpenVPN",             "OpenVPNTechnologies.OpenVPN",           False),
        AppEntry("Wireshark",           "WiresharkFoundation.Wireshark",         False),
        AppEntry("Advanced IP Scanner", "Famatech.AdvancedIPScanner",            False),
        AppEntry("PuTTY",               "PuTTY.PuTTY",                           False),
    ]),

    # -----------------------------------------------------------------------
    # 25. Security & Passwords
    # -----------------------------------------------------------------------
    CategoryEntry("security_passwords", False, [
        AppEntry("Bitwarden",    "Bitwarden.Bitwarden",                 False),
        AppEntry("KeePassXC",    "KeePassXCTeam.KeePassXC",             False),
        AppEntry("1Password",    "AgileBits.1Password",                 False),
        AppEntry("Malwarebytes", "Malwarebytes.Malwarebytes",           False),
        AppEntry("ProtonVPN",    "Proton.ProtonVPN",                    False),
        AppEntry("NordVPN",      "NordVPN.NordVPN",                     False),
    ]),

    # -----------------------------------------------------------------------
    # 26. System Utilities
    # -----------------------------------------------------------------------
    CategoryEntry("system_utilities", False, [
        AppEntry("PowerToys",          "Microsoft.PowerToys",                   True),
        AppEntry("Everything",         "voidtools.Everything",                  True),
        AppEntry("Rufus",              "Rufus.Rufus",                           False),
        AppEntry("Ventoy",             "Ventoy.Ventoy",                         False),
        AppEntry("TreeSize Free",      "JAMSoftware.TreeSize.Free",             False),
        AppEntry("WizTree",            "AntibodySoftware.WizTree",              False),
        AppEntry("BleachBit",          "BleachBit.BleachBit",                   False),
        AppEntry("Sysinternals Suite", "Microsoft.Sysinternals",                False),
        AppEntry("Process Lasso",      "BitSum.ProcessLasso",                   False),
        AppEntry("Revo Uninstaller",   "RevoUninstaller.RevoUninstaller",       False),
    ]),

    # -----------------------------------------------------------------------
    # 27. Hardware Monitoring
    # -----------------------------------------------------------------------
    CategoryEntry("hardware_monitoring", False, [
        AppEntry("HWiNFO",          "REALiX.HWiNFO",                    True),
        AppEntry("CPU-Z",           "CPUID.CPU-Z",                       False),
        AppEntry("GPU-Z",           "TechPowerUp.GPU-Z",                 False),
        AppEntry("HWMonitor",       "CPUID.HWMonitor",                   False),
        AppEntry("MSI Afterburner", "Guru3D.Afterburner",                False),
        AppEntry("Core Temp",       "ALCPU.CoreTemp",                    False),
        AppEntry("CrystalDiskInfo", "CrystalDewWorld.CrystalDiskInfo",   False),
    ]),

    # -----------------------------------------------------------------------
    # 28. Drivers & Hardware
    # -----------------------------------------------------------------------
    CategoryEntry("drivers_hardware", False, [
        AppEntry("NVIDIA App",              "Nvidia.NvidiaApp",                         False),
        AppEntry("Intel DSA",               "Intel.IntelDriverAndSupportAssistant",     False),
        AppEntry("Snappy Driver Installer", "GlennDelahoy.SnappyDriverInstallerOrigin", False),
    ]),
]


# --------------------------------------------------------------------------
# Standalone smoke test / self-validation
# --------------------------------------------------------------------------

if __name__ == "__main__":
    _snake_re = re.compile(r"^[a-z][a-z0-9_]*$")

    all_apps: list[AppEntry] = [
        app
        for cat in APPS_CATALOG
        for app in cat.apps
    ]

    # 1. Total app count
    # NOTE: spec states 176 but the supplied data enumerates 177 apps.
    #       Assert 177 (the actual count). If you need to drop an app,
    #       remove it from the catalog above and update this assertion.
    assert len(all_apps) == 177, (
        f"CHECK 1 FAILED — expected 177 apps, got {len(all_apps)}"
    )

    # 2. Recommended count
    rec_count = sum(1 for a in all_apps if a.recommended)
    assert rec_count == 18, (
        f"CHECK 2 FAILED — expected 18 recommended, got {rec_count}: "
        + str([a.name for a in all_apps if a.recommended])
    )

    # 3. Category count
    assert len(APPS_CATALOG) == 28, (
        f"CHECK 3 FAILED — expected 28 categories, got {len(APPS_CATALOG)}"
    )

    # 4. Exactly 2 categories default_expanded=True
    expanded = [c.key for c in APPS_CATALOG if c.default_expanded]
    assert expanded == ["browsers", "communication"], (
        f"CHECK 4 FAILED — expanded categories: {expanded}"
    )

    # 5. All winget_id values unique
    seen_ids: set[str] = set()
    dupes: list[str] = []
    for app in all_apps:
        if app.winget_id in seen_ids:
            dupes.append(app.winget_id)
        seen_ids.add(app.winget_id)
    assert not dupes, f"CHECK 5 FAILED — duplicate winget IDs: {dupes}"

    # 6. All category keys unique
    seen_keys: set[str] = set()
    dupe_keys: list[str] = []
    for cat in APPS_CATALOG:
        if cat.key in seen_keys:
            dupe_keys.append(cat.key)
        seen_keys.add(cat.key)
    assert not dupe_keys, (
        f"CHECK 6 FAILED — duplicate category keys: {dupe_keys}"
    )

    # 7. Category keys match snake_case (lowercase letters, digits, underscores)
    bad_keys = [c.key for c in APPS_CATALOG if not _snake_re.match(c.key)]
    assert not bad_keys, (
        f"CHECK 7 FAILED — non-snake_case category keys: {bad_keys}"
    )

    # Summary
    print("All 7 checks passed.")
    print(f"  Categories : {len(APPS_CATALOG)}")
    print(f"  Total apps : {len(all_apps)}")
    print(f"  Recommended: {rec_count}")
    print(f"  Expanded   : {expanded}")
    print()
    print("Recommended apps:")
    for app in all_apps:
        if app.recommended:
            print(f"  *  {app.name:<35} {app.winget_id}")
