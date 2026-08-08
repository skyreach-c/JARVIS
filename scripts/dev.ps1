$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$desktopDir = Join-Path $projectRoot "apps\desktop"
$pythonPath = Join-Path $projectRoot "core\.venv\Scripts\python.exe"
$cargoBin = Join-Path $env:USERPROFILE ".cargo\bin"

if (-not (Test-Path $pythonPath)) {
    throw "Python environment not found. Run .\scripts\bootstrap.ps1 first."
}

$env:JARVIS_PYTHON = $pythonPath
$env:PATH = "$cargoBin;$env:PATH"

pnpm.cmd --dir $desktopDir tauri dev
exit $LASTEXITCODE
