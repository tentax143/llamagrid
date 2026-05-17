<#
.SYNOPSIS
    Populate peer\bundled\ with every file peer.spec references in its datas section.

.DESCRIPTION
    peer.spec datas entries:
        (peer/bundled/rpc-server,  'rpc-server')   <-- directory: rpc-server.exe + *.dll
        (peer/bundled/nssm.exe,    '.')             <-- single file

    Strategy for rpc-server.exe + DLLs:
        1. Copy from C:\llama\ if rpc-server.exe is present  (existing install)
        2. Otherwise download llama-b4231-bin-win-cuda-cu12.2.0-x64.zip from GitHub

    Strategy for nssm.exe:
        1. Copy from C:\LlamaGrid\nssm.exe if present
        2. Locate via Get-Command (Chocolatey, PATH)
        3. Copy from C:\ProgramData\chocolatey\bin\nssm.exe
        4. Download nssm-2.24.zip from nssm.cc

.PARAMETER Force
    Re-download and overwrite even if files already exist.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\prepare_bundled.ps1
    powershell -ExecutionPolicy Bypass -File scripts\prepare_bundled.ps1 -Force
#>
param(
    [switch]$Force
)

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Root       = Split-Path $PSScriptRoot -Parent
$BundledDir = Join-Path $Root "peer\bundled"
$RpcDir     = Join-Path $BundledDir "rpc-server"
$NssmDest   = Join-Path $BundledDir "nssm.exe"

$LlamaTag    = "b4231"
$LlamaZipUrl = "https://github.com/ggerganov/llama.cpp/releases/download/$LlamaTag/llama-$LlamaTag-bin-win-cuda-cu12.2.0-x64.zip"
$NssmZipUrl  = "https://nssm.cc/release/nssm-2.24.zip"
$LlamaLocal  = "C:\llama"

$MB = 1048576   # 1 MB in bytes  -- avoids 1MB suffix inside expressions
$KB = 1024      # 1 KB in bytes

function Write-Step { param($msg) Write-Host ("`n[{0}]" -f $msg) -ForegroundColor Cyan }
function Write-Ok   { param($msg) Write-Host ("  OK  {0}" -f $msg) -ForegroundColor Green }
function Write-Skip { param($msg) Write-Host ("  --  {0}" -f $msg) -ForegroundColor DarkGray }
function Write-Warn { param($msg) Write-Host ("  !!  {0}" -f $msg) -ForegroundColor Yellow }
function Write-Fail { param($msg) Write-Host ("  XX  {0}" -f $msg) -ForegroundColor Red; exit 1 }

function Get-FileSizeMB {
    param($path)
    return [math]::Round((Get-Item $path).Length / $MB, 1)
}

function Get-FileSizeKB {
    param($path)
    return [math]::Round((Get-Item $path).Length / $KB, 0)
}

function Download-File {
    param($url, $dest)
    $leaf = Split-Path $url -Leaf
    Write-Host ("  Downloading {0}..." -f $leaf) -NoNewline
    try {
        Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing
        $sz = Get-FileSizeMB $dest
        Write-Host (" done ({0} MB)" -f $sz)
    }
    catch {
        Write-Host ""
        Write-Fail ("Download failed: {0}" -f $_)
    }
}

function Expand-ZipTo {
    param($zipPath, $destDir)
    Expand-Archive -Path $zipPath -DestinationPath $destDir -Force
}

# ---------------------------------------------------------------------------
# 0. Directories
# ---------------------------------------------------------------------------
Write-Step "Creating peer\bundled directories"

foreach ($dir in @($BundledDir, $RpcDir)) {
    if (Test-Path -LiteralPath $dir) {
        $item = Get-Item -LiteralPath $dir
        if ($item -is [System.IO.FileInfo]) {
            # A stale file is blocking where the directory needs to go — remove it
            Write-Host ("  Removing stale file at {0}" -f $dir)
            Remove-Item -LiteralPath $dir -Force
            New-Item -ItemType Directory -Path $dir | Out-Null
        }
        # else: already a directory, nothing to do
    } else {
        New-Item -ItemType Directory -Path $dir | Out-Null
    }
}

if (-not (Test-Path -LiteralPath $RpcDir -PathType Container)) {
    Write-Fail ("Could not create directory: {0}" -f $RpcDir)
}
Write-Ok ("peer\bundled\rpc-server\ ready")

# ---------------------------------------------------------------------------
# 1. rpc-server.exe + DLLs
# ---------------------------------------------------------------------------
Write-Step "rpc-server.exe"

$RpcExeDest = Join-Path $RpcDir "rpc-server.exe"
$needRpc    = $Force -or (-not (Test-Path $RpcExeDest))

if (-not $needRpc) {
    Write-Skip "rpc-server.exe already present (use -Force to refresh)"
}
else {
    $localRpc = Join-Path $LlamaLocal "rpc-server.exe"

    if (Test-Path $localRpc) {
        # Strategy 1: copy from C:\llama
        Write-Host ("  Copying from {0}..." -f $LlamaLocal)

        Copy-Item $localRpc $RpcExeDest -Force
        $sz = Get-FileSizeMB $RpcExeDest
        Write-Ok ("rpc-server.exe  ({0} MB)" -f $sz)

        $dlls = @(Get-ChildItem -Path $LlamaLocal -Filter "*.dll" -File)
        if ($dlls.Count -eq 0) {
            Write-Warn ("No DLLs found in {0} -- rpc-server may crash at runtime" -f $LlamaLocal)
        }
        else {
            foreach ($dll in $dlls) {
                Copy-Item $dll.FullName (Join-Path $RpcDir $dll.Name) -Force
            }
            Write-Ok ("{0} DLL(s) copied from {1}" -f $dlls.Count, $LlamaLocal)
        }
    }
    else {
        # Strategy 2: download from GitHub
        Write-Warn ("{0}\rpc-server.exe not found -- downloading from GitHub" -f $LlamaLocal)
        Write-Host ("  URL: {0}" -f $LlamaZipUrl)

        $tmpZip = Join-Path $env:TEMP ("llama-{0}.zip" -f $LlamaTag)
        $tmpDir = Join-Path $env:TEMP ("llama-{0}-extracted" -f $LlamaTag)

        Download-File $LlamaZipUrl $tmpZip

        Write-Host "  Extracting..."
        if (Test-Path $tmpDir) { Remove-Item $tmpDir -Recurse -Force }
        Expand-ZipTo $tmpZip $tmpDir

        $found = Get-ChildItem -Path $tmpDir -Filter "rpc-server.exe" -Recurse -File |
                 Select-Object -First 1
        if (-not $found) {
            Write-Fail "rpc-server.exe not found in downloaded archive -- check LlamaTag in versioning.py"
        }

        $srcDir = $found.DirectoryName
        Copy-Item $found.FullName $RpcExeDest -Force
        $sz = Get-FileSizeMB $RpcExeDest
        Write-Ok ("rpc-server.exe  ({0} MB)" -f $sz)

        $dlls = @(Get-ChildItem -Path $srcDir -Filter "*.dll" -File)
        foreach ($dll in $dlls) {
            Copy-Item $dll.FullName (Join-Path $RpcDir $dll.Name) -Force
        }
        Write-Ok ("{0} DLL(s) extracted" -f $dlls.Count)

        Remove-Item $tmpZip -Force -ErrorAction SilentlyContinue
        Remove-Item $tmpDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}

# ---------------------------------------------------------------------------
# 2. nssm.exe
# ---------------------------------------------------------------------------
Write-Step "nssm.exe"

$needNssm = $Force -or (-not (Test-Path $NssmDest))

if (-not $needNssm) {
    Write-Skip "nssm.exe already present (use -Force to refresh)"
}
else {
    $copied = $false

    # Strategy 1: C:\LlamaGrid\nssm.exe
    $c1 = "C:\LlamaGrid\nssm.exe"
    if ((-not $copied) -and (Test-Path $c1)) {
        Copy-Item $c1 $NssmDest -Force
        Write-Ok ("nssm.exe copied from {0}" -f $c1)
        $copied = $true
    }

    # Strategy 2: nssm on PATH
    if (-not $copied) {
        $nssmCmd = Get-Command nssm -ErrorAction SilentlyContinue
        if ($nssmCmd) {
            Copy-Item $nssmCmd.Source $NssmDest -Force
            Write-Ok ("nssm.exe copied from PATH ({0})" -f $nssmCmd.Source)
            $copied = $true
        }
    }

    # Strategy 3: Chocolatey default path
    if (-not $copied) {
        $c3 = "C:\ProgramData\chocolatey\bin\nssm.exe"
        if (Test-Path $c3) {
            Copy-Item $c3 $NssmDest -Force
            Write-Ok ("nssm.exe copied from Chocolatey ({0})" -f $c3)
            $copied = $true
        }
    }

    # Strategy 4: choco install nssm
    if (-not $copied) {
        $choco = Get-Command choco -ErrorAction SilentlyContinue
        if ($choco) {
            Write-Host "  Installing nssm via Chocolatey..."
            choco install nssm -y --no-progress 2>&1 | Out-Null
            $nssmCmd = Get-Command nssm -ErrorAction SilentlyContinue
            if ($nssmCmd) {
                Copy-Item $nssmCmd.Source $NssmDest -Force
                Write-Ok ("nssm.exe installed via choco ({0})" -f $nssmCmd.Source)
                $copied = $true
            }
        }
    }

    # Strategy 5: winget install nssm
    if (-not $copied) {
        $winget = Get-Command winget -ErrorAction SilentlyContinue
        if ($winget) {
            Write-Host "  Installing nssm via winget..."
            winget install --id NSSM.NSSM --silent --accept-package-agreements --accept-source-agreements 2>&1 | Out-Null
            # winget installs to Program Files — search for it
            $found = Get-ChildItem -Path "$env:ProgramFiles" -Filter "nssm.exe" -Recurse -File -ErrorAction SilentlyContinue |
                     Select-Object -First 1
            if (-not $found) {
                $found = Get-ChildItem -Path "${env:ProgramFiles(x86)}" -Filter "nssm.exe" -Recurse -File -ErrorAction SilentlyContinue |
                         Select-Object -First 1
            }
            if ($found) {
                Copy-Item $found.FullName $NssmDest -Force
                Write-Ok ("nssm.exe installed via winget ({0})" -f $found.FullName)
                $copied = $true
            }
        }
    }

    # Strategy 6: download zip — try nssm.cc then Wayback Machine fallback
    if (-not $copied) {
        $tmpZip = Join-Path $env:TEMP "nssm-2.24.zip"
        $tmpDir = Join-Path $env:TEMP "nssm-2.24-extracted"

        $nssmUrls = @(
            $NssmZipUrl,
            "https://web.archive.org/web/20240101000000*/https://nssm.cc/release/nssm-2.24.zip",
            "https://web.archive.org/web/2024/https://nssm.cc/release/nssm-2.24.zip"
        )

        $downloaded = $false
        foreach ($url in $nssmUrls) {
            Write-Host ("  Trying: {0}" -f $url)
            try {
                Invoke-WebRequest -Uri $url -OutFile $tmpZip -UseBasicParsing -TimeoutSec 30
                if ((Get-Item $tmpZip).Length -gt 100000) {
                    $downloaded = $true
                    break
                }
            } catch {
                Write-Host ("  Failed ({0}), trying next..." -f $_.Exception.Message.Split("`n")[0])
            }
        }

        if (-not $downloaded) {
            Write-Host ""
            Write-Host "  Could not download nssm automatically." -ForegroundColor Yellow
            Write-Host "  Manual options (run ONE of these, then re-run this script):" -ForegroundColor Yellow
            Write-Host ""
            Write-Host "    Option A -- Chocolatey:" -ForegroundColor Cyan
            Write-Host "      choco install nssm -y"
            Write-Host ""
            Write-Host "    Option B -- winget:" -ForegroundColor Cyan
            Write-Host "      winget install --id NSSM.NSSM"
            Write-Host ""
            Write-Host "    Option C -- manual download:" -ForegroundColor Cyan
            Write-Host "      Download nssm-2.24.zip from https://nssm.cc/download"
            Write-Host "      Extract win64\nssm.exe and place it at:"
            Write-Host ("      {0}" -f $NssmDest)
            Write-Host ""
            Write-Fail "nssm.exe could not be obtained automatically"
        }

        Write-Host "  Extracting..."
        if (Test-Path $tmpDir) { Remove-Item $tmpDir -Recurse -Force }
        Expand-ZipTo $tmpZip $tmpDir

        $found = Get-ChildItem -Path $tmpDir -Filter "nssm.exe" -Recurse -File |
                 Where-Object { $_.DirectoryName -match "win64" } |
                 Select-Object -First 1

        if (-not $found) {
            $found = Get-ChildItem -Path $tmpDir -Filter "nssm.exe" -Recurse -File |
                     Select-Object -First 1
        }

        if (-not $found) {
            Write-Fail "nssm.exe not found in downloaded archive"
        }

        Copy-Item $found.FullName $NssmDest -Force
        $sz = Get-FileSizeKB $NssmDest
        Write-Ok ("nssm.exe  ({0} KB)" -f $sz)

        Remove-Item $tmpZip -Force -ErrorAction SilentlyContinue
        Remove-Item $tmpDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}

# ---------------------------------------------------------------------------
# 3. Verification
# ---------------------------------------------------------------------------
Write-Step "Verification"

$missing = 0

if (Test-Path $RpcExeDest) {
    $sz = Get-FileSizeMB $RpcExeDest
    Write-Ok ("peer\bundled\rpc-server\rpc-server.exe  ({0} MB)" -f $sz)
}
else {
    Write-Warn "MISSING: peer\bundled\rpc-server\rpc-server.exe"
    $missing++
}

$dllCount = @(Get-ChildItem -Path $RpcDir -Filter "*.dll" -File).Count
if ($dllCount -gt 0) {
    Write-Ok ("{0} DLL(s) in peer\bundled\rpc-server\" -f $dllCount)
}
else {
    Write-Warn "peer\bundled\rpc-server\ has no DLLs -- CUDA rpc-server will likely crash at runtime"
}

if (Test-Path $NssmDest) {
    $sz = Get-FileSizeKB $NssmDest
    Write-Ok ("peer\bundled\nssm.exe  ({0} KB)" -f $sz)
}
else {
    Write-Warn "MISSING: peer\bundled\nssm.exe"
    $missing++
}

Write-Host ""
if ($missing -eq 0) {
    Write-Host "All required files present. peer.spec build should succeed." -ForegroundColor Green
    Write-Host ""
    Write-Host "Next step:" -ForegroundColor Cyan
    Write-Host "  pyinstaller --clean --noconfirm --distpath build\dist\peer build\peer.spec"
}
else {
    Write-Host ("{0} required file(s) missing -- fix errors above before running PyInstaller." -f $missing) -ForegroundColor Red
    exit 1
}
