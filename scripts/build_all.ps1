<#
.SYNOPSIS
    Build LlamaGrid host and peer installers.
.DESCRIPTION
    Runs fetch_binaries, PyInstaller for both apps, then NSIS for both installers.
.PARAMETER Version
    Version string to embed (default: 0.1.0)
.PARAMETER SkipFetch
    Skip the binary download step (use when binaries are already present)
.EXAMPLE
    .\scripts\build_all.ps1
    .\scripts\build_all.ps1 -Version 0.2.0 -SkipFetch
#>
param(
    [string]$Version = "0.1.0",
    [switch]$SkipFetch
)

$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

function Step($msg) { Write-Host "`n=== $msg ===" -ForegroundColor Cyan }
function Ok($msg)   { Write-Host "  OK: $msg" -ForegroundColor Green }
function Err($msg)  { Write-Host "  FAIL: $msg" -ForegroundColor Red; exit 1 }

Step "Environment"
python --version; if ($LASTEXITCODE -ne 0) { Err "Python not found" }
pyinstaller --version; if ($LASTEXITCODE -ne 0) { Err "PyInstaller not found — pip install pyinstaller" }
Ok "Python + PyInstaller available"

if (-not $SkipFetch) {
    Step "Fetching binaries"
    python build\fetch_binaries.py
    if ($LASTEXITCODE -ne 0) { Err "fetch_binaries failed" }
    Ok "Binaries ready"
}

Step "Building host executable"
New-Item -ItemType Directory -Force "build\dist\host" | Out-Null
pyinstaller --clean --noconfirm --distpath build\dist\host build\host.spec
if ($LASTEXITCODE -ne 0) { Err "PyInstaller host build failed" }
Ok "build\dist\host\llamagrid-host.exe"

Step "Building peer executable"
New-Item -ItemType Directory -Force "build\dist\peer" | Out-Null
pyinstaller --clean --noconfirm --distpath build\dist\peer build\peer.spec
if ($LASTEXITCODE -ne 0) { Err "PyInstaller peer build failed" }
Ok "build\dist\peer\llamagrid-peer.exe"

$hasMakensis = Get-Command makensis -ErrorAction SilentlyContinue
if ($hasMakensis) {
    Step "Building host installer"
    New-Item -ItemType Directory -Force "build\output" | Out-Null
    makensis /DVERSION=$Version build\host_installer.nsi
    if ($LASTEXITCODE -ne 0) { Err "NSIS host installer failed" }
    Ok "build\output\llamagrid-host-$Version-setup.exe"

    Step "Building peer installer"
    makensis /DVERSION=$Version build\peer_installer.nsi
    if ($LASTEXITCODE -ne 0) { Err "NSIS peer installer failed" }
    Ok "build\output\llamagrid-peer-$Version-setup.exe"
} else {
    Write-Host "`n  NOTE: makensis not found — NSIS installers not built." -ForegroundColor Yellow
    Write-Host "  Install NSIS from https://nsis.sourceforge.io and add to PATH."
    Write-Host "  Executables are in build\dist\{host,peer}\"
}

Step "Build complete"
Write-Host "  Host exe:          build\dist\host\llamagrid-host.exe"
Write-Host "  Peer exe:          build\dist\peer\llamagrid-peer.exe"
if ($hasMakensis) {
    Write-Host "  Host installer:    build\output\llamagrid-host-$Version-setup.exe"
    Write-Host "  Peer installer:    build\output\llamagrid-peer-$Version-setup.exe"
}
