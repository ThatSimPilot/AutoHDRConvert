# Builds dist\AutoHDRConvert.exe: the standalone exe (no installer).
# Run from the repo root, inside the venv:  .\build.ps1   (or double-click / run build.cmd)
$ErrorActionPreference = 'Continue'   # PyInstaller logs to stderr; failures are caught by the exit-code check below

if (-not (Test-Path hdrfix.exe)) { throw 'hdrfix.exe must be in the repository folder.' }

Write-Host '== Building AutoHDRConvert.exe =='
pyinstaller AutoHDRConvert.spec --noconfirm
if ($LASTEXITCODE) { throw 'PyInstaller failed' }

$exe = Join-Path $PSScriptRoot 'dist\AutoHDRConvert.exe'
$mb = [math]::Round((Get-Item $exe).Length / 1MB, 1)
Write-Host "Built $exe ($mb MB)"
