$ErrorActionPreference = 'Stop'
$vendor = Join-Path (Split-Path $PSScriptRoot -Parent) 'third_party/weknora'
$source = Join-Path $vendor 'upstream'
$derived = Join-Path $vendor 'derived'
$commit = (& git -C $source rev-parse HEAD).Trim()
if ($commit -ne '97796c637aa53713edc846bd0a39470d687f0fdf') { throw 'Unexpected upstream revision' }
$paths = [System.Collections.Generic.List[string]]::new()
Get-ChildItem (Join-Path $source 'internal/infrastructure/chunker') -Filter '*.go' | Where-Object { $_.Name -ne 'parent_child_offsets_test.go' } | ForEach-Object { $paths.Add('internal/infrastructure/chunker/' + $_.Name) }
foreach ($p in @('LICENSE', 'internal/types/retrieval_config.go', 'internal/types/extract_graph.go', 'internal/application/service/knowledgebase_search_fusion.go', 'config/prompt_templates/graph_extraction.yaml', 'config/prompt_templates/generate_summary.yaml')) { $paths.Add($p) }
foreach ($p in @('recall.go','mrr.go','ndcg.go','precision.go','map.go')) { $paths.Add('internal/application/service/metric/' + $p) }
$manifest = @()
foreach ($p in $paths) {
    $target = Join-Path $derived $p
    if (Test-Path $target) { throw "Refusing to overwrite $target" }
    New-Item -ItemType Directory -Path (Split-Path $target -Parent) -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $source $p) -Destination $target
    $manifest += @{ path=$p; sha256=(Get-FileHash $target -Algorithm SHA256).Hash.ToLower(); upstream_commit=$commit }
}
$manifest | ConvertTo-Json -Depth 4 | Set-Content (Join-Path $vendor 'source-manifest.json') -Encoding utf8
$licenseHash = (Get-FileHash (Join-Path $source 'LICENSE') -Algorithm SHA256).Hash.ToLower()
@("upstream_repository=https://github.com/Tencent/WeKnora", 'status=LOCKED', "locked_commit=$commit", "license_sha256=$licenseHash", 'runtime=weknora-embedded-v1') | Set-Content (Join-Path $vendor 'UPSTREAM_COMMIT') -Encoding ascii
Write-Output "Locked $commit; extracted $($manifest.Count) files."
