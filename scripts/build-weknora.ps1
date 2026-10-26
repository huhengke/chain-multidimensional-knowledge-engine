param([string]$GoExecutable='go')
$ErrorActionPreference='Stop'
$root=Split-Path $PSScriptRoot -Parent
$managedGo=Join-Path $root '.runtime/toolchains/go/bin/go.exe'
if ($GoExecutable -eq 'go' -and (Test-Path $managedGo)) { $GoExecutable=$managedGo }
$vendor=Join-Path $root 'third_party/weknora'
$manifest=Get-Content (Join-Path $vendor 'source-manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
foreach ($entry in $manifest) {
    $actual=(Get-FileHash (Join-Path $vendor ('derived/'+$entry.path)) -Algorithm SHA256).Hash.ToLower()
    if ($actual -ne $entry.sha256) { throw "Upstream extraction changed without an audited patch: $($entry.path)" }
}
Push-Location (Join-Path $vendor 'derived')
try {
    & $GoExecutable test ./...
    if ($LASTEXITCODE -ne 0) { throw 'WeKnora regression tests failed.' }
    & $GoExecutable build -trimpath -o '../bin/cognition-runtime.exe' ./cmd/cognition-runtime
    if ($LASTEXITCODE -ne 0) { throw 'WeKnora bridge build failed.' }
    $files=Get-ChildItem -LiteralPath (Get-Location) -File -Recurse | Where-Object {$_.Extension -eq '.go' -or $_.Name -eq 'go.mod'} | Sort-Object FullName | ForEach-Object {@{path=[IO.Path]::GetRelativePath((Get-Location).Path,$_.FullName);sha256=(Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLower()}}
    @{upstream_commit='97796c637aa53713edc846bd0a39470d687f0fdf';binary_sha256=(Get-FileHash '../bin/cognition-runtime.exe' -Algorithm SHA256).Hash.ToLower();go_version=(& $GoExecutable version);files=$files} | ConvertTo-Json -Depth 5 | Set-Content '../build-manifest.json' -Encoding utf8
} finally { Pop-Location }
