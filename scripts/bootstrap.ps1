$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$coreDir = Join-Path $projectRoot "core"
$desktopDir = Join-Path $projectRoot "apps\desktop"
$venvPython = Join-Path $coreDir ".venv\Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    python -m venv (Join-Path $coreDir ".venv")
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $venvPython -m pip install -e "${coreDir}[dev]"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

pnpm.cmd --dir $desktopDir install
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "JARVIS development dependencies are ready."
