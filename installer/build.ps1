# Build script for WinSetupTool — runs both onefile and onedir builds.
#
# Usage from project root (WinSetupV2):
#   .\installer\build.ps1
#   .\installer\build.ps1 -Clean        # remove dist/ and build/ first
#   .\installer\build.ps1 -OneFileOnly  # skip the onedir build
#   .\installer\build.ps1 -OneDirOnly   # skip the onefile build

param(
    [switch]$Clean,
    [switch]$OneFileOnly,
    [switch]$OneDirOnly
)

$ErrorActionPreference = "Stop"

# Resolve paths from project root (parent of this script's directory)
$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

Write-Host "Project root: $ProjectRoot" -ForegroundColor Cyan

# Locate PyInstaller — prefer venv, fall back to PATH
$PyInstaller = Join-Path $ProjectRoot ".venv\Scripts\pyinstaller.exe"
if (-not (Test-Path $PyInstaller)) {
    $PyInstaller = "pyinstaller"
    Write-Host "Note: using PyInstaller from PATH (no venv detected)" -ForegroundColor Yellow
}

# Validate prerequisites
$Required = @(
    "main.py",
    "Furina_icon.ico",
    "WinSetupTool.spec",
    "WinSetupTool-onedir.spec",
    "installer\manifest.xml",
    "installer\version_info.txt",
    "installer\runtime_hook_pyside6.py"
)
$missing = $false
foreach ($f in $Required) {
    if (-not (Test-Path $f)) {
        Write-Host "ERROR: Missing required file: $f" -ForegroundColor Red
        $missing = $true
    }
}
if ($missing) { exit 1 }

# Clean if requested
if ($Clean) {
    Write-Host "Cleaning previous builds..." -ForegroundColor Yellow
    Remove-Item -Recurse -Force "dist"  -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force "build" -ErrorAction SilentlyContinue
    Write-Host "Clean done." -ForegroundColor Yellow
}

# ── ONEFILE (portable) ──────────────────────────────────────────────────────
if (-not $OneDirOnly) {
    Write-Host ""
    Write-Host "=== Building ONEFILE (portable) ===" -ForegroundColor Green
    & $PyInstaller "WinSetupTool.spec" --noconfirm
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: Onefile build failed (exit $LASTEXITCODE)" -ForegroundColor Red
        exit 1
    }

    $OneFile = "dist\WinSetupTool.exe"
    if (Test-Path $OneFile) {
        $sizeMB = [math]::Round((Get-Item $OneFile).Length / 1MB, 1)
        Write-Host "  Portable: $OneFile  ($sizeMB MB)" -ForegroundColor Green
    } else {
        Write-Host "  WARNING: expected $OneFile not found" -ForegroundColor Yellow
    }
}

# ── ONEDIR (installer source) ───────────────────────────────────────────────
if (-not $OneFileOnly) {
    Write-Host ""
    Write-Host "=== Building ONEDIR (installer source) ===" -ForegroundColor Green
    & $PyInstaller "WinSetupTool-onedir.spec" --noconfirm
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: Onedir build failed (exit $LASTEXITCODE)" -ForegroundColor Red
        exit 1
    }

    $OneDir = "dist\WinSetupTool"
    if (Test-Path "$OneDir\WinSetupTool.exe") {
        $items    = Get-ChildItem $OneDir -Recurse -File
        $totalMB  = [math]::Round(($items | Measure-Object Length -Sum).Sum / 1MB, 1)
        $fileCount = $items.Count
        Write-Host "  Folder:   $OneDir\  ($fileCount files, $totalMB MB total)" -ForegroundColor Green
    } else {
        Write-Host "  WARNING: expected $OneDir\WinSetupTool.exe not found" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "Done." -ForegroundColor Cyan
