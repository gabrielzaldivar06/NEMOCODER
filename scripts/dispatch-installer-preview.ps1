param(
    [string]$Owner = "",
    [string]$Repo = "",

    [string]$Workflow = "nemo-code-ci.yml",
    [string]$Ref = "main",
    [int]$TimeoutMinutes = 45,
    [int]$PollSeconds = 20,
    [string]$Token = ""
)

$ErrorActionPreference = "Stop"

function Convert-SecureStringToPlainText {
    param([Security.SecureString]$SecureValue)

    if (-not $SecureValue) {
        return ""
    }

    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecureValue)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
}

function Resolve-OwnerRepoFromOrigin {
    $originUrl = (git remote get-url origin 2>$null)
    if (-not $originUrl) {
        return $null
    }

    $httpsMatch = [regex]::Match($originUrl, "github\.com[/:](?<owner>[^/]+)/(?<repo>[^/.]+)(?:\.git)?$")
    if ($httpsMatch.Success) {
        return @{
            owner = $httpsMatch.Groups["owner"].Value
            repo = $httpsMatch.Groups["repo"].Value
        }
    }

    return $null
}

if (-not $Owner -or -not $Repo) {
    $resolved = Resolve-OwnerRepoFromOrigin
    if ($resolved) {
        if (-not $Owner) {
            $Owner = $resolved.owner
        }
        if (-not $Repo) {
            $Repo = $resolved.repo
        }
    }
}

if (-not $Owner -or -not $Repo) {
    throw "Missing owner/repo and could not resolve from origin remote. Provide -Owner and -Repo."
}

if (-not $Token) {
    if ($env:GITHUB_TOKEN) {
        $Token = $env:GITHUB_TOKEN
    } elseif ($env:GH_TOKEN) {
        $Token = $env:GH_TOKEN
    }
}

if (-not $Token) {
    Write-Host "GitHub token not found in environment."
    $secureToken = Read-Host -Prompt "Paste GitHub token (input hidden)" -AsSecureString
    $Token = Convert-SecureStringToPlainText -SecureValue $secureToken
}

if (-not $Token) {
    throw "Missing GitHub token. Provide -Token, set GITHUB_TOKEN/GH_TOKEN, or enter it in the secure prompt."
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = (Resolve-Path (Join-Path $scriptDir "..")).Path
Set-Location $repoRoot

$headers = @{
    Authorization = "Bearer $Token"
    Accept = "application/vnd.github+json"
    "User-Agent" = "nemo-installer-dispatch"
    "X-GitHub-Api-Version" = "2022-11-28"
}

function Invoke-GitHubApi {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("Get", "Post")]
        [string]$Method,
        [Parameter(Mandatory = $true)]
        [string]$Uri,
        [string]$Body = ""
    )

    $request = @{
        Uri = $Uri
        Headers = $headers
        Method = $Method
    }

    if ($Body) {
        $request["Body"] = $Body
        $request["ContentType"] = "application/json"
    }

    try {
        return Invoke-RestMethod @request
    } catch {
        $message = $_.Exception.Message
        $responseBody = ""
        if ($_.ErrorDetails -and $_.ErrorDetails.Message) {
            $responseBody = $_.ErrorDetails.Message
        }
        $diag = "GitHub API call failed: $Method $Uri`n$message"
        if ($responseBody) {
            $diag += "`n$responseBody"
        }
        throw $diag
    }
}

$dispatchUri = "https://api.github.com/repos/$Owner/$Repo/actions/workflows/$Workflow/dispatches"
$dispatchBody = @{
    ref = $Ref
    inputs = @{
        installer_mode = "true"
    }
} | ConvertTo-Json -Depth 6

$startUtc = [DateTime]::UtcNow
$runsUri = "https://api.github.com/repos/$Owner/$Repo/actions/workflows/$Workflow/runs?event=workflow_dispatch&per_page=20"

# Capture the latest existing run before dispatch so we only track a brand-new run.
$latestBeforeDispatch = Invoke-GitHubApi -Method Get -Uri $runsUri
$baselineRunId = 0
if ($latestBeforeDispatch.workflow_runs -and $latestBeforeDispatch.workflow_runs.Count -gt 0) {
    $baselineRunId = [int64]$latestBeforeDispatch.workflow_runs[0].id
}

Write-Host "Dispatching workflow '$Workflow' for $Owner/$Repo (ref=$Ref, installer_mode=true)..."
Invoke-GitHubApi -Method Post -Uri $dispatchUri -Body $dispatchBody | Out-Null

$run = $null
$deadline = $startUtc.AddMinutes($TimeoutMinutes)

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
    $runsResponse = Invoke-GitHubApi -Method Get -Uri $runsUri
    $candidates = @($runsResponse.workflow_runs)

    foreach ($candidate in $candidates) {
        $createdUtc = [DateTime]::Parse($candidate.created_at).ToUniversalTime()
        $candidateId = [int64]$candidate.id
        if (
            $candidateId -gt $baselineRunId -and
            $createdUtc -ge $startUtc.AddMinutes(-2) -and
            $candidate.head_branch -eq $targetBranch
        ) {
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
    $run = Invoke-GitHubApi -Method Get -Uri $runUri
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
$artifactsResponse = Invoke-GitHubApi -Method Get -Uri $artifactsUri
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