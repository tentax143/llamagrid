<#
.SYNOPSIS
    Run the LlamaGrid host in development mode from source.
#>
$Root = Split-Path $PSScriptRoot -Parent
Set-Location $Root

$env:PYTHONPATH = $Root
Write-Host "Starting LlamaGrid host (dev mode)..." -ForegroundColor Cyan
Write-Host "Dashboard: http://localhost:8080" -ForegroundColor Green
python host\main.py
