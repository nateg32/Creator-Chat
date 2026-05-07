param(
    [string]$Message = "",
    [string]$Remote = "origin",
    [switch]$StayOnMain
)

$ErrorActionPreference = "Stop"

function Run-Git {
    & git @args
    if ($LASTEXITCODE -ne 0) {
        throw "git $($args -join ' ') failed with exit code $LASTEXITCODE"
    }
}

function Get-GitOutput {
    $output = & git @args
    if ($LASTEXITCODE -ne 0) {
        throw "git $($args -join ' ') failed with exit code $LASTEXITCODE"
    }
    return $output
}

$repoRoot = (Get-GitOutput rev-parse --show-toplevel | Select-Object -First 1).Trim()
Set-Location $repoRoot

$changes = @(Get-GitOutput status --porcelain)
if ($changes.Count -eq 0) {
    Write-Host "No changes to commit."
    exit 0
}

$branch = (Get-GitOutput branch --show-current | Select-Object -First 1).Trim()
if (-not $branch) {
    throw "You are in a detached HEAD state. Switch to a branch first."
}

if (($branch -eq "main" -or $branch -eq "master") -and -not $StayOnMain) {
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $branch = "codex/auto-push-$stamp"
    Run-Git switch -c $branch
}

if (-not $Message.Trim()) {
    $changedFiles = @(Get-GitOutput diff --name-only)
    $untrackedFiles = @(Get-GitOutput ls-files --others --exclude-standard)
    $allFiles = @($changedFiles + $untrackedFiles | Where-Object { $_ } | Select-Object -Unique)
    if ($allFiles.Count -eq 1) {
        $Message = "Update $($allFiles[0])"
    } else {
        $Message = "Update $($allFiles.Count) files"
    }
}

Run-Git add -A

$staged = @(Get-GitOutput diff --cached --name-only)
if ($staged.Count -eq 0) {
    Write-Host "No staged changes to commit."
    exit 0
}

Run-Git commit -m $Message
Run-Git push -u $Remote $branch

$remoteUrl = (Get-GitOutput remote get-url $Remote | Select-Object -First 1).Trim()
if ($remoteUrl -match "github\.com[:/](?<owner>[^/]+)/(?<repo>[^/.]+)(?:\.git)?$") {
    $owner = $Matches.owner
    $repo = $Matches.repo
    Write-Host "Pushed $branch."
    Write-Host "PR: https://github.com/$owner/$repo/pull/new/$branch"
} else {
    Write-Host "Pushed $branch."
}
