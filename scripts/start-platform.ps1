param([string]$PythonExecutable='python', [string]$NodeExecutable='node', [int]$Port=8079)
$ErrorActionPreference='Stop'
$projectRoot=Split-Path $PSScriptRoot -Parent
if (-not (Test-Path (Join-Path $projectRoot 'frontend/dist/index.html'))) { throw 'Run scripts/build-platform.ps1 first.' }
if (-not (Test-Path (Join-Path $projectRoot '.runtime/weknora/weknora-lite.exe'))) { throw 'Build the WeKnora Lite sidecar first (.runtime/weknora/weknora-lite.exe).' }
if (-not (Test-Path (Join-Path $projectRoot 'services/agent/node_modules'))) { throw 'Run npm ci in services/agent first.' }
Push-Location $projectRoot
try {
    & $PythonExecutable -m platform_server.launcher --node $NodeExecutable --port $Port
    if ($LASTEXITCODE -ne 0) { throw 'Startup failed; see .runtime/standalone/*.log.' }
} finally { Pop-Location }
