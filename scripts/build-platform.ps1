param([string]$NodeExecutable = "node")
$ErrorActionPreference = "Stop"
$frontendRoot = Join-Path (Split-Path $PSScriptRoot -Parent) "frontend"
Push-Location $frontendRoot
try {
    & $NodeExecutable node_modules/typescript/bin/tsc --noEmit
    if ($LASTEXITCODE -ne 0) { throw "TypeScript validation failed." }
    & $NodeExecutable --max-old-space-size=4096 node_modules/vite/bin/vite.js build
    if ($LASTEXITCODE -ne 0) { throw "Frontend build failed. Use a verified Node version; see docs/production-platform.md." }
} finally { Pop-Location }
