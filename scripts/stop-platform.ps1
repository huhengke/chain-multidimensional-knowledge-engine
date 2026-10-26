param([int]$Port=8079)
$ErrorActionPreference='Stop'
$projectRoot=Split-Path $PSScriptRoot -Parent
$installation=[IO.Path]::GetFullPath((Join-Path $projectRoot '.runtime/standalone'))
function Get-Health($servicePort) {
    # Windows PowerShell decodes application/json without charset as Latin-1,
    # corrupting the Chinese installation path; decode the raw UTF-8 bytes.
    $response=Invoke-WebRequest "http://127.0.0.1:$servicePort/healthz" -TimeoutSec 4 -UseBasicParsing
    [Text.Encoding]::UTF8.GetString($response.RawContentStream.ToArray()) | ConvertFrom-Json
}
foreach ($servicePort in @($Port,18001,18000)) {
    $listeners=Get-NetTCPConnection -LocalPort $servicePort -State Listen -ErrorAction SilentlyContinue
    if (-not $listeners) { continue }
    $health=Get-Health $servicePort
    if ($health.installation -ne $installation) { throw "Port $servicePort is not owned by this installation. No process stopped on that port." }
    foreach ($processId in ($listeners.OwningProcess | Select-Object -Unique)) { Stop-Process -Id $processId -ErrorAction Stop }
    Write-Output "Stopped project service on port $servicePort. Durable data preserved."
}
$weknoraRoot=Join-Path $projectRoot '.runtime/weknora'
$weknoraPort=18090
$weknoraEnv=Join-Path $weknoraRoot 'weknora.env'
if (Test-Path $weknoraEnv) {
    $line=Get-Content $weknoraEnv | Where-Object { $_ -match '^\s*SERVER_PORT\s*=' } | Select-Object -First 1
    if ($line) { $weknoraPort=[int](($line -split '=',2)[1].Trim()) }
}
$weknoraBinary=[IO.Path]::GetFullPath((Join-Path $weknoraRoot 'weknora-lite.exe'))
$weknoraPidFile=Join-Path $installation 'weknora.pid'
$listeners=Get-NetTCPConnection -LocalPort $weknoraPort -State Listen -ErrorAction SilentlyContinue
foreach ($processId in ($listeners.OwningProcess | Select-Object -Unique)) {
    $process=Get-Process -Id $processId -ErrorAction SilentlyContinue
    $processPath=$null
    if ($process) { try { $processPath=[IO.Path]::GetFullPath($process.Path) } catch {} }
    if ($processPath -ne $weknoraBinary) { throw "Port $weknoraPort is not owned by this installation. No process stopped on that port." }
    Stop-Process -Id $processId -ErrorAction Stop
    Write-Output "Stopped project WeKnora sidecar on port $weknoraPort. Durable data preserved."
}
if (Test-Path $weknoraPidFile) { Remove-Item $weknoraPidFile -ErrorAction SilentlyContinue }
