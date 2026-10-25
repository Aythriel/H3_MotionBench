param([string]$Root = (Split-Path $PSScriptRoot -Parent))
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'Bootstrap-Runtime.ps1') -Root $Root
$python = Join-Path $Root '.runtime\python\python.exe'
& $python -s (Join-Path $PSScriptRoot 'install.py') --root $Root --only hybrid_node
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. See data/install.log.' }
