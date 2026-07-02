$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"

if (-not (Test-Path $Python)) {
  throw "Virtualenv Python not found: $Python"
}

Get-CimInstance Win32_Process -Filter "name = 'python.exe'" |
  Where-Object { $_.CommandLine -like "*sales_automation.web*" } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

Set-Location $Root
$env:PYTHONPATH = Join-Path $Root "src"

Start-Process `
  -FilePath $Python `
  -ArgumentList "-m sales_automation.web --config config.yaml --host 127.0.0.1 --port 8765" `
  -WorkingDirectory $Root `
  -WindowStyle Hidden

Start-Sleep -Seconds 3
$listener = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
if (-not $listener) {
  throw "salesbot web did not start on http://127.0.0.1:8765"
}

Write-Host "Salesbot running at http://127.0.0.1:8765"
