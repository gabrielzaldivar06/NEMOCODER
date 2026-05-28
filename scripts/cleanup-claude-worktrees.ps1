<#
.SYNOPSIS
Cleanup stale .claude/worktrees/agent-* directories left behind by Claude Code subagents.

.DESCRIPTION
Claude Code subagents create isolated worktrees under .claude/worktrees/. They are
"locked" (intentionally — to survive session restarts) but the harness does not
prune them automatically. After many sessions the directory accumulates.

This script:
  1. Enumerates worktrees under .claude/worktrees/ via `git worktree list --porcelain`.
  2. For each `agent-*` worktree older than MaxAgeDays:
     - Removes it via `git worktree remove --force <path>`.
     - Deletes the orphan branch `worktree-agent-*`.
  3. Runs `git worktree prune` at the end.

Skips: the active workspace, anything not under .claude/worktrees/, anything younger
than the cutoff. Non-`agent-*` directories (suspicious-wilson-*, etc.) are listed
but not touched unless -IncludeAll is passed.

.PARAMETER MaxAgeDays
Delete worktrees whose directory mtime is older than this. Default: 7.

.PARAMETER DryRun
List what would be deleted without modifying anything. Default: ON.
Pass -DryRun:$false to actually delete.

.PARAMETER IncludeAll
Also process non-`agent-*` worktrees (e.g. `suspicious-wilson-*`). Default: off.

.EXAMPLE
.\scripts\cleanup-claude-worktrees.ps1
  # Lists candidates older than 7 days, does nothing.

.\scripts\cleanup-claude-worktrees.ps1 -DryRun:$false
  # Actually deletes them.

.\scripts\cleanup-claude-worktrees.ps1 -MaxAgeDays 3 -DryRun:$false
  # More aggressive: anything older than 3 days.
#>
[CmdletBinding()]
param(
    [int]$MaxAgeDays = 7,
    [switch]$DryRun = $true,
    [switch]$IncludeAll = $false
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = (Resolve-Path (Join-Path $scriptDir "..")).Path
Set-Location $repoRoot

$wtRoot = Join-Path $repoRoot ".claude\worktrees"
if (-not (Test-Path $wtRoot)) {
    Write-Host "No .claude/worktrees directory — nothing to clean up."
    exit 0
}

$cutoff = (Get-Date).AddDays(-$MaxAgeDays)
Write-Host "Cutoff: anything older than $($cutoff.ToString('yyyy-MM-dd HH:mm:ss'))"
Write-Host "DryRun: $DryRun  IncludeAll: $IncludeAll"
Write-Host ""

# Parse `git worktree list --porcelain` into structured records.
$porcelain = & git worktree list --porcelain 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Error "git worktree list failed — is this a git repo?"
    exit 1
}

$records = @()
$current = $null
foreach ($line in $porcelain) {
    if ($line -match '^worktree (.+)$') {
        if ($current) { $records += $current }
        $current = [PSCustomObject]@{
            Path = $matches[1]
            Branch = $null
            Locked = $false
            HEAD = $null
        }
    } elseif ($line -match '^HEAD (.+)$' -and $current) {
        $current.HEAD = $matches[1]
    } elseif ($line -match '^branch refs/heads/(.+)$' -and $current) {
        $current.Branch = $matches[1]
    } elseif ($line -match '^locked' -and $current) {
        $current.Locked = $true
    }
}
if ($current) { $records += $current }

$mainPath = (Resolve-Path $repoRoot).Path.TrimEnd('\') -replace '\\', '/'
$candidates = @()
foreach ($r in $records) {
    $normalizedPath = $r.Path -replace '\\', '/'
    if ($normalizedPath -eq $mainPath) { continue }  # never touch primary checkout
    if (-not ($normalizedPath -like "*/.claude/worktrees/*")) { continue }
    $name = Split-Path -Leaf $normalizedPath
    if (-not $IncludeAll -and -not ($name -like "agent-*")) { continue }
    if (-not (Test-Path $r.Path)) {
        # Worktree directory gone but git still has the registration — prunable.
        $candidates += [PSCustomObject]@{ Path = $r.Path; Branch = $r.Branch; Reason = "missing_directory"; LastWrite = $null }
        continue
    }
    $age = (Get-Item $r.Path).LastWriteTime
    if ($age -gt $cutoff) { continue }
    $candidates += [PSCustomObject]@{ Path = $r.Path; Branch = $r.Branch; Reason = "stale"; LastWrite = $age }
}

if ($candidates.Count -eq 0) {
    Write-Host "No stale worktrees found (older than $MaxAgeDays days)."
    exit 0
}

Write-Host "Found $($candidates.Count) candidate(s):"
foreach ($c in $candidates) {
    $ageStr = if ($c.LastWrite) { $c.LastWrite.ToString('yyyy-MM-dd HH:mm') } else { "—" }
    Write-Host ("  {0,-50} branch={1,-44} last={2,-20} ({3})" -f (Split-Path -Leaf $c.Path), $c.Branch, $ageStr, $c.Reason)
}
Write-Host ""

if ($DryRun) {
    Write-Host "DryRun is ON — no deletion performed. Pass -DryRun:`$false to actually delete." -ForegroundColor Yellow
    exit 0
}

$removed = 0
$failed = 0
foreach ($c in $candidates) {
    try {
        & git worktree remove --force $c.Path 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) {
            # Fallback: force-remove the directory and let git prune handle the registration.
            if (Test-Path $c.Path) {
                Remove-Item -Recurse -Force $c.Path -ErrorAction Stop
            }
        }
        if ($c.Branch) {
            & git branch -D $c.Branch 2>&1 | Out-Null
        }
        $removed++
        Write-Host "  removed: $(Split-Path -Leaf $c.Path)" -ForegroundColor Green
    } catch {
        $failed++
        Write-Warning "  failed: $($c.Path) — $($_.Exception.Message)"
    }
}

& git worktree prune 2>&1 | Out-Null

Write-Host ""
Write-Host "Done. removed=$removed failed=$failed" -ForegroundColor Cyan
