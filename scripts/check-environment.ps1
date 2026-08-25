$ErrorActionPreference = "Stop"

Set-Location (Split-Path -Parent $PSScriptRoot)

if (-not (Test-Path -LiteralPath ".venv\Scripts\python.exe")) {
    throw "Repository-local .venv is missing. Run scripts\bootstrap.ps1 first."
}

$python = (Resolve-Path -LiteralPath ".venv\Scripts\python.exe").Path
$reported = & $python -c "import sys; print(sys.executable); print(sys.version)"
$reported

if ($reported[0] -ne $python) {
    throw "The active interpreter is not the repository-local .venv interpreter."
}

uv run eap-migrate --help | Out-Host

