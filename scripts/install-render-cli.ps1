param(
    [string]$Version = "latest",
    [string]$InstallDir = ""
)

$ErrorActionPreference = "Stop"

function Get-RepoRoot {
    return [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
}

function Get-RenderArch {
    $osArch = [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString().ToLowerInvariant()
    switch ($osArch) {
        "x64" { return "amd64" }
        "x86" { return "386" }
        "arm64" { return "arm64" }
        default { throw "Unsupported Windows architecture for Render CLI: $osArch" }
    }
}

function Resolve-LatestVersion {
    $effectiveUrl = & curl.exe -Ls -o NUL -w "%{url_effective}" "https://github.com/render-oss/cli/releases/latest"
    if ($LASTEXITCODE -ne 0 -or -not $effectiveUrl) {
        throw "Unable to resolve the latest Render CLI release."
    }

    if ($effectiveUrl -match "/tag/v(?<version>[0-9][^/]+)$") {
        return $Matches.version
    }

    throw "Unable to parse the latest Render CLI version from $effectiveUrl"
}

if (-not $InstallDir) {
    $InstallDir = Join-Path (Get-RepoRoot) ".tools\\render"
}

$arch = Get-RenderArch
$resolvedVersion = if ($Version -eq "latest") { Resolve-LatestVersion } else { $Version.TrimStart("v") }
$tag = "v$resolvedVersion"
$assetName = "cli_${resolvedVersion}_windows_${arch}.zip"
$binaryName = "cli_v${resolvedVersion}.exe"
$downloadUrl = "https://github.com/render-oss/cli/releases/download/$tag/$assetName"

New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null

$tempRoot = Join-Path $env:TEMP ("render-cli-" + [guid]::NewGuid().ToString("N"))
$zipPath = Join-Path $tempRoot $assetName
$extractDir = Join-Path $tempRoot "extract"
$targetExe = Join-Path $InstallDir "render.exe"

New-Item -ItemType Directory -Path $tempRoot -Force | Out-Null

try {
    Write-Host "Downloading Render CLI $tag for windows/$arch..."
    Invoke-WebRequest -Uri $downloadUrl -OutFile $zipPath

    New-Item -ItemType Directory -Path $extractDir -Force | Out-Null
    Expand-Archive -Path $zipPath -DestinationPath $extractDir -Force

    $downloadedExe = Join-Path $extractDir $binaryName
    if (-not (Test-Path $downloadedExe)) {
        throw "Expected Render CLI binary not found at $downloadedExe"
    }

    Copy-Item -LiteralPath $downloadedExe -Destination $targetExe -Force
    Set-Content -Path (Join-Path $InstallDir "VERSION.txt") -Value $resolvedVersion
    Write-Host "Installed Render CLI to $targetExe"
} finally {
    if (Test-Path $tempRoot) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force
    }
}
