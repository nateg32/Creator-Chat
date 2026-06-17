param(
    [string]$RenderVersion = "latest"
)

$ErrorActionPreference = "Stop"

$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$frontendDir = Join-Path $repoRoot "frontend\\creator-chat"
$vercelToolDir = Join-Path $repoRoot ".tools\\vercel"

Push-Location $frontendDir
try {
    npm install
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
} finally {
    Pop-Location
}

npm install --prefix $vercelToolDir vercel@53.2.0
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

& (Join-Path $PSScriptRoot "install-render-cli.ps1") -Version $RenderVersion
exit $LASTEXITCODE
