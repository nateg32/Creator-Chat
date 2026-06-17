param(
    [switch]$Frontend,
    [switch]$Backend,
    [switch]$IncludeWorker,
    [string]$Commit = "",
    [string]$BackendServiceId = $env:RENDER_BACKEND_SERVICE_ID,
    [string]$WorkerServiceId = $env:RENDER_WORKER_SERVICE_ID
)

$ErrorActionPreference = "Stop"

$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))

if (-not $Frontend -and -not $Backend -and -not $IncludeWorker) {
    $Frontend = $true
    $Backend = $true
    $IncludeWorker = $true
}

if ($Frontend) {
    Write-Host "Deploying frontend to Vercel production..."
    Push-Location $repoRoot
    try {
        & (Join-Path $PSScriptRoot "vercel.ps1") --prod --yes
    } finally {
        Pop-Location
    }
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

$renderDeployFlags = @()
if ($Commit) {
    $renderDeployFlags += @("--commit", $Commit)
}
$renderDeployFlags += @("--wait")

if ($Backend) {
    if (-not $BackendServiceId) {
        throw "BackendServiceId was not provided. Set RENDER_BACKEND_SERVICE_ID or pass -BackendServiceId."
    }
    Write-Host "Deploying backend service on Render..."
    & (Join-Path $PSScriptRoot "render.ps1") @("deploys", "create", $BackendServiceId) @renderDeployFlags
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

if ($IncludeWorker) {
    if (-not $WorkerServiceId) {
        throw "WorkerServiceId was not provided. Set RENDER_WORKER_SERVICE_ID or pass -WorkerServiceId."
    }
    Write-Host "Deploying worker service on Render..."
    & (Join-Path $PSScriptRoot "render.ps1") @("deploys", "create", $WorkerServiceId) @renderDeployFlags
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}
