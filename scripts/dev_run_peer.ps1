<#
.SYNOPSIS
    Run the LlamaGrid peer agent in development mode from source.
.PARAMETER HostIP
    IP address of the LlamaGrid host (default: 172.16.71.183)
.PARAMETER Token
    Auth token from the host dashboard settings tab
.PARAMETER SkipGui
    Skip wizard and run agent directly (requires peer_config.json already configured)
#>
param(
    [string]$HostIP = "172.16.71.183",
    [string]$Token = "",
    [switch]$SkipGui
)

$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

$env:PYTHONPATH = $Root

if ($SkipGui) {
    # Patch config directly and run agent
    $cfgPath = "C:\LlamaGrid\peer_config.json"
    if (Test-Path $cfgPath) {
        $cfg = Get-Content $cfgPath | ConvertFrom-Json
        if ($HostIP) { $cfg.host_ip = $HostIP }
        if ($Token)  { $cfg.auth_token = $Token }
        $cfg.installed = $true
        $cfg | ConvertTo-Json -Depth 5 | Set-Content $cfgPath -Encoding utf8
        Write-Host "Config updated at $cfgPath" -ForegroundColor Green
    }
    Write-Host "Running peer agent (dev mode, no GUI)..." -ForegroundColor Cyan
    python -c "
import sys, os
sys.path.insert(0, '.')
import shared.logging_setup as ls; ls.setup('peer_agent')
from peer.config import load_config
from peer.agent import Agent
Agent(load_config()).run()
"
} else {
    Write-Host "Running peer (dev mode, will show GUI if not configured)..." -ForegroundColor Cyan
    $env:LLAMAGRID_GUI = "1"
    python peer\main.py
}
