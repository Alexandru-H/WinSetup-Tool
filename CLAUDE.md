# WinSetup Tool v2 — Specificație pentru Claude Code

## 1. ROL & OBIECTIV

Ești senior Python/Qt developer. Construiești de la zero, împreună cu mine, **WinSetup Tool v2** — o aplicație desktop PySide6 care automatizează setup-ul unui PC după un clean install de Windows 11.

Livrăm **un fișier o dată**, testăm, poliszăm, apoi trecem la următorul. Fără grabă, fără "let me also add X while I'm here". Disciplină.

---

## 2. CONTEXT UTILIZATOR

- Nume: Alexandru Hoaghea, Sibiu, RO
- Python 3.14.3, Windows 11 24H2 (IoT LTSC)
- Prefer comunicare **directă, fără teorie inutilă**, mix RO/EN e OK
- La bug-uri: îți dau **fișierul curent real**, nu ce ai generat ultima dată (evităm drift-ul)
- Schimbări arhitecturale: **discutăm ÎNAINTE** de a scrie cod

---

## 3. STACK TEHNOLOGIC

| Componentă | Alegere |
|---|---|
| Runtime | Python 3.14 |
| UI | PySide6 (Qt 6) |
| System calls | `subprocess` + PowerShell + `ctypes` |
| Metrics | `psutil` |
| Build | PyInstaller onefile + `main.spec` + runtime hook |
| Installer | **Inno Setup** (`.iss`) cu bootstrap winget |

---

## 4. PRINCIPII NON-NEGOTIABLE

Astea nu se încalcă niciodată. Dacă un feature ar cere încălcarea, oprește-te și întreabă.

1. **Separation of concerns strictă**
   - `app/system/` **nu** importă nimic din Qt. Zero. Returnează date/exceptions.
   - `app/ui/` **nu** cheamă `subprocess` direct. Folosește doar `app/system/`.
   - `app/core/` e utilitar pur (paths, settings, i18n, logging).

2. **Toate path-urile prin `core/paths.py`**
   - Un singur loc care știe despre `sys._MEIPASS`, `portable.txt`, `%LOCALAPPDATA%`.
   - Nicăieri altundeva nu facem `os.path.dirname(__file__)` sau similar.

3. **Logging din minutul 1** (nu adăugat la final). `core/logging_setup.py` configurează un logger root cu rotate file handler în `%LOCALAPPDATA%\WinSetupTool\logs\`.

4. **Toate task-urile lungi în `QThread`** — winget, DISM, registry batch, enumerări lungi. UI-ul **nu îngheață niciodată**. Folosim un pattern unic `Worker(QObject)` + signals (`progress`, `finished`, `error`).

5. **i18n integrat din start**
   - Fiecare string vizibil prin `tr("key")`.
   - Fișiere JSON: `resources/translations/ro.json`, `en.json`.
   - **Niciodată hardcodat** în RO cu plan de "adăugăm EN la final".

6. **Settings și Theme injectate, nu singleton-uri globale**
   - `MainWindow(settings, theme_mgr, locale_mgr)` le primește ca parametri.
   - Paginile le primesc tot prin constructor.

7. **PowerShell wrapper unic** (`system/powershell.py`)
   - Argumentele numerice trec ca parametri separați, **nu concatenate în string** (PowerShell interpretează `MB` ca multiplier și strică silențios).
   - Escape corect pentru path-uri cu spații.

8. **Hot-swap theme & locale fără restart** (detalii la §8).

---

## 5. STRUCTURA DE FIȘIERE

```
winsetup/
├── main.py                      # entry point subțire (~40 linii)
├── main.spec                    # PyInstaller onefile
├── hook-pyside6-fix.py          # runtime hook (shiboken6 _MEIPASS fix)
├── requirements.txt
├── portable.txt                 # opțional → activează portable mode
├── installer/
│   └── winsetup.iss             # Inno Setup script (bootstrap winget)
│
├── app/
│   ├── __init__.py
│   ├── core/
│   │   ├── paths.py             # SINGURUL loc cu path logic
│   │   ├── settings.py          # JSON + defaults + safe save
│   │   ├── logging_setup.py
│   │   ├── elevation.py         # UAC detect + re-launch
│   │   ├── i18n.py              # LocaleManager(QObject) + localeChanged signal
│   │   └── theming.py           # ThemeManager(QObject) + themeChanged signal
│   │
│   ├── system/                  # ZERO Qt aici
│   │   ├── powershell.py        # wrapper comun
│   │   ├── hardware.py          # psutil + GPU registry fallback (IoT LTSC)
│   │   ├── wallpaper.py         # ctypes SystemParametersInfoW
│   │   ├── winget.py            # detect + install + uninstall + search
│   │   ├── registry.py          # tweaks + rollback snapshot
│   │   ├── services.py
│   │   ├── tasks.py             # scheduled tasks
│   │   ├── appx.py              # UWP enum + remove
│   │   └── win32apps.py         # uninstall Win32
│   │
│   ├── ui/
│   │   ├── main_window.py
│   │   ├── sidebar.py           # CARVE-OUT pattern (§9)
│   │   ├── titlebar.py          # DWM dark/light sync cu tema
│   │   ├── widgets/
│   │   │   ├── page_base.py
│   │   │   ├── card.py          # card generic (Home, stats)
│   │   │   ├── app_card.py
│   │   │   ├── toggle.py
│   │   │   ├── segmented.py     # Dark/Light, EN/RO
│   │   │   └── themed_widget.py # mixin refresh_theme()
│   │   └── pages/
│   │       ├── home_page.py
│   │       ├── apps_page.py
│   │       ├── wallpaper_page.py
│   │       ├── optimizations_page.py
│   │       ├── debloat_page.py
│   │       └── uninstall_page.py
│   │
│   └── resources/
│       ├── icons/               # SVG preferate
│       ├── translations/
│       │   ├── ro.json
│       │   └── en.json
│       └── data/
│           ├── apps_catalog.json
│           ├── debloat_lists.json
│           └── optimizations.json
```

---

## 6. LECȚII ÎNVĂȚATE (fix-uri obligatorii din start)

Astea **trebuie să fie în cod de la început**, nu descoperite mai târziu:

### 6.1 PyInstaller + PySide6 onefile
- `SPECPATH` e **deja un director** — NU folosi `os.path.dirname(SPECPATH)`.
- `shiboken6` necesită **runtime hook** care copiază directorul său în `sys._MEIPASS` la pornire (altfel crash la import).
- Path-uri icon: fallback `_MEIPASS` → director exe → director script.

### 6.2 Settings path (SETTINGS_PATH safe)
```python
# Prioritate:
# 1. Portable: <exe_dir>/config/ dacă există <exe_dir>/portable.txt
# 2. Frozen:  %LOCALAPPDATA%/WinSetupTool/config/
# 3. Dev:     <script_dir>/config/
```
NU `sys._MEIPASS` (read-only), NU `%TEMP%`.

### 6.3 DWM titlebar respectă tema
```python
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
value = 1 if theme == "dark" else 0   # AMBELE, nu doar 1
```
Re-apply la fiecare theme switch, nu doar la startup.

### 6.4 High-DPI fără blur
```python
os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "1"
os.environ["QT_SCALE_FACTOR_ROUNDING_POLICY"] = "PassThrough"
```
Ambele. Doar prima = text blurat pe 125%/150% scaling.

### 6.5 QPalette completă
Setează și `ToolTipBase`, `ToolTipText`, `PlaceholderText` — altfel tooltips moștenesc culori default.

### 6.6 QTimer anti-stacking
Orice `QTimer.singleShot` recursiv (ex: retry la resize) necesită **flag guard + limită retry** (max 10). Fără astea, coada crește la infinit.

### 6.7 Layout: `addSpacing()` > CSS `margin` pe QLabel
Pentru spațiere fiabilă. CSS margin pe label e inconsistent.

### 6.8 Hide footer: `setVisible(False)`, nu `setFixedHeight(0)`
A doua variantă lasă artefacte de pixeli.

### 6.9 GPU detection IoT LTSC
`wmic` lipsește. Fallback la registry:
```
HKLM\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}
```
Enumerăm subkey-urile, citim `DriverDesc`.

---

## 7. CORE MODULES — schițe obligatorii

### 7.1 `core/paths.py`
Expune:
- `IS_FROZEN: bool`
- `IS_PORTABLE: bool`
- `RESOURCES_DIR: Path` (read-only, _MEIPASS sau `app/resources/`)
- `CONFIG_DIR: Path` (writable)
- `LOGS_DIR: Path`
- `resource(name: str) -> Path` (helper pentru assets)
- `settings_file() -> Path`

### 7.2 `core/settings.py`
- `Settings` class cu `load()`, `save()`, `get(key, default)`, `set(key, value)`
- `save()` are try/except cu fallback la AppData
- Defaults: `{"theme": "dark", "locale": "ro", "first_run": true}`

### 7.3 `core/theming.py` (ThemeManager QObject)
```python
class ThemeManager(QObject):
    themeChanged = Signal(dict)   # emite noul dict de culori

    def __init__(self, settings):
        ...

    def current(self) -> dict: ...
    def set_theme(self, name: str):  # "dark" | "light"
        # 1. update intern
        # 2. settings.set + save
        # 3. emit themeChanged(new_dict)
```

Paletele: v. §8.3.

### 7.4 `core/i18n.py` (LocaleManager QObject)
```python
class LocaleManager(QObject):
    localeChanged = Signal(str)

    def tr(self, key: str, **fmt) -> str: ...
    def set_locale(self, code: str): ...  # "ro" | "en"
```

---

## 8. HOT-SWAP THEME & LOCALE (fără restart, cu animație)

### 8.1 Pattern general
Fiecare widget custom moștenește mixin-ul `ThemedWidget`:
```python
class ThemedWidget:
    def connect_theme(self, theme_mgr: ThemeManager):
        self._theme_mgr = theme_mgr
        theme_mgr.themeChanged.connect(self.refresh_theme)
        self.refresh_theme(theme_mgr.current())

    def refresh_theme(self, theme: dict):
        """Override: re-aplică stylesheet cu noile culori."""
        raise NotImplementedError
```
Similar `LocalizedWidget` cu `refresh_locale()`.

### 8.2 Animație switch (fade scurt, non-glitch)
În `MainWindow.switch_theme()`:
1. `QGraphicsOpacityEffect` pe `central_widget`
2. `QPropertyAnimation` opacity `1.0 → 0.55` (160ms, `OutCubic`)
3. La `finished`: `theme_mgr.set_theme(new)` + `QTimer.singleShot(0, ...)` pentru ca Qt să proceseze repaint
4. `QPropertyAnimation` opacity `0.55 → 1.0` (200ms, `InCubic`)

**Niciodată fade la 0.0** — fade mic (0.55) = vezi tranziția, fără black flash.

### 8.3 Palete (obligatorii)
```python
DARK = {
    "body_bg":    "#0d0d1c",   # exterior sidebar + body
    "panel_bg":   "#141428",   # sidebar + content area (SAME color — cheia design-ului)
    "card":       "#1a1a30",
    "card_hover": "#22223c",
    "accent":     "#3b82f6",
    "accent_h":   "#2563eb",
    "text_pri":   "#e2e8f0",
    "text_sec":   "#6b7280",
    "border":     "#22223c",
    "ok":         "#22c55e",
    "warn":       "#f59e0b",
    "err":        "#ef4444",
}

LIGHT = {
    "body_bg":    "#e5e7eb",
    "panel_bg":   "#f8fafc",
    "card":       "#ffffff",
    "card_hover": "#f0f4fb",
    "accent":     "#3b82f6",
    "accent_h":   "#2563eb",
    "text_pri":   "#0f172a",
    "text_sec":   "#64748b",
    "border":     "#cbd5e1",
    "ok":         "#16a34a",
    "warn":       "#d97706",
    "err":        "#dc2626",
}
```

### 8.4 Locale switch
Fără animație (schimbarea textului e imperceptibilă ca glitch). Widget-urile implementează `refresh_locale()` și reasignează textele.

---

## 9. SIDEBAR DESIGN (CARVE-OUT PATTERN)

**Acesta e design-ul, nu-l schimba fără să discutăm.**

### 9.1 Concept vizual
Sidebar-ul și content area au **aceeași culoare** (`panel_bg` = `#141428`). În jurul sidebar-ului, body-ul este **mai întunecat** (`body_bg` = `#0d0d1c`).

În dreptul item-ului activ, sidebar-ul pare să se "deschidă" către conținut — item-ul activ arată ca un tab care iese din sidebar și se conectează la content. Efectul se obține cu **două panel-uri dark** (culoarea `body_bg`) poziționate exact **deasupra** și **dedesubtul** item-ului activ, cu `border-radius` asimetric care creează curbura:

```
┌──────────────┬──────────────────────────┐
│ sidebar      │ content area             │  ← ambele = panel_bg
│ (panel_bg)   │ (panel_bg)               │
│              │                          │
│  □ Home      │                          │
│              │                          │
│╲▁▁▁▁▁▁▁▁▁▁╲ │                          │  ← panel DARK top
│              │                          │     border-radius: 0 0 18px 0
│   ■ Apps ───┼──►                        │  ← ACTIVE item (flush cu content)
│              │                          │
│╱▔▔▔▔▔▔▔▔▔▔╱ │                          │  ← panel DARK bottom
│              │                          │     border-radius: 0 18px 0 0
│  □ Debloat   │                          │
│              │                          │
└──────────────┴──────────────────────────┘
    ↑
  body_bg la stânga sidebar-ului
```

### 9.2 Implementare
```python
class Sidebar(QFrame):
    # Structura internă:
    # QVBoxLayout cu:
    #   - NavButton (Home)
    #   - CarvePanel top (visible doar când Apps e activ)
    #   - NavButton (Apps) — ACTIVE
    #   - CarvePanel bottom (visible doar când Apps e activ)
    #   - NavButton (Debloat)
    #   ...
```

**Mai simplu și mai curat:** un `QWidget` absolute-positioned peste sidebar, care desenează cele două panel-uri la coordonatele item-ului activ. La navigație, animăm poziția cu `QPropertyAnimation` (150ms, `OutCubic`).

```python
class CarveOverlay(QWidget):
    """Desenează două dreptunghiuri dark deasupra și dedesubt activeRect."""
    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor(self._theme["body_bg"]))
        p.setPen(Qt.NoPen)

        # Panel TOP: de la y=0 la activeRect.top(), colț dreapta-jos rotund
        top = QPainterPath()
        top.addRoundedRect(0, 0, self.width(),
                           self._active_y, 0, 0)
        # manual: colțuri specifice — folosim moveTo/lineTo/arcTo
        # sau: addRect + addRoundedRect overlay pentru colțul inferior-dreapta
        ...

        # Panel BOTTOM: similar, colț dreapta-sus rotund
```

Raza: **18px**. Item activ: **42px înălțime**, font **11pt semibold**.

### 9.3 Cerințe animație
- Schimbare item activ → `QPropertyAnimation` pe `_active_y` (150ms)
- Hover pe un item inactiv → fade ușor pe background (100ms)
- Zero janks, zero glitch. Testăm pe 60Hz și 144Hz.

---

## 10. APPS CATALOG (cu browsing, search, categorii)

### 10.1 Format `apps_catalog.json`
```json
{
  "version": 2,
  "categories": [
    {
      "id": "browsers",
      "name_ro": "Browsere",
      "name_en": "Browsers",
      "icon": "globe.svg",
      "accent": "#1e3a5f"
    },
    ...
  ],
  "apps": [
    {
      "id": "firefox",
      "name": "Mozilla Firefox",
      "winget_id": "Mozilla.Firefox",
      "category": "browsers",
      "description_ro": "Browser open-source, accent pe confidențialitate.",
      "description_en": "Open-source browser focused on privacy.",
      "icon": "firefox.svg",
      "size_mb": 210,
      "tags": ["browser", "privacy", "open-source"],
      "popular": true
    },
    ...
  ]
}
```

### 10.2 Categorii inițiale (15)
Browsere, Comunicare, Gaming, Media & Audio, Video & Editare, Design & Grafică, Productivitate, Securitate, Download & Transfer, Arhivare, Dev Tools, Runtime-uri, Sistem & Utilitar, Hardware, Rețea & Remote.

### 10.3 Browsing pe pagina Apps
- **Sidebar stâng** al paginii: listă categorii cu count per categorie
- **Top bar**: search (filtrează live pe nume + tag-uri) + sort (A-Z, popular)
- **Grid**: `AppCard` în 3-4 coloane responsive
- **Bottom bar**: `N selectate — ~X MB total — [Install All]`
- **Mod "Popular"**: tab special care filtrează `popular: true`

### 10.4 Seed minim pentru început
Începem cu **~35 apps** acoperind toate categoriile (2-3 per categorie). Extindem pe parcurs. Tu mă întrebi ce să pun, nu inventezi tu 110 apps random.

---

## 11. INSTALLER SETUP.EXE (bootstrap winget)

### 11.1 Tool: Inno Setup 6
Free, industry standard, creează exe compact cu uninstaller proper.

### 11.2 Flux
```
1. Launch setup.exe
2. Welcome page
3. License (opțional) + Install dir (default: Program Files\WinSetup Tool)
4. Checkbox "Instalează Windows Package Manager (winget) dacă lipsește" (default: ON)
5. Install page:
   a. Dacă checkbox ON → verifică winget:
       - Run: powershell Get-Command winget -ErrorAction SilentlyContinue
       - Dacă absent: download + install (vezi 11.3)
   b. Copy fișiere app
   c. Create shortcuts
6. Finish + Launch option
```

### 11.3 Instalare winget programatic
```powershell
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

# 1. VCLibs (dependență)
$vclibs = "$env:TEMP\vclibs.appx"
Invoke-WebRequest "https://aka.ms/Microsoft.VCLibs.x64.14.00.Desktop.appx" -OutFile $vclibs
Add-AppxPackage $vclibs

# 2. UI.Xaml 2.8 (dependență)
# ... (download + install)

# 3. winget propriu-zis
$winget = "$env:TEMP\winget.msixbundle"
Invoke-WebRequest "https://aka.ms/getwinget" -OutFile $winget
Add-AppxPackage $winget
```

### 11.4 Fișier `installer/winsetup.iss`
Schelet:
```iss
[Setup]
AppName=WinSetup Tool
AppVersion=2.0.0
AppPublisher=Alexandru Hoaghea
DefaultDirName={autopf}\WinSetup Tool
DefaultGroupName=WinSetup Tool
OutputBaseFilename=WinSetupTool-Setup
Compression=lzma2/max
SolidCompression=yes
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64
WizardStyle=modern

[Tasks]
Name: "installwinget"; Description: "Instalează Windows Package Manager dacă lipsește"; GroupDescription: "Dependențe:"

[Files]
Source: "..\dist\WinSetupTool.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\assets\icon.ico"; DestDir: "{app}"

[Icons]
Name: "{group}\WinSetup Tool"; Filename: "{app}\WinSetupTool.exe"; IconFilename: "{app}\icon.ico"
Name: "{autodesktop}\WinSetup Tool"; Filename: "{app}\WinSetupTool.exe"; Tasks: desktopicon

[Code]
procedure InstallWinget();
// apel PowerShell cu scriptul de la 11.3
// ExtractTemporaryFile + Exec
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    if IsTaskSelected('installwinget') then
      InstallWinget();
end;
```

---

## 12. ORDINEA DE CONSTRUCȚIE

Strict în această ordine. **Nu săriți înainte.**

### Faza 0 — Fundație (fără UI vizibil)
1. `requirements.txt` + structura goală de foldere + `.gitignore`
2. `app/core/paths.py`
3. `app/core/logging_setup.py`
4. `app/core/settings.py`
5. `app/core/elevation.py`
6. `app/core/i18n.py` + `resources/translations/ro.json` + `en.json` (keys inițiale)
7. `app/core/theming.py`
8. `main.py` (scheletul de intrare: init settings → theme → locale → MainWindow placeholder)
9. **Checkpoint**: rulează, nu crapă, creează `%LOCALAPPDATA%/WinSetupTool/config/settings.json`

### Faza 1 — Shell UI
10. `app/ui/widgets/themed_widget.py` (mixin)
11. `app/ui/titlebar.py` (DWM sync)
12. `app/ui/sidebar.py` cu CarveOverlay (doar vizual, click-uri no-op)
13. `app/ui/main_window.py` cu `QStackedWidget` + 6 pagini placeholder
14. `app/ui/widgets/segmented.py` (Dark/Light, EN/RO) + conectare la managers
15. **Checkpoint**: pornește, schimbă tema cu fade, schimbă locale, sidebar animează între item-uri. Nicio funcționalitate de sistem încă.

### Faza 2 — Pagini (una câte una, system + UI împreună)
16. `system/hardware.py` + `pages/home_page.py`
17. `system/wallpaper.py` + `pages/wallpaper_page.py`
18. `system/registry.py` + `pages/optimizations_page.py`
19. `system/services.py` + `system/tasks.py` + `system/appx.py` + `pages/debloat_page.py`
20. `system/winget.py` + `pages/apps_page.py`
21. `system/win32apps.py` + `pages/uninstall_page.py`

### Faza 3 — Livrare
22. `hook-pyside6-fix.py` + `main.spec` + primul build de test
23. `installer/winsetup.iss` + primul setup.exe
24. Polish final: iconițe toate, traduceri complete, edge cases pe VM curat

---

## 13. STIL DE LUCRU

- **Un fișier per răspuns.** Livrezi codul complet al unui fișier, aștepți feedback, apoi mergi la următorul.
- **Nu inventa feature-uri** peste ce e în spec. Dacă vezi ceva ce "ar fi bine să adăugăm", întrebi înainte.
- **Când fix-uiești un bug:** îți dau fișierul real, îl editezi minimal, îmi dai diff-ul + explicația.
- **Înainte de schimbare arhitecturală:** oprește-te, explică trade-off-urile, așteaptă confirmare.
- **Mesaje în română casual**, cod și comentarii în engleză (standard profesional).
- **Commit messages în engleză**, conventional commits: `feat:`, `fix:`, `refactor:`, `chore:`.

---

## 14. CHECKLIST ÎNAINTE DE FIECARE `create_file`

Pentru fiecare fișier nou, verifici în minte:

- [ ] Are docstring la început cu scopul modulului?
- [ ] Import-urile sunt grupate (stdlib / third-party / local)?
- [ ] Dacă e UI: folosește `ThemedWidget`/`LocalizedWidget` dacă afișează culori/text?
- [ ] Dacă e system: **zero** importuri din PySide6?
- [ ] Path-uri prin `core.paths`, nu manual?
- [ ] Strings vizibile prin `tr()`, nu hardcodate?
- [ ] Long tasks în QThread/Worker?
- [ ] Logging apelat pe ramuri de eroare?
- [ ] Type hints pe funcții publice?

---

## 15. PRIMA TA ACȚIUNE

Creează `requirements.txt` + structura de foldere goală (cu `__init__.py` unde e nevoie) + `.gitignore`. Apoi oprește-te și așteaptă să-ți zic "ok, mergem la `paths.py`".

**Nu** scrie `paths.py`, `settings.py` sau altceva în același mesaj. Un pas o dată.
