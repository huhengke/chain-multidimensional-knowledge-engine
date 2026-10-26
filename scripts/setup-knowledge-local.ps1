param([string]$PythonExecutable='python', [string]$HuggingFaceEndpoint='https://huggingface.co', [int]$Port=8079)
$ErrorActionPreference='Stop'
$projectRoot=Split-Path $PSScriptRoot -Parent
$localPython=Join-Path $projectRoot '.runtime/platform-venv/Scripts/python.exe'
Push-Location $projectRoot
try {
    if (-not (Test-Path -LiteralPath $localPython)) {
        & $PythonExecutable -m venv '.runtime/platform-venv'
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the project Python environment.' }
    }
    & $localPython -m pip install -r requirements-knowledge-local.txt
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
    $env:HF_ENDPOINT=$HuggingFaceEndpoint
    $env:HF_HUB_DISABLE_XET='1'
    & $localPython -m platform_server.prepare_local_models --port $Port
    if ($LASTEXITCODE -ne 0) { throw 'Model preparation failed. Existing data has not been reset.' }
} finally { Pop-Location }
