$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Source = Join-Path $Root "addon"
$Build = Join-Path $Root "build"
$Out = Join-Path $Build "1782-92-addon-v0.1.0.zip"

New-Item -ItemType Directory -Force -Path $Build | Out-Null

$blender = Get-Command blender -ErrorAction SilentlyContinue
if (-not $blender) {
    throw "Blender is not in PATH. Run Blender's extension build command manually with --source-dir `"$Source`"."
}

& $blender.Source --command extension build --source-dir $Source --output-filepath $Out
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "Built: $Out"
