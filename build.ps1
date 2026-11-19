<#
.SYNOPSIS
    Builds Stravinsky for distribution: the app folder, a portable zip and
    the installer (when Inno Setup is installed).

.DESCRIPTION
    1. runs the unit tests,
    2. freezes the app with PyInstaller (packaging\stravinsky.spec),
    3. zips the app folder  -> Stravinsky-<version>-portable.zip,
    4. compiles the installer -> Stravinsky-Setup-<version>.exe (Inno Setup 6).

    The version comes from version.py. Build files go outside OneDrive by
    default (C:\Builds\Stravinsky) so hundreds of MB aren't synced.

.EXAMPLE
    .\build.ps1
    .\build.ps1 -OutDir D:\Releases -SkipTests
#>
param(
    [string]$OutDir = "C:\Builds\Stravinsky",
    [string]$Python = "",
    [switch]$SkipTests,
    [switch]$SkipInstaller
)

# Native tools (python, ISCC) report on stderr; Windows PowerShell 5.1 would
# turn that into terminating errors. Failures are caught by exit code instead.
$ErrorActionPreference = "Continue"
$Root = $PSScriptRoot

function Step($text) { Write-Host "`n==> $text" -ForegroundColor Cyan }

if (-not $Python) {
    $venv = Join-Path (Split-Path $Root -Parent) ".venv\Scripts\python.exe"
    if (Test-Path $venv) { $Python = $venv } else { $Python = "python" }
}

$versionText = Get-Content (Join-Path $Root "version.py") -Raw
if ($versionText -notmatch '__version__\s*=\s*"([^"]+)"') { throw "No __version__ in version.py" }
$Version = $Matches[1]
Write-Host "Stravinsky $Version  (python: $Python)"

$WorkDir = Join-Path $OutDir "work"
$DistDir = Join-Path $OutDir "dist"
$AppDir = Join-Path $DistDir "Stravinsky"
$ReleaseDir = Join-Path $OutDir "release"
New-Item -ItemType Directory -Force -Path $ReleaseDir -ErrorAction Stop | Out-Null

if (-not $SkipTests) {
    Step "Unit tests"
    Push-Location $Root
    try {
        & $Python -m unittest discover -s tests -t .
        if ($LASTEXITCODE -ne 0) { throw "Tests failed" }
    } finally { Pop-Location }
}

Step "PyInstaller"
& $Python -m PyInstaller --noconfirm --clean --log-level WARN `
    --workpath $WorkDir --distpath $DistDir (Join-Path $Root "packaging\stravinsky.spec")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
if (-not (Test-Path (Join-Path $AppDir "Stravinsky.exe"))) { throw "Stravinsky.exe was not produced" }

Step "Start-up self-test"
# The packaged app opens its splash and main window (all tabs) and closes;
# a build that can't start must never be shipped.
$test = Start-Process (Join-Path $AppDir "Stravinsky.exe") -ArgumentList "--self-test" -PassThru -Wait
$appLog = Join-Path $env:LOCALAPPDATA "Stravinsky\stravinsky.log"
if (Test-Path $appLog) { Get-Content $appLog | Write-Host }
if ($test.ExitCode -ne 0) { throw "The packaged app failed to start (exit code $($test.ExitCode))" }

Step "Portable zip"
$zip = Join-Path $ReleaseDir "Stravinsky-$Version-portable.zip"
if (Test-Path $zip) { Remove-Item $zip -Force -ErrorAction Stop }
# Right after the self-test Windows (or the antivirus scanning the new exe)
# can hold the file for a moment: retry briefly.
for ($try = 1; $try -le 10; $try++) {
    try {
        Compress-Archive -Path $AppDir -DestinationPath $zip -CompressionLevel Optimal -Force -ErrorAction Stop
        break
    } catch {
        if ($try -eq 10) { throw }
        Start-Sleep -Seconds 3
    }
}
Write-Host $zip

if (-not $SkipInstaller) {
    Step "Installer"
    $iscc = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
    ) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
    if (-not $iscc) {
        Write-Warning "Inno Setup 6 not found: installer skipped. Install it with: winget install JRSoftware.InnoSetup"
    } else {
        & $iscc /Q "/DAppVersion=$Version" "/DSourceDir=$AppDir" "/DOutputDir=$ReleaseDir" `
            (Join-Path $Root "packaging\installer.iss")
        if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
        Write-Host (Join-Path $ReleaseDir "Stravinsky-Setup-$Version.exe")
    }
}

Step "Done"
Get-ChildItem $ReleaseDir | Format-Table Name, @{n = "MB"; e = { [math]::Round($_.Length / 1MB, 1) } } -AutoSize
