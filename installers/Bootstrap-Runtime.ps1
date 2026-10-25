param([string]$Root = (Split-Path $PSScriptRoot -Parent))
$ErrorActionPreference = 'Stop'
$Root = [IO.Path]::GetFullPath($Root)
$runtime = Join-Path $Root '.runtime'
$pythonHome = Join-Path $runtime 'python'
$python = Join-Path $pythonHome 'python.exe'
$manifest = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'dependencies.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$lockPath = Join-Path $PSScriptRoot 'dashboard-requirements.lock'
$fingerprint = (Get-FileHash -LiteralPath $lockPath -Algorithm SHA256).Hash
$stamp = Join-Path $runtime 'dashboard.ready'
if ((Test-Path -LiteralPath $python) -and (Test-Path -LiteralPath $stamp) -and ((Get-Content -LiteralPath $stamp -Raw).Trim() -eq $fingerprint)) {
    & $python -s -c 'import aiohttp, PIL, av' 2>$null
    if ($LASTEXITCODE -eq 0) { return }
}
New-Item -ItemType Directory -Force -Path $runtime | Out-Null
$bootstrapLock = $null
try {
    $bootstrapLock = [IO.File]::Open((Join-Path $runtime 'bootstrap.lock'), [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    function Get-VerifiedArtifact($Spec) {
        $target = Join-Path $runtime $Spec.filename
        if ((Test-Path -LiteralPath $target) -and ((Get-Item -LiteralPath $target).Length -eq $Spec.size) -and ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash -eq $Spec.sha256)) { return $target }
        $partial = $target + '.partial'
        Write-Host ('Downloading ' + $Spec.filename)
        Invoke-WebRequest -UseBasicParsing -Uri $Spec.url -OutFile $partial
        if (((Get-Item -LiteralPath $partial).Length -ne $Spec.size) -or ((Get-FileHash -LiteralPath $partial -Algorithm SHA256).Hash -ne $Spec.sha256)) { throw ('Download verification failed: ' + $Spec.filename) }
        Move-Item -LiteralPath $partial -Destination $target -Force
        return $target
    }
    if (-not (Test-Path -LiteralPath $python)) {
        $archive = Get-VerifiedArtifact $manifest.bootstrap.python
        Expand-Archive -LiteralPath $archive -DestinationPath $pythonHome -Force
    }
    $pth = Get-ChildItem -LiteralPath $pythonHome -Filter 'python*._pth' | Select-Object -First 1
    if (-not $pth) { throw 'Embedded Python path configuration is missing.' }
    $lines = [IO.File]::ReadAllLines($pth.FullName)
    if ($lines -notcontains 'Lib/site-packages') { Add-Content -LiteralPath $pth.FullName -Value 'Lib/site-packages' -Encoding ascii }
    if ($lines -notcontains 'import site') { Add-Content -LiteralPath $pth.FullName -Value 'import site' -Encoding ascii }
    $wheel = Get-VerifiedArtifact $manifest.bootstrap.pip
    Write-Host 'Initializing the MotionBench dashboard runtime...'
    & $python -s (Join-Path $PSScriptRoot 'bootstrap_pip.py') $wheel $lockPath
    if ($LASTEXITCODE -ne 0) { throw 'Dashboard dependency installation failed. Run the launcher again to retry.' }
    & $python -s -c 'import aiohttp, PIL, av'
    if ($LASTEXITCODE -ne 0) { throw 'Dashboard runtime validation failed.' }
    Set-Content -LiteralPath $stamp -Value $fingerprint -Encoding ascii
} finally {
    if ($bootstrapLock) { $bootstrapLock.Dispose() }
}
