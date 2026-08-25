$ErrorActionPreference = "Stop"

Set-Location (Split-Path -Parent $PSScriptRoot)

uv python install 3.11.13
uv venv --python 3.11.13
uv sync --all-groups
uv run python --version
uv run eap-migrate --help

