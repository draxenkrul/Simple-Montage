# Builds dist\SimpleMontage\SimpleMontage.exe and dist\SimpleMontage-windows-x64.zip
#
# Usage (from the project folder, in PowerShell):
#   powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
param(
    [string]$Python = "",          # Python used to create .venv (default: py launcher or python)
    [switch]$SkipTests
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# Run a program and stop the build if it fails. Uses plain $args so flags such
# as "-m" are passed through to the program untouched.
function Run {
    $exe, $rest = $args
    & $exe @rest
    if ($LASTEXITCODE) { throw "Command failed ($LASTEXITCODE): $($args -join ' ')" }
}

Write-Host "== 1/5 Python environment"
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    if (-not $Python) { $Python = if (Get-Command py -ErrorAction SilentlyContinue) { "py" } else { "python" } }
    Run $Python -m venv .venv
}
$py = Join-Path $root ".venv\Scripts\python.exe"
Run $py -m pip install --quiet --upgrade pip
Run $py -m pip install --quiet -e ".[dev]" "pyinstaller>=6.11"

Write-Host "== 2/5 FFmpeg and libmpv"
& (Join-Path $PSScriptRoot "fetch_binaries.ps1")

if (-not $SkipTests) {
    Write-Host "== 3/5 Tests"
    Run $py -m pytest -q
} else { Write-Host "== 3/5 Tests skipped" }

Write-Host "== 4/5 PyInstaller"
Run $py -m PyInstaller --noconfirm --clean packaging\SimpleMontage.spec
$app = Join-Path $root "dist\SimpleMontage"
New-Item -ItemType Directory -Force -Path "$app\bin" | Out-Null
Copy-Item bin\ffmpeg.exe, bin\ffprobe.exe, bin\libmpv-2.dll -Destination "$app\bin" -Force

Write-Host "== 5/5 Checking the built app"
$report = Join-Path $root "dist\smoke-test.txt"
$proc = Start-Process -FilePath "$app\SimpleMontage.exe" -ArgumentList "--smoke-test", "`"$report`"" -Wait -PassThru
Get-Content $report -ErrorAction SilentlyContinue
if ($proc.ExitCode -ne 0) { throw "The built app failed its self-check (exit code $($proc.ExitCode))." }

$zip = Join-Path $root "dist\SimpleMontage-windows-x64.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path $app -DestinationPath $zip
Write-Host ""
Write-Host "Done:"
Write-Host "  App: $app\SimpleMontage.exe"
Write-Host "  Zip: $zip"
