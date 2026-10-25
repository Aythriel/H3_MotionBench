param(
    [string]$ComfyHome = '',
    [string]$PythonPath = '',
    [string]$DataPath = '',
    [int]$Port = 8788,
    [int]$ComfyPort = 0,
    [switch]$NoBrowser
)
$ErrorActionPreference = 'Stop'
if (-not $DataPath) { $DataPath = Join-Path $PSScriptRoot 'data' }
$DataPath = [IO.Path]::GetFullPath($DataPath)
New-Item -ItemType Directory -Force -Path $DataPath | Out-Null
$dashboardUrl = "http://127.0.0.1:$Port"
$status = $null
try { $status = Invoke-RestMethod "$dashboardUrl/api/status" -TimeoutSec 2 } catch {}
if ($null -ne $status -and ($status.workbench -ne 'h3-motionbench' -or [IO.Path]::GetFullPath($status.data_dir) -ne $DataPath)) {
    throw "Port $Port is occupied by another service or data folder. Choose an unused -Port."
}
if ($null -eq $status) {
    if (-not $PythonPath) {
        & (Join-Path $PSScriptRoot 'installers\Bootstrap-Runtime.ps1') -Root $PSScriptRoot
        $PythonPath = Join-Path $PSScriptRoot '.runtime\python\python.exe'
    }
    if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) { throw 'The selected Python executable was not found.' }
    $serverPath = Join-Path $PSScriptRoot 'server.py'
    $serverArgs = @('-s', ('"{0}"' -f $serverPath), '--data', ('"{0}"' -f $DataPath), '--port', $Port)
    if ($ComfyHome) { $serverArgs += @('--comfy-home', ('"{0}"' -f [IO.Path]::GetFullPath($ComfyHome))) }
    if ($ComfyPort) { $serverArgs += @('--comfy-port', $ComfyPort) }
    Start-Process -FilePath $PythonPath -ArgumentList $serverArgs -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $DataPath 'dashboard.stdout.log') -RedirectStandardError (Join-Path $DataPath 'dashboard.stderr.log') | Out-Null
}
$initialized = $false
for ($attempt = 0; $attempt -lt 120; $attempt++) {
    try { $status = Invoke-RestMethod "$dashboardUrl/api/status" -TimeoutSec 2 } catch { Start-Sleep -Milliseconds 500; continue }
    if ($status.workbench -ne 'h3-motionbench' -or [IO.Path]::GetFullPath($status.data_dir) -ne $DataPath) { throw "Port $Port is occupied by another service." }
    if ($status.initialized) { $initialized = $true; break }
    Start-Sleep -Milliseconds 500
}
if (-not $initialized) { throw "MotionBench did not finish initialization. Inspect $DataPath\dashboard.stderr.log" }
$page = if ($status.dependencies -and -not $status.dependencies.generation_ready) { "$dashboardUrl/settings.html" } else { $dashboardUrl }
if (-not $NoBrowser) { Start-Process $page }
Write-Output "H3 MotionBench: $page"
