$ErrorActionPreference = "Stop"

$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$installScript = Join-Path $PSScriptRoot "install-render-cli.ps1"
$renderExe = Join-Path $repoRoot ".tools\\render\\render.exe"

if (-not (Test-Path $renderExe)) {
    & $installScript
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

& $renderExe @args
exit $LASTEXITCODE
