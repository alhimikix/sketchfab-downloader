$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$buildPython = Join-Path $PSScriptRoot '.build-venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $buildPython)) {
    python -m venv .build-venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create build environment.' }
}
& $buildPython -m pip install -r requirements-build.txt
if ($LASTEXITCODE -ne 0) { throw 'Build dependency installation failed.' }
& $buildPython scripts/build_release.py --target windows-x64
if ($LASTEXITCODE -ne 0) { throw 'EXE build failed.' }
Write-Host "Ready: $PSScriptRoot\dist\SketchfabLocal.exe"
