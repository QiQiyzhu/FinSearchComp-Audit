param([int]$Port = 8090)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Creating Python environment failed.' }
}
& .venv/Scripts/python.exe -m pip install -r requirements-workbench.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
Write-Host "FinAgent: http://127.0.0.1:$Port"
& .venv/Scripts/python.exe -m uvicorn research_workbench.api:create_app --factory --host 127.0.0.1 --port $Port
