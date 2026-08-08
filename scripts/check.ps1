$ErrorActionPreference = "Stop"

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)] [scriptblock] $Command,
        [Parameter(Mandatory = $true)] [string] $Label
    )

    Write-Host "==> $Label"
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed with exit code $LASTEXITCODE"
    }
}

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$coreDir = Join-Path $projectRoot "core"
$desktopDir = Join-Path $projectRoot "apps\desktop"
$pythonPath = Join-Path $coreDir ".venv\Scripts\python.exe"
$cargoPath = Join-Path $env:USERPROFILE ".cargo\bin\cargo.exe"

if (-not (Test-Path $pythonPath)) {
    throw "Python environment not found. Run .\scripts\bootstrap.ps1 first."
}

Invoke-Checked { & $pythonPath -m pytest (Join-Path $coreDir "tests") -q -p no:cacheprovider } "Python tests"
Invoke-Checked { & $pythonPath -m ruff check $coreDir } "Python lint"
Invoke-Checked { pnpm.cmd --dir $desktopDir test } "React tests"
Invoke-Checked { pnpm.cmd --dir $desktopDir build } "React build"
Invoke-Checked { & $cargoPath fmt --manifest-path (Join-Path $desktopDir "src-tauri\Cargo.toml") -- --check } "Rust format"
Invoke-Checked { & $cargoPath check --manifest-path (Join-Path $desktopDir "src-tauri\Cargo.toml") } "Rust build check"
Invoke-Checked { & $cargoPath test --manifest-path (Join-Path $desktopDir "src-tauri\Cargo.toml") } "Rust tests"

Write-Host "All JARVIS checks passed."
