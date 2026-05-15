param(
    [int]$ApiPort = 8787,
    [int]$UiPort = 5173,
    [string]$NemoMcpUrl = "",
    [string]$PythonExe = "",
    [switch]$SkipNemoCheck
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Net.Http

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = (Resolve-Path (Join-Path $ScriptDir "..")).Path
$RuntimeRoot = Join-Path $RepoRoot ".spacecode-runtimes"
$LogDir = Join-Path $RuntimeRoot "logs"
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

function Get-PythonLaunch {
    if ($PythonExe) {
        if (-not (Test-Path $PythonExe)) {
            throw "PythonExe was provided but does not exist: $PythonExe"
        }
        return @{ File = (Resolve-Path $PythonExe).Path; Args = @() }
    }

    if ($env:SPACE_CODE_PYTHON) {
        if (-not (Test-Path $env:SPACE_CODE_PYTHON)) {
            throw "SPACE_CODE_PYTHON points to a missing executable: $env:SPACE_CODE_PYTHON"
        }
        return @{ File = (Resolve-Path $env:SPACE_CODE_PYTHON).Path; Args = @() }
    }

    $venvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    if (Test-Path $venvPython) {
        try {
            & $venvPython --version *> $null
            if ($LASTEXITCODE -eq 0) {
                return @{ File = $venvPython; Args = @() }
            }
        } catch {
            Write-Warning ".venv Python exists but could not start: $($_.Exception.Message)"
        }
    }

    if ($env:USERPROFILE) {
        $codexPython = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
        if (Test-Path $codexPython) {
            try {
                & $codexPython --version *> $null
                if ($LASTEXITCODE -eq 0) {
                    Write-Host "Using bundled Codex Python because .venv Python is unavailable."
                    return @{ File = $codexPython; Args = @() }
                }
            } catch {
                Write-Warning "Bundled Codex Python exists but could not start: $($_.Exception.Message)"
            }
        }
    }

    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        return @{ File = $pyLauncher.Source; Args = @("-3") }
    }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        return @{ File = $python.Source; Args = @() }
    }

    throw "No Python executable found. Create .venv or install Python/py launcher."
}

function Get-NpmFile {
    $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if (-not $npm) {
        $npm = Get-Command npm -ErrorAction SilentlyContinue
    }
    if (-not $npm) {
        throw "npm was not found in PATH."
    }
    return $npm.Source
}

function Test-TcpPort {
    param(
        [string]$HostName,
        [int]$Port
    )

    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $connect = $client.BeginConnect($HostName, $Port, $null, $null)
        if (-not $connect.AsyncWaitHandle.WaitOne(400)) {
            return $false
        }
        $client.EndConnect($connect)
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Test-NemoMcpSse {
    param([string]$Url)

    $client = [System.Net.Http.HttpClient]::new()
    $client.Timeout = [TimeSpan]::FromSeconds(4)
    try {
        $request = [System.Net.Http.HttpRequestMessage]::new([System.Net.Http.HttpMethod]::Get, $Url)
        $request.Headers.Accept.ParseAdd("text/event-stream")
        $response = $client.SendAsync($request, [System.Net.Http.HttpCompletionOption]::ResponseHeadersRead).GetAwaiter().GetResult()
        $contentType = ""
        if ($response.Content.Headers.ContentType) {
            $contentType = $response.Content.Headers.ContentType.MediaType
        }
        return @{
            Ok = $response.IsSuccessStatusCode -and $contentType -eq "text/event-stream"
            StatusCode = [int]$response.StatusCode
            ContentType = $contentType
        }
    } catch {
        return @{ Ok = $false; StatusCode = 0; ContentType = ""; Error = $_.Exception.Message }
    } finally {
        $client.Dispose()
    }
}

function Invoke-JsonPost {
    param(
        [string]$Url,
        [string]$Json
    )

    $client = [System.Net.Http.HttpClient]::new()
    $client.Timeout = [TimeSpan]::FromSeconds(8)
    try {
        $content = [System.Net.Http.StringContent]::new($Json, [System.Text.Encoding]::UTF8, "application/json")
        $response = $client.PostAsync($Url, $content).GetAwaiter().GetResult()
        $body = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
        return @{
            Ok = $response.IsSuccessStatusCode
            StatusCode = [int]$response.StatusCode
            Body = $body
        }
    } catch {
        return @{ Ok = $false; StatusCode = 0; Body = $_.Exception.Message }
    } finally {
        $client.Dispose()
    }
}

function Wait-ForPort {
    param(
        [string]$Name,
        [int]$Port,
        [int]$Seconds = 20
    )

    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-TcpPort -HostName "127.0.0.1" -Port $Port) {
            Write-Host "$Name ready on http://127.0.0.1:$Port"
            return $true
        }
        Start-Sleep -Milliseconds 500
    }
    Write-Warning "$Name did not open port $Port within $Seconds seconds."
    return $false
}

function Start-LocalProcess {
    param(
        [string]$Name,
        [string]$File,
        [string[]]$Arguments,
        [string]$WorkingDirectory,
        [string]$StdoutPath,
        [string]$StderrPath
    )

    Write-Host "Starting $Name..."
    return Start-Process `
        -FilePath $File `
        -ArgumentList $Arguments `
        -WorkingDirectory $WorkingDirectory `
        -RedirectStandardOutput $StdoutPath `
        -RedirectStandardError $StderrPath `
        -WindowStyle Hidden `
        -PassThru
}

Set-Location $RepoRoot

# Auto-read nemo_mcp_url from settings.json when not supplied as a parameter
if (-not $NemoMcpUrl) {
    $settingsPath = Join-Path $RuntimeRoot "mission-control\settings.json"
    if (Test-Path $settingsPath) {
        try {
            $settings = Get-Content $settingsPath -Raw | ConvertFrom-Json
            if ($settings.nemo_mcp_url) {
                $NemoMcpUrl = $settings.nemo_mcp_url
            }
        } catch {
            Write-Warning "Could not read nemo_mcp_url from settings.json: $($_.Exception.Message)"
        }
    }
}

if (-not $SkipNemoCheck -and $NemoMcpUrl -notlike "stdio://*") {
    $nemo = Test-NemoMcpSse -Url $NemoMcpUrl
    if (-not $nemo.Ok) {
        $detail = if ($nemo.Error) { $nemo.Error } else { "HTTP $($nemo.StatusCode), content-type '$($nemo.ContentType)'" }
        throw "NEMO MCP SSE is not ready at $NemoMcpUrl ($detail). Start NEMO first, then retry."
    }
    Write-Host "NEMO MCP ready at $NemoMcpUrl"
}

$python = Get-PythonLaunch
$npmFile = Get-NpmFile
$env:PYTHONPATH = Join-Path $RepoRoot "src"
$env:SPACE_CODE_NEMO_MCP_URL = $NemoMcpUrl
$env:SPACE_CODE_MISSION_CONTROL_API_URL = "http://127.0.0.1:$ApiPort"

if (Test-TcpPort -HostName "127.0.0.1" -Port $ApiPort) {
    Write-Host "Mission Control API already listening on http://127.0.0.1:$ApiPort"
} else {
    $apiArgs = @($python.Args + @(
        "-m",
        "nemo_coding_platform",
        "mission-control-server",
        "--repo",
        $RepoRoot,
        "--runtimes",
        $RuntimeRoot,
        "--port",
        "$ApiPort"
    ))
    Start-LocalProcess `
        -Name "Mission Control API" `
        -File $python.File `
        -Arguments $apiArgs `
        -WorkingDirectory $RepoRoot `
        -StdoutPath (Join-Path $LogDir "mission-control-api.out.log") `
        -StderrPath (Join-Path $LogDir "mission-control-api.err.log") | Out-Null
}

$UiRoot = Join-Path $RepoRoot "apps\mission-control"
$NodeModules = Join-Path $UiRoot "node_modules"
if (-not (Test-Path $NodeModules)) {
    throw "Missing apps/mission-control/node_modules. Run: npm --prefix apps/mission-control install"
}

if (Test-TcpPort -HostName "127.0.0.1" -Port $UiPort) {
    Write-Host "Mission Control UI already listening on http://127.0.0.1:$UiPort"
} else {
    Start-LocalProcess `
        -Name "Mission Control UI" `
        -File $npmFile `
        -Arguments @("run", "dev", "--", "--port", "$UiPort") `
        -WorkingDirectory $UiRoot `
        -StdoutPath (Join-Path $LogDir "mission-control-ui.out.log") `
        -StderrPath (Join-Path $LogDir "mission-control-ui.err.log") | Out-Null
}

$apiReady = Wait-ForPort -Name "Mission Control API" -Port $ApiPort
$uiReady = Wait-ForPort -Name "Mission Control UI" -Port $UiPort
if (-not $apiReady -or -not $uiReady) {
    throw "MVP local did not fully start. Check logs in $LogDir."
}

$mcpStatusUrl = "http://127.0.0.1:$ApiPort/api/nemo/mcp-status"
$mcpStatusBody = "{`"nemo_mcp_url`":`"$NemoMcpUrl`",`"selected_nemo_tools`":[`"prime_context`",`"search_memories`"]}"
$mcpStatus = Invoke-JsonPost -Url $mcpStatusUrl -Json $mcpStatusBody
if (-not $mcpStatus.Ok -or $mcpStatus.Body -notmatch '"active"\s*:\s*true') {
    throw "Mission Control API started, but NEMO MCP watcher did not report active. Response: $($mcpStatus.Body)"
}

Write-Host ""
Write-Host "MVP local ready:"
Write-Host "  UI:  http://127.0.0.1:$UiPort"
Write-Host "  API: http://127.0.0.1:$ApiPort"
Write-Host "  MCP: $NemoMcpUrl"
Write-Host "  Logs: $LogDir"
