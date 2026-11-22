<#
.SYNOPSIS
    Publishes a new version: sets version.py, commits, tags and pushes, so the
    tag and version.py always match (GitHub then builds and publishes the
    installer and the zip -- see .github/workflows/release.yml).

.EXAMPLE
    .\release.ps1 1.0.3
    release.bat 1.0.3
#>
param(
    [Parameter(Mandatory = $true)][string]$Version,
    [string]$Python = ""
)

$ErrorActionPreference = "Continue"   # git/python report on stderr; exit codes are checked
$Root = $PSScriptRoot
Set-Location $Root

function Fail($message) { Write-Host "`n$message" -ForegroundColor Red; exit 1 }
function Invoke-Git { & git.exe @args; if ($LASTEXITCODE -ne 0) { Fail "git $($args -join ' ') failed." } }

$Version = $Version.TrimStart("v")
if ($Version -notmatch '^\d+\.\d+\.\d+$') { Fail "Use a version like 1.0.3 (got '$Version')." }
$tag = "v$Version"

if ((& git branch --show-current) -ne "main") { Fail "Switch to the main branch first." }
if (& git status --porcelain) { Fail "There are uncommitted changes: commit them first, then run this again." }

Write-Host "Checking GitHub for newer work..."
Invoke-Git fetch --quiet origin main --tags
if (& git log --oneline "HEAD..origin/main") {
    Fail "GitHub has commits this computer doesn't (from another computer?). Run: git pull --ff-only"
}
if (& git tag --list $tag) { Fail "Version $Version was already used (tag $tag exists). Pick a higher number." }

$versionFile = Join-Path $Root "version.py"
$text = [IO.File]::ReadAllText($versionFile)
if ($text -notmatch '__version__\s*=\s*"([^"]+)"') { Fail "No __version__ in version.py" }
$current = $Matches[1]
if ([version]$Version -le [version]$current) { Fail "$Version is not higher than the current version $current." }

if (-not $Python) {
    $venv = Join-Path (Split-Path $Root -Parent) ".venv\Scripts\python.exe"
    if (Test-Path $venv) { $Python = $venv } else { $Python = "python" }
}
Write-Host "Running the tests..."
& $Python -m unittest discover -s tests -t . 2>&1 | Select-Object -Last 3 | ForEach-Object { "  $_" }
if ($LASTEXITCODE -ne 0) { Fail "Tests failed: nothing was changed." }

# Write without a byte-order mark (Windows PowerShell's Set-Content would add one).
$text = $text -replace '__version__\s*=\s*"[^"]+"', "__version__ = `"$Version`""
[IO.File]::WriteAllText($versionFile, $text, (New-Object Text.UTF8Encoding $false))

Invoke-Git add version.py
Invoke-Git commit --quiet -m "Release $Version"
Invoke-Git tag -a $tag -m "Stravinsky $Version"
Invoke-Git push --quiet origin main
Invoke-Git push --quiet origin $tag

Write-Host "`nVersion $Version sent to GitHub." -ForegroundColor Green
Write-Host "Build progress: https://github.com/jvpd90/Stravinsky_QDA/actions"
Write-Host "When it finishes (~10-15 min): https://github.com/jvpd90/Stravinsky_QDA/releases/tag/$tag"
