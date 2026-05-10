param(
    [int]$Port = 5173
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location ..
npm --prefix apps/mission-control run preview -- --port $Port
