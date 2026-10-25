param([string]$Output = '')
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'installers\Bootstrap-Runtime.ps1') -Root $PSScriptRoot
$arguments = @('-s', (Join-Path $PSScriptRoot 'build_share.py'))
if ($Output) { $arguments += @('--output', $Output) }
& (Join-Path $PSScriptRoot '.runtime\python\python.exe') @arguments
if ($LASTEXITCODE -ne 0) { throw 'Share package creation failed.' }
