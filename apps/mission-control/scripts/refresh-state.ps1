$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path "$PSScriptRoot\..\..\.."
$target = Join-Path $PSScriptRoot "..\public\mission-control-state.sample.json"
Push-Location $repoRoot
try {
  $env:PYTHONPATH = "src"
  c:/dev/dev4/.venv/Scripts/python.exe -m nemo_coding_platform mission-control-state --repo . --runtimes .nemo-runtimes --save-json $target --json | Out-Null
}
finally {
  Pop-Location
}
Write-Output "Wrote $target"