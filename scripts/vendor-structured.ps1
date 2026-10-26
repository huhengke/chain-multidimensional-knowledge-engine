param([Parameter(Mandatory=$true)][string]$SourceRoot)
$ErrorActionPreference = 'Stop'
$destinationRoot = Join-Path (Split-Path $PSScriptRoot -Parent) 'services'
$manifest = [System.Collections.Generic.List[object]]::new()
function Copy-CodeTree([string]$Source, [string]$Destination, [string[]]$Extensions) {
    Get-ChildItem -LiteralPath $Source -File -Recurse | Where-Object {
        $_.FullName -notmatch '[\\/](node_modules|__pycache__|\.venv|data|target|\.git)[\\/]' -and $_.Extension -in $Extensions
    } | ForEach-Object {
        $relative = [IO.Path]::GetRelativePath($Source, $_.FullName)
        $target = Join-Path $Destination $relative
        if (Test-Path -LiteralPath $target) { throw "Refusing to overwrite migrated source: $target" }
        New-Item -ItemType Directory -Path (Split-Path $target -Parent) -Force | Out-Null
        Copy-Item -LiteralPath $_.FullName -Destination $target
        $manifest.Add(@{ path = [IO.Path]::GetRelativePath($destinationRoot, $target); source_sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLower() })
    }
}
$engineSource = Join-Path $SourceRoot 'AI数据引擎/chain-neuron-engine'
Copy-CodeTree (Join-Path $engineSource 'engine') (Join-Path $destinationRoot 'structured/engine') @('.py')
Copy-CodeTree (Join-Path $engineSource 'docs/contracts') (Join-Path $destinationRoot 'structured/docs/contracts') @('.json', '.yaml', '.md')
Copy-CodeTree (Join-Path $engineSource 'tests') (Join-Path $destinationRoot 'structured/tests') @('.py')
Copy-CodeTree (Join-Path $SourceRoot 'Agent/src') (Join-Path $destinationRoot 'agent/src') @('.js')
Copy-CodeTree (Join-Path $SourceRoot 'Agent/tests') (Join-Path $destinationRoot 'agent/tests') @('.js')
Copy-CodeTree (Join-Path $SourceRoot '自研AI数据引擎/crates') (Join-Path $destinationRoot 'cnts/crates') @('.rs', '.toml')
Copy-CodeTree (Join-Path $SourceRoot '自研AI数据引擎/python') (Join-Path $destinationRoot 'cnts/python') @('.py', '.pyd')
Copy-CodeTree (Join-Path $SourceRoot '自研AI数据引擎/schemas') (Join-Path $destinationRoot 'cnts/schemas') @('.json', '.yaml')
Copy-CodeTree (Join-Path $SourceRoot '自研AI数据引擎/tests') (Join-Path $destinationRoot 'cnts/tests') @('.py')
foreach ($pair in @(@('Agent/package.json','agent/package.json'), @('Agent/package-lock.json','agent/package-lock.json'), @('自研AI数据引擎/Cargo.toml','cnts/Cargo.toml'), @('自研AI数据引擎/Cargo.lock','cnts/Cargo.lock'), @('自研AI数据引擎/pyproject.toml','cnts/pyproject.toml'))) {
    Copy-Item -LiteralPath (Join-Path $SourceRoot $pair[0]) -Destination (Join-Path $destinationRoot $pair[1])
}
$manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $destinationRoot 'source-migration.json') -Encoding utf8
Write-Output "Migrated $($manifest.Count) source/build files. No databases, source connections, model credentials or active profiles copied."
