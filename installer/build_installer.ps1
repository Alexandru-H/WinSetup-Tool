# Build the WinSetup Tool installer.
#
# Usage from project root:
#   .\installer\build_installer.ps1
#   .\installer\build_installer.ps1 -SkipPyInstaller   # skip rebuild of dist\WinSetupTool

param(
    [switch]$SkipPyInstaller
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

Write-Host "Project root: $ProjectRoot" -ForegroundColor Cyan

# 1. Make sure the onedir build exists
$OneDir = "dist\WinSetupTool"
$OneDirExe = Join-Path $OneDir "WinSetupTool.exe"

if (-not $SkipPyInstaller) {
    Write-Host ""
    Write-Host "=== Building onedir bundle ===" -ForegroundColor Green

    $PyInstaller = Join-Path $ProjectRoot ".venv\Scripts\pyinstaller.exe"
    if (-not (Test-Path $PyInstaller)) {
        Write-Error "PyInstaller not found at $PyInstaller"
        exit 1
    }

    & $PyInstaller "WinSetupTool-onedir.spec" --noconfirm --clean
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Onedir build failed."
        exit 1
    }
}

if (-not (Test-Path $OneDirExe)) {
    Write-Error "Onedir build not found at $OneDirExe. Run without -SkipPyInstaller to build it."
    exit 1
}

# 2. Locate Inno Setup compiler
$Iscc = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if (-not (Test-Path $Iscc)) {
    Write-Error @"
Inno Setup not found at: $Iscc
Install it from: https://jrsoftware.org/isdl.php
"@
    exit 1
}

# 3. Verify required files
$Required = @(
    "installer\setup.iss",
    "Furina_icon.ico",
    "LICENSE.txt"
)
foreach ($f in $Required) {
    if (-not (Test-Path $f)) {
        Write-Error "Missing required file: $f"
        exit 1
    }
}

# 4. Run Inno Setup compiler
Write-Host ""
Write-Host "=== Running Inno Setup compiler ===" -ForegroundColor Green
& $Iscc "installer\setup.iss"
if ($LASTEXITCODE -ne 0) {
    Write-Error "Inno Setup compilation failed."
    exit 1
}

# 5. Locate the output and report
$Output = "dist\WinSetupTool-1.0.0-Setup.exe"
if (Test-Path $Output) {
    $sizeMB = [math]::Round((Get-Item $Output).Length / 1MB, 1)
    Write-Host ""
    Write-Host "Installer built: $Output ($sizeMB MB)" -ForegroundColor Cyan
} else {
    Write-Warning "Compilation reported success but installer not found at $Output"
}

Write-Host ""
Write-Host "Done." -ForegroundColor Cyan
