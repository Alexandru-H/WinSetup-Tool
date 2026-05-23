# WinSetupV2 — Decisions & Bug History

This document captures decisions made during development with their reasoning, and bugs encountered with their resolutions. Read when debugging similar issues or considering architectural changes.

---

## Architectural Decisions

### Why per-module deliberate widget duplication

`OptimizationToggle`, `DebloatToggle`, `AppCard` are SEPARATE widgets despite structural similarity.

**Reasoning:** Each module's widget evolves independently:
- `OptimizationToggle` has restart-required ⟲ badge that others don't
- `DebloatToggle` has warning ⚠️ for risky tier that others don't
- `AppCard` has install progress + version display that others don't

Generalizing into one base class created tight coupling. When we tried (early v1.0 attempt), every per-module visual tweak required modifying the base class, which broke other modules.

**Constraint:** When adding new widgets in v1.1+, prefer duplication over generalization. Only extract shared utilities (like `ThemedCheckboxDelegate`) when the abstraction is genuinely module-agnostic.

### Why bulk_detect for Debloat instead of sequential is_installed()

Original spec called `is_installed(package_name)` for each of 44 AppX packages on page load. That triggered 44 separate PowerShell processes — total 30-60 seconds, UI frozen.

**Solution:** `bulk_detect()` runs ONE PowerShell command (`Get-AppxPackage`) returning all installed packages, then cross-checks against catalog locally.

**Implementation:** `app/system/appx.py::bulk_detect()` returns `dict[str, bool]` keyed by package_name.

**Constraint:** When adding new system-detection features in v1.1+, always check if bulk operation is available before iterating. PowerShell startup cost is ~500ms; sequential calls explode.

### Why uninstall scanner runs in QThread

`scan_installed_apps()` takes ~3 seconds (registry walk + winget enrichment + dedup). Running on UI thread freezes the page on open.

**Solution:** `_ScanWorker` QThread in `UninstallPage` runs scan, emits results via signal, page populates table when done.

**Constraint:** Any operation > 100ms must run in QThread, not UI thread. This includes file I/O, subprocess, registry walks, network requests.

### Why threading.Event for Uninstall worker pause/resume

After successful uninstall, page must show leftover dialog and wait for user before next app. Worker is on QThread, dialog is on UI thread.

**Solution:** `worker._continue_event = threading.Event()`. Worker calls `wait(timeout=300)` after emitting `leftovers_found`. Page calls `event.set()` after dialog closes.

**Why not Qt signals:** Cross-thread signal handlers run async — page would need to call back to worker, but worker is blocked. `threading.Event` is the simplest cross-thread sync primitive.

**Constraint:** When workers need to pause for UI input, use `threading.Event`. Don't try to do it with QSemaphore or QMutex — `Event` is purpose-built for this and works perfectly.

### Why one Q_ARG signature

All `QMetaObject.invokeMethod` calls use `Q_ARG("QVariantList", list(items))`.

**Reasoning:** PySide6 6.8+ broke implicit list conversion. `Q_ARG(list, ...)` works on 6.7 but fails on 6.8+ with "QVariant cannot convert".

**Constraint:** Always use the string `"QVariantList"` form. Test on PySide6 ≥ 6.8.

### Why `accept_source_agreements` flag in winget calls

Old winget versions (< 1.6) prompted interactively for source agreements on first install. `subprocess.run` doesn't pass stdin, so winget hung indefinitely.

**Solution:** All winget calls include `--accept-source-agreements --accept-package-agreements`.

**Constraint:** Don't remove these flags even if testing on a machine where they seem unnecessary. Other users will have stale winget configs.

### Why settings live in Settings class, not module-level globals

`settings.json` is read at startup, written on changes. Multiple components need read access.

**Solution:** `Settings` class with `load()`, `save()`, `get(key)`, `set(key, value)` methods. Single instance passed to all components.

**Anti-pattern avoided:** Module-level dict that components read directly. Causes initialization order bugs (component A reads before component B writes).

### Why theme palette is dict not dataclass

Original v1.0 attempt used `@dataclass class ThemePalette` for type safety. Switched to `dict[str, str]`.

**Reasoning:**
- Dynamic key access needed in QSS templates: `f"background: {palette['card']};"`
- New tokens added per-module without changing palette class signature
- Theme refactor in v1.1 may add/remove tokens — dict is forward-compatible

**Constraint:** Use string keys. Document tokens in CLAUDE.md. Don't introduce dataclass back unless v2.0 reorganization.

---

## Bugs Encountered & Resolutions

### Bug: Sidebar carve-out attempts that failed

**Symptom:** Need a curved cut on the right edge of sidebar where it meets the active panel.

**Failed approaches:**
1. **QRegion masks** — clipping rectangle with circular subtraction. Sub-pixel artifacts, antialiasing failed at radius edges. Looked jagged.
2. **CarveOverlay child widget** — separate widget painted over sidebar with transparent fill + alpha mask. Rendering order issues with sibling widgets, z-order glitches on hover.

**Working approach:** Single-widget `QPainterPath.subtracted()` in `paintEvent`:
```python
exterior = QPainterPath()
exterior.addRoundedRect(rect, 8, 8)  # exterior radius

carve = QPainterPath()
carve.addEllipse(QPointF(carve_cx, carve_cy), 18, 18)  # carve radius

result = exterior.subtracted(carve)
painter.fillPath(result, panel_color)
```

**Why it works:** Single `paintEvent`, no z-order issues, antialiased natively by Qt's path renderer.

**Constraint:** Don't try to "modernize" with QML or QGraphicsEffect. The path approach is correct and stable.

### Bug: Theme switch caused color flash on AppCard

**Symptom:** Switching dark↔light caused 50-100ms flash of intermediate color before correct theme rendered.

**Cause:** `ThemeManager.themeChanged` signal fired before all components had updated palettes. AppCard repainted with old palette during transition.

**Fix:** Added `QTimer.singleShot(20, self.update)` after palette update in AppCard. Defers repaint by 1 frame.

**Alternative considered:** Block all repaints during transition. Too invasive — would break unrelated animations.

**Constraint:** When debugging theme transition glitches, check if the affected widget calls `update()` synchronously in `_apply_theme()`. Defer with QTimer if flash visible.

### Bug: Manifest XML invalid — UAC didn't fire

**Symptom:** PyInstaller built `.exe` with manifest, but Windows didn't show UAC prompt at launch.

**Cause:** Original manifest had duplicate `<application>` element (one for `<compatibility>`, one for `<windowsSettings>`). Invalid XML — Windows manifest parser silently ignored it.

**Failed fix attempt 1:** Use `asmv3:application` namespace prefix to disambiguate. PyInstaller embedded it, but Windows still didn't recognize.

**Final solution (works):**
1. Simplify manifest to UAC-only (remove `<compatibility>` and `<windowsSettings>` sections — DPI is set imperatively in `main.py` anyway)
2. Add `relaunch_as_admin()` fallback in `main.py` that runs ShellExecuteW with "runas" verb if `is_elevated()` returns False AND `sys.frozen` is True

**Why dual approach:** Manifest may or may not work on different Windows builds. Auto-elevation in code works regardless — bullet-proof.

**Constraint:** Trust the auto-elevation in code more than the manifest. Don't remove it even if manifest seems to work in testing.

### Bug: Icon missing in title bar / taskbar despite embedded in EXE

**Symptom:** PyInstaller built `.exe` with `icon='Furina_icon.ico'`. Windows Explorer showed the icon on file. App's title bar and taskbar showed default icon.

**Cause:** Two separate icon mechanisms:
1. **Windows EXE resource** — set via PyInstaller's `icon=` parameter. This is what Explorer reads.
2. **Qt application icon** — set via `QApplication.setWindowIcon(QIcon(path))`. This is what title bar / taskbar show.

`main.py` code was correct: `icon_path = resource("icons/app.ico")` + `app.setWindowIcon(QIcon(...))`. But `app/resources/icons/app.ico` didn't exist — file was at `Furina_icon.ico` in project root only.

**Fix:** Copy `Furina_icon.ico` to `app/resources/icons/app.ico`. PyInstaller `datas` already bundles the entire `app/resources/` tree, so it's included automatically in frozen mode.

**Constraint:** Keep BOTH copies. Project root `Furina_icon.ico` for `.spec` files. `app/resources/icons/app.ico` for runtime `setWindowIcon()`.

### Bug: Inno Setup script — GUID parsed as constant

**Symptom:** `#define MyAppId "{8F9C2E1A-3B4D-...}"` caused Inno Setup compile error: "Unknown identifier 'app'".

**Cause:** Inno Setup uses `{...}` for built-in constants like `{app}`, `{group}`. A literal GUID `{8F9C2E1A-...}` was interpreted as a constant.

**Fix:** Double curly braces `{{8F9C2E1A-...}` — this is Inno Setup's escape for literal `{`.

```iss
#define MyAppId "{{8F9C2E1A-3B4D-4E5F-A6B7-C8D9E0F1A2B3}"
```

**Constraint:** Whenever using `{...}` literally in `.iss` files (GUIDs, JSON, regex patterns), escape with `{{...}`.

### Bug: Inno Setup IsAppRunning() — IEnumVariant unsupported

**Symptom:** Original WMI query used `Procs._NewEnum` to iterate processes. Compile error: "Unknown identifier 'IEnumVariant'".

**Cause:** Inno Setup's Pascal Script doesn't support full COM enumerator interfaces. `_NewEnum` returns `IEnumVariant` which Pascal Script can't manipulate.

**Fix:** Use `Procs.Count > 0` instead of iterating:
```iss
function IsAppRunning(const FileName: string): Boolean;
var
  WMI: Variant;
  Procs: Variant;
begin
  WMI := CreateOleObject('WbemScripting.SWbemLocator');
  WMI := WMI.ConnectServer('.', 'root\CIMV2');
  Procs := WMI.ExecQuery('SELECT * FROM Win32_Process WHERE Name = "' + FileName + '"');
  Result := Procs.Count > 0;
end;
```

**Constraint:** When using WMI in Inno Setup, prefer `.Count` over enumeration. Pascal Script COM support is limited.

### Bug: Worktree files not visible in main repo

**Symptom:** During README delivery, Sonnet edited files in `.claude/worktrees/funny-swanson-8ccae8/` but main repo at `WinSetupV2/` didn't see them. `git status` showed nothing.

**Cause:** Claude Code worktrees are SEPARATE clones — they have their own working directory. Files written there exist in the worktree only, not in main repo.

**Detection:** `Get-ChildItem -Path . -Recurse -Filter "README*"` showed files at worktree path, not at root.

**Fix:** Manual `Copy-Item` from worktree to main repo:
```powershell
Copy-Item .\.claude\worktrees\funny-swanson-8ccae8\README.md .\
Copy-Item .\.claude\worktrees\funny-swanson-8ccae8\README.ro.md .\
```

Then `.gitignore` updated to exclude `.claude/` to prevent Git embedding warnings.

**Constraint:** When Claude Code edits files, verify they appear in main repo with `ls` before committing. If not, copy from worktree manually.

### Bug: PowerShell markdown rendering converted file paths

**Symptom:** User copy-pasted `git add README.md README.ro.md` from chat. PowerShell saw `git add [README.md](http://README.md) [README.ro.md](http://README.ro.md)` and threw "pathspec did not match any files".

**Cause:** Chat interface auto-converts plain text that matches filename patterns into markdown links when copying. `README.md` becomes `[README.md](http://README.md)` on paste.

**Fix:** Either type commands manually, or use simpler `git add .` to avoid filename arguments.

**Constraint:** When giving PowerShell commands to user, prefer wildcards (`git add .`) over explicit filenames when possible. Or warn user to type manually.

### Bug: PyInstaller MIMAPI64.dll / LIBPQ.dll warnings

**Symptom:** PyInstaller build logs show warnings about missing `MIMAPI64.dll` (Mimer SQL) and `LIBPQ.dll` (PostgreSQL).

**Cause:** PySide6's `QtSql` module ships drivers for various databases. Some require runtime DLLs from the database vendor.

**Resolution:** Non-fatal warnings. We exclude `PySide6.QtSql` in `.spec` files anyway, so these driver plugins are never loaded.

**Constraint:** Ignore these warnings. If they ever change to errors, verify `PySide6.QtSql` is in `excludes` list.

### Bug: IoT LTSC test environment limitations

**Symptom:** Debloat module showed all 44 toggles disabled with "Already removed". Apps catalog showed mixed results.

**Cause:** Windows 11 IoT LTSC ships without consumer AppX apps (Solitaire, Xbox Game Bar, BingNews, etc.). The detection logic worked correctly — there's just nothing to detect.

**Resolution:** Accept limitation. Use IoT LTSC for code testing + UI testing. Use VM Win11 normal or alternate PC for screenshots and live happy-path testing.

**Constraint:** When testing on IoT LTSC, "Already removed" is expected for most Debloat entries. Don't try to "fix" the detection.

---

## Things We Tried That Didn't Work

### Tried: Centralizing checkbox styling via QSS only

Goal: Make all checkboxes (AppCard, OptimizationToggle, UninstallPage table) share styling via Stylesheet.

**Result:** QSS for `QCheckBox::indicator` works for QCheckBox widgets, but NOT for `Qt.CheckStateRole` rendered by QTableView's default item delegate. Table cells use OS-native style, ignoring QSS.

**Solution:** Custom `ThemedCheckboxDelegate` painted manually with `QPainter`. Now all checkboxes look identical.

### Tried: Using QSettings instead of custom Settings class

Goal: Use Qt's `QSettings` for persistence (cross-platform, registry-backed on Windows).

**Result:** `QSettings` registry mode requires admin or per-user write to specific keys. INI mode worked but felt heavy for our simple JSON needs.

**Solution:** Custom `Settings` class wrapping `json.dump/load`. Simpler, more debuggable, works fine for our scale.

### Tried: Generalized worker base class

Goal: One `BaseWorker(QObject)` with shared cancel logic, then `InstallWorker(BaseWorker)`, `ApplyWorker(BaseWorker)`, etc.

**Result:** Workers diverged in subtle ways:
- `InstallWorker` cancels by terminating winget subprocess (slow, signal-based)
- `ApplyWorker` cancels by setting flag (each tweak function checks)
- `UninstallWorker` cancels both (subprocess + flag) AND has pause/resume

Trying to unify the cancel mechanism in base class led to over-engineering. Each worker has its own concerns.

**Solution:** Each worker is independent. Common pattern is documented in CLAUDE.md but not enforced via inheritance.

---

## Things to Reconsider in v1.1+

### Theme system — accent should NOT override panel_bg

Currently selecting a custom accent (Navy, Orchid, etc.) replaces the entire `panel_bg` token. This means:
- Dark Default + Navy → entire panel becomes Navy-tinted
- User can't have "Dark theme + Navy buttons"

**Goal:** Decouple. Theme (dark/light) controls panel/body/text. Accent controls buttons, progress, hover, focus rings only.

**Touches:** `theming.py`, `_compose_palette()`, `settings_page.py` accent picker, all 9 accent definitions.

**Risk:** Breaking existing screenshots / user mental model. Mitigation: clear changelog in v1.1 release notes.

### Sidebar animations

Current snappy/glitchy feel suggests:
- QPropertyAnimation timing not coordinated with theme update
- Possibly animation curves wrong (linear vs OutCubic)
- Possibly repaint triggered mid-animation

**Investigate first:** Is it visible only on theme change, or also on hover / page switch? Different fixes for different triggers.

### Optimizations card descriptions

44px → 56px upgrade is "trivial" but never done because i18n descriptions for all 32 tweaks haven't been written. ~30 min of writing.

Decision: Do this in v1.1 sprint with screenshots — descriptions help screenshots look richer.

---

## Validation Checklist for v1.1 Releases

When releasing v1.1.0:

1. ☐ All v1.0 features still work (regression test all 5 modules)
2. ☐ Theme refactor doesn't break existing user `settings.json`
3. ☐ Sidebar animations smooth on theme switch + page switch + hover
4. ☐ Screenshots captured at 1280x720 for README
5. ☐ Demo GIF under 5 MB (optimize with ScreenToGif compression)
6. ☐ Both READMEs updated with media paths
7. ☐ `dist/` rebuilt fresh (portable + folder + installer)
8. ☐ Installer install/uninstall verified clean
9. ☐ Git committed all changes
10. ☐ Tag `v1.1.0` annotated
11. ☐ User explicit confirmation for GitHub push
12. ☐ Push: `git push origin main --tags`
13. ☐ GitHub Release with both `.exe` files attached

---

**End of DECISIONS.md**
