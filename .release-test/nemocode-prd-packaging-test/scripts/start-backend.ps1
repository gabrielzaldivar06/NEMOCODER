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
