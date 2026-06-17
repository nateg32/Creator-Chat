$ErrorActionPreference = "Stop"

$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$frontendDir = Join-Path $repoRoot "frontend\\creator-chat"
$frontendProjectFile = Join-Path $frontendDir ".vercel\\project.json"
$rootProjectFile = Join-Path $repoRoot ".vercel\\project.json"
$toolDir = Join-Path $repoRoot ".tools\\vercel"
$vercelCmd = Join-Path $toolDir "node_modules\\.bin\\vercel.cmd"

Push-Location $repoRoot
try {
    if (-not (Test-Path $vercelCmd)) {
        npm install --prefix $toolDir vercel@53.2.0
        if ($LASTEXITCODE -ne 0) {
            exit $LASTEXITCODE
        }
    }

    if (-not (Test-Path $rootProjectFile) -and (Test-Path $frontendProjectFile)) {
        $projectMetadata = Get-Content $frontendProjectFile | ConvertFrom-Json
        & $vercelCmd link --yes --project $projectMetadata.projectId --scope $projectMetadata.orgId
        if ($LASTEXITCODE -ne 0) {
            exit $LASTEXITCODE
        }
    }

    & $vercelCmd @args
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
