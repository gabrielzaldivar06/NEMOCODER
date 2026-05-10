param(
    [string]$Version = "",
    [string]$OutputRoot = ".release"
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = (Resolve-Path (Join-Path $ScriptDir "..")).Path
Set-Location $RepoRoot

function Get-PythonExe {
    $venvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    if (Test-Path $venvPython) {
        return $venvPython
    }
    return "python"
}

function Read-ProjectVersion {
    $pyproject = Join-Path $RepoRoot "pyproject.toml"
    if (-not (Test-Path $pyproject)) {
        return "0.0.0"
    }
    $text = Get-Content $pyproject -Raw -Encoding UTF8
    $m = [regex]::Match($text, '(?m)^version\s*=\s*"([^"]+)"')
    if ($m.Success) {
        return $m.Groups[1].Value
    }
    return "0.0.0"
}

function Assert-RequiredArtifact {
    param(
        [string]$Path,
        [string]$Description
    )

    if (-not (Test-Path $Path)) {
        throw "Missing required artifact: $Description ($Path)"
    }
}

function Get-RelativePath {
    param(
        [string]$BasePath,
        [string]$FullPath
    )

    return [System.IO.Path]::GetRelativePath($BasePath, $FullPath).Replace("\", "/")
}

$projectVersion = Read-ProjectVersion
if (-not $Version) {
    $Version = "$projectVersion-$(Get-Date -Format yyyyMMddHHmmss)"
}

$releaseRoot = Join-Path $RepoRoot $OutputRoot
$bundleRoot = Join-Path $releaseRoot "nemocode-$Version"
$archivePath = Join-Path $releaseRoot "nemocode-$Version.zip"

if (Test-Path $bundleRoot) {
    Remove-Item $bundleRoot -Recurse -Force
}
if (Test-Path $archivePath) {
    Remove-Item $archivePath -Force
}

New-Item -ItemType Directory -Path $bundleRoot | Out-Null
New-Item -ItemType Directory -Path (Join-Path $bundleRoot "backend") | Out-Null
New-Item -ItemType Directory -Path (Join-Path $bundleRoot "mission-control-web") | Out-Null
New-Item -ItemType Directory -Path (Join-Path $bundleRoot "desktop-shell-web") | Out-Null
New-Item -ItemType Directory -Path (Join-Path $bundleRoot "scripts") | Out-Null

$pythonExe = Get-PythonExe

Write-Host "[1/6] Building backend wheel/sdist"
& $pythonExe -m pip install --quiet build
& $pythonExe -m build --sdist --wheel
Copy-Item -Path (Join-Path $RepoRoot "dist\*") -Destination (Join-Path $bundleRoot "backend") -Force
Assert-RequiredArtifact -Path (Join-Path $bundleRoot "backend\*.whl") -Description "Backend wheel"
Assert-RequiredArtifact -Path (Join-Path $bundleRoot "backend\*.tar.gz") -Description "Backend sdist"

Write-Host "[2/6] Building mission-control frontend"
$npmPrefix = "apps/mission-control"
$lockFile = Join-Path $RepoRoot "$npmPrefix\package-lock.json"
if (Test-Path $lockFile) {
    npm --prefix $npmPrefix ci
} else {
    npm --prefix $npmPrefix install
}
npm --prefix $npmPrefix run build
Copy-Item -Path (Join-Path $RepoRoot "$npmPrefix\dist\*") -Destination (Join-Path $bundleRoot "mission-control-web") -Recurse -Force
Assert-RequiredArtifact -Path (Join-Path $bundleRoot "mission-control-web\index.html") -Description "Mission Control build output"

Write-Host "[3/6] Building desktop shell web evidence"
$desktopPrefix = "apps/nemo-desktop"
$desktopLockFile = Join-Path $RepoRoot "$desktopPrefix\package-lock.json"
if (Test-Path $desktopLockFile) {
    npm --prefix $desktopPrefix ci
} else {
    npm --prefix $desktopPrefix install
}
npm --prefix $desktopPrefix run build
Copy-Item -Path (Join-Path $RepoRoot "$desktopPrefix\dist\*") -Destination (Join-Path $bundleRoot "desktop-shell-web") -Recurse -Force
Assert-RequiredArtifact -Path (Join-Path $bundleRoot "desktop-shell-web\index.html") -Description "Desktop shell build output"

Write-Host "[4/6] Writing launcher scripts"
$startBackend = @'
param(
    [string]$RepoPath = ".",
    [string]$RuntimesPath = ".nemo-runtimes",
    [int]$Port = 8787
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location ..
$env:PYTHONPATH = "src"
$pythonExe = Join-Path (Get-Location) ".venv\Scripts\python.exe"
if (-not (Test-Path $pythonExe)) {
    $pythonExe = "python"
}
& $pythonExe -m nemo_coding_platform mission-control-server --repo $RepoPath --runtimes $RuntimesPath --port $Port
'@

$startWebPreview = @'
param(
    [int]$Port = 5173
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location ..
npm --prefix apps/mission-control run preview -- --port $Port
'@

$startBackend | Set-Content -Path (Join-Path $bundleRoot "scripts\start-backend.ps1") -Encoding UTF8
$startWebPreview | Set-Content -Path (Join-Path $bundleRoot "scripts\start-web-preview.ps1") -Encoding UTF8

Write-Host "[5/6] Writing manifest, integrity, and checklist"
$manifest = [ordered]@{
    package = "nemocode"
    version = $projectVersion
    bundle_version = $Version
    generated_at_utc = (Get-Date).ToUniversalTime().ToString("o")
    release_mode = "rc_baseline"
    artifacts = @(
        "backend/*.whl",
        "backend/*.tar.gz",
        "mission-control-web/*",
        "desktop-shell-web/*",
        "scripts/start-backend.ps1",
        "scripts/start-web-preview.ps1"
    )
    prd_alignment = "Global PRD Finalization RC baseline: backend + mission-control + desktop shell evidence. Installer target follows in next slice."
}
$manifest | ConvertTo-Json -Depth 5 | Set-Content -Path (Join-Path $bundleRoot "manifest.json") -Encoding UTF8

$fileHashes = @()
Get-ChildItem -Path $bundleRoot -File -Recurse | ForEach-Object {
    $hash = Get-FileHash -Path $_.FullName -Algorithm SHA256
    $fileHashes += [ordered]@{
        path = Get-RelativePath -BasePath $bundleRoot -FullPath $_.FullName
        sha256 = $hash.Hash.ToLowerInvariant()
        size_bytes = $_.Length
    }
}

$integrity = [ordered]@{
    bundle_version = $Version
    generated_at_utc = (Get-Date).ToUniversalTime().ToString("o")
    file_count = $fileHashes.Count
    files = $fileHashes
}
$integrity | ConvertTo-Json -Depth 6 | Set-Content -Path (Join-Path $bundleRoot "integrity.json") -Encoding UTF8

$releaseChecklist = @(
    "# Release Checklist (RC Baseline)",
    "",
    "- [x] Backend wheel artifact exists",
    "- [x] Backend sdist artifact exists",
    "- [x] Mission Control build artifact exists",
    "- [x] Desktop shell build artifact exists",
    "- [x] Launcher scripts generated",
    "- [x] Manifest generated",
    "- [x] Integrity report generated",
    "",
    "Bundle version: $Version",
    "Generated UTC: $((Get-Date).ToUniversalTime().ToString('o'))"
)
$releaseChecklist | Set-Content -Path (Join-Path $bundleRoot "release-checklist.md") -Encoding UTF8

Copy-Item -Path (Join-Path $RepoRoot "README.md") -Destination (Join-Path $bundleRoot "README.md") -Force
if (Test-Path (Join-Path $RepoRoot "GETTING_STARTED.md")) {
    Copy-Item -Path (Join-Path $RepoRoot "GETTING_STARTED.md") -Destination (Join-Path $bundleRoot "GETTING_STARTED.md") -Force
}

Write-Host "[6/6] Creating zip archive"
Compress-Archive -Path (Join-Path $bundleRoot "*") -DestinationPath $archivePath

Write-Host ""
Write-Host "Release bundle ready:" -ForegroundColor Green
Write-Host "  $bundleRoot"
Write-Host "Zip archive:" -ForegroundColor Green
Write-Host "  $archivePath"
