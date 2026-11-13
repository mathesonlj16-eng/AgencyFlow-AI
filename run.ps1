# AgencyFlow AI: start the local demo on Windows.
# Requires Python 3.11+ and an Ollama model running locally.
Set-Location $PSScriptRoot
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    Write-Host 'Creating local Python environment...'
    py -3.11 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 is required. Install it or use README instructions.' }
}
& $python -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
Write-Host 'Open http://127.0.0.1:8766 in your browser.'
& $python -m uvicorn app:app --host 127.0.0.1 --port 8766
