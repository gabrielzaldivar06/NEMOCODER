param(
    [Parameter(Mandatory = $true)]
    [string]$Owner,

    [Parameter(Mandatory = $true)]
    [string]$Repo,

    [string]$Workflow = "nemo-code-ci.yml",
    [string]$Ref = "main",
    [int]$TimeoutMinutes = 45,
    [int]$PollSeconds = 20,
    [string]$Token = ""
)

$ErrorActionPreference = "Stop"

if (-not $Token) {
    if ($env:GITHUB_TOKEN) {
        $Token = $env:GITHUB_TOKEN
    } elseif ($env:GH_TOKEN) {
        $Token = $env:GH_TOKEN
    }
}

if (-not $Token) {
    throw "Missing GitHub token. Provide -Token or set GITHUB_TOKEN/GH_TOKEN."
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = (Resolve-Path (Join-Path $scriptDir "..")).Path
Set-Location $repoRoot

$headers = @{
    Authorization = "Bearer $Token"
    Accept = "application/vnd.github+json"
    "X-GitHub-Api-Version" = "2022-11-28"
}

$dispatchUri = "https://api.github.com/repos/$Owner/$Repo/actions/workflows/$Workflow/dispatches"
$dispatchBody = @{
    ref = $Ref
    inputs = @{
        installer_mode = "true"
    }
} | ConvertTo-Json -Depth 6

$startUtc = [DateTime]::UtcNow
Write-Host "Dispatching workflow '$Workflow' for $Owner/$Repo (ref=$Ref, installer_mode=true)..."
Invoke-RestMethod -Uri $dispatchUri -Headers $headers -Method Post -Body $dispatchBody -ContentType "application/json"

$run = $null
$deadline = $startUtc.AddMinutes($TimeoutMinutes)
$runsUri = "https://api.github.com/repos/$Owner/$Repo/actions/workflows/$Workflow/runs?event=workflow_dispatch&per_page=20"

function Get-NormalizedRef {
    param([string]$RawRef)

    if ($RawRef.StartsWith("refs/heads/")) {
        return $RawRef.Substring("refs/heads/".Length)
    }
    return $RawRef
}

$targetBranch = Get-NormalizedRef -RawRef $Ref

Write-Host "Waiting for workflow run to appear..."
while (([DateTime]::UtcNow) -lt $deadline -and -not $run) {
    $runsResponse = Invoke-RestMethod -Uri $runsUri -Headers $headers -Method Get
    $candidates = @($runsResponse.workflow_runs)

    foreach ($candidate in $candidates) {
        $createdUtc = [DateTime]::Parse($candidate.created_at).ToUniversalTime()
        if ($createdUtc -ge $startUtc.AddMinutes(-2) -and $candidate.head_branch -eq $targetBranch) {
            $run = $candidate
            break
        }
    }

    if (-not $run) {
        Start-Sleep -Seconds $PollSeconds
    }
}

if (-not $run) {
    throw "Timed out waiting for workflow run creation."
}

$runId = $run.id
$runUri = "https://api.github.com/repos/$Owner/$Repo/actions/runs/$runId"
Write-Host "Tracking run id=$runId ..."

while (([DateTime]::UtcNow) -lt $deadline) {
    $run = Invoke-RestMethod -Uri $runUri -Headers $headers -Method Get
    Write-Host ("Run status={0} conclusion={1}" -f $run.status, $run.conclusion)

    if ($run.status -eq "completed") {
        break
    }

    Start-Sleep -Seconds $PollSeconds
}

if ($run.status -ne "completed") {
    throw "Timed out waiting for run completion (id=$runId)."
}

$artifactsUri = "https://api.github.com/repos/$Owner/$Repo/actions/runs/$runId/artifacts"
$artifactsResponse = Invoke-RestMethod -Uri $artifactsUri -Headers $headers -Method Get
$artifactNames = @($artifactsResponse.artifacts | ForEach-Object { $_.name })

$requiredArtifacts = @("release-confidence-evidence", "installer-transition-evidence")
$missing = @()
foreach ($artifact in $requiredArtifacts) {
    if ($artifactNames -notcontains $artifact) {
        $missing += $artifact
    }
}

$result = [ordered]@{
    owner = $Owner
    repo = $Repo
    workflow = $Workflow
    ref = $Ref
    installer_mode = "true"
    run_id = $runId
    run_number = $run.run_number
    run_url = $run.html_url
    status = $run.status
    conclusion = $run.conclusion
    started_at_utc = $run.run_started_at
    updated_at_utc = $run.updated_at
    required_artifacts = $requiredArtifacts
    published_artifacts = $artifactNames
    missing_artifacts = $missing
    generated_at_utc = ([DateTime]::UtcNow.ToString("o"))
}

New-Item -ItemType Directory -Path artifacts -Force | Out-Null
$ts = Get-Date -Format "yyyyMMddHHmmss"
$jsonOut = "artifacts/ci-installer-run-$ts.json"
$mdOut = "artifacts/ci-installer-run-$ts.md"

$result | ConvertTo-Json -Depth 6 | Set-Content -Path $jsonOut -Encoding UTF8

$md = @(
    "# CI Installer Preview Run",
    "",
    "- repo: $Owner/$Repo",
    "- workflow: $Workflow",
    "- ref: $Ref",
    "- run_id: $runId",
    "- run_number: $($run.run_number)",
    "- status: $($run.status)",
    "- conclusion: $($run.conclusion)",
    "- run_url: $($run.html_url)",
    "",
    "## Artifacts",
    "- required: $($requiredArtifacts -join ', ')",
    "- published: $($artifactNames -join ', ')"
)

if ($missing.Count -gt 0) {
    $md += "- missing: $($missing -join ', ')"
} else {
    $md += "- missing: none"
}

$md | Set-Content -Path $mdOut -Encoding UTF8

Write-Host "Wrote evidence: $jsonOut"
Write-Host "Wrote evidence: $mdOut"

if ($run.conclusion -ne "success") {
    throw "Workflow completed with conclusion '$($run.conclusion)'."
}

if ($missing.Count -gt 0) {
    throw "Workflow succeeded but required artifacts are missing: $($missing -join ', ')"
}

Write-Host "Installer preview run passed with required artifacts present."