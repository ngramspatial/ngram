param(
    [Parameter(Position = 0)]
    [ValidateSet("on", "off", "status", "restart")]
    [string]$Action = "status",
    [string]$TunnelName = "ngram-inference",
    [string]$CloudflaredConfig = "",
    [string]$TunnelUrl = "",
    [string]$GatewayHost = "127.0.0.1",
    [int]$GatewayPort = 8010,
    [switch]$LeaveOllamaRunning
)

$ErrorActionPreference = "Stop"

$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptRoot
# Ensure this script sees the latest user/machine PATH entries (new installs in old shells).
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
$machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
if ($userPath -or $machinePath) {
    $env:Path = "$userPath;$machinePath;$env:Path"
}
if (-not $CloudflaredConfig) {
    $userProfile = [Environment]::GetFolderPath("UserProfile")
    $CloudflaredConfig = Join-Path $userProfile ".cloudflared\config.yml"
}

function Write-Step([string]$Message) {
    Write-Host "==> $Message"
}

function Get-PythonExecutable() {
    $venvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    if (Test-Path $venvPython) {
        return $venvPython
    }
    foreach ($name in @("py", "python")) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd -and $cmd.Source -and (Test-Path $cmd.Source)) {
            return $cmd.Source
        }
    }
    throw "Python executable not found. Create the repository .venv before starting the gateway."
}

function Get-OllamaExecutable() {
    $cmd = Get-Command ollama -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source -and (Test-Path $cmd.Source)) {
        return $cmd.Source
    }
    $localOllama = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
    if (Test-Path $localOllama) {
        return $localOllama
    }
    throw "Ollama executable not found. Install Ollama or add it to PATH."
}

function Get-CloudflaredExecutable() {
    $repoLocal = Join-Path $RepoRoot ".runtime\bin\cloudflared.exe"
    if (Test-Path $repoLocal) {
        return $repoLocal
    }
    $cmd = Get-Command cloudflared -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source -and (Test-Path $cmd.Source)) {
        return $cmd.Source
    }
    $linkPath = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Links\cloudflared.exe"
    if (Test-Path $linkPath) {
        return $linkPath
    }
    $pkgRoot = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages"
    if (Test-Path $pkgRoot) {
        $dirs = Get-ChildItem -Path $pkgRoot -Directory -Filter "Cloudflare.cloudflared_*" -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending
        foreach ($d in $dirs) {
            $exe = Join-Path $d.FullName "cloudflared.exe"
            if (Test-Path $exe) {
                return $exe
            }
        }
    }
    throw "cloudflared executable not found. Reinstall with: winget install --id Cloudflare.cloudflared -e"
}

function Read-DotEnvValue([string]$Key) {
    $dotenvPath = Join-Path $RepoRoot ".env"
    if (-not (Test-Path $dotenvPath)) {
        return ""
    }
    foreach ($raw in (Get-Content $dotenvPath)) {
        $line = $raw.Trim()
        if (-not $line -or $line.StartsWith("#")) {
            continue
        }
        $eq = $line.IndexOf("=")
        if ($eq -lt 1) {
            continue
        }
        $k = $line.Substring(0, $eq).Trim()
        if ($k -ne $Key) {
            continue
        }
        $v = $line.Substring($eq + 1).Trim().Trim("'").Trim('"')
        return $v
    }
    return ""
}

function Get-SettingValue([string]$Key) {
    $fromProcess = [Environment]::GetEnvironmentVariable($Key, "Process")
    if ($fromProcess -and $fromProcess.Trim()) {
        return $fromProcess.Trim()
    }
    return Read-DotEnvValue $Key
}

function Get-GatewayToken() {
    if ($env:INFERENCE_GATEWAY_TOKEN -and $env:INFERENCE_GATEWAY_TOKEN.Trim()) {
        return $env:INFERENCE_GATEWAY_TOKEN.Trim()
    }
    if ($env:NGRAM_INFERENCE_GATEWAY_TOKEN -and $env:NGRAM_INFERENCE_GATEWAY_TOKEN.Trim()) {
        return $env:NGRAM_INFERENCE_GATEWAY_TOKEN.Trim()
    }
    $fromEnvFile = Read-DotEnvValue "INFERENCE_GATEWAY_TOKEN"
    if ($fromEnvFile) {
        return $fromEnvFile
    }
    $fromEnvFileAlt = Read-DotEnvValue "NGRAM_INFERENCE_GATEWAY_TOKEN"
    if ($fromEnvFileAlt) {
        return $fromEnvFileAlt
    }
    return ""
}

function Get-TunnelUrlFromConfig([string]$ConfigPath) {
    if (-not (Test-Path $ConfigPath)) {
        return ""
    }
    foreach ($raw in (Get-Content $ConfigPath)) {
        $line = $raw.Trim()
        if ($line -like "- hostname:*") {
            $hostnameFound = $line.Substring("- hostname:".Length).Trim()
            if ($hostnameFound) {
                return "https://$hostnameFound"
            }
        }
        if ($line -like "hostname:*") {
            $hostnameFound = $line.Substring("hostname:".Length).Trim()
            if ($hostnameFound) {
                return "https://$hostnameFound"
            }
        }
    }
    return ""
}

function Test-Url(
    [string]$Url,
    [hashtable]$Headers = @{},
    [int]$TimeoutSec = 3,
    [int[]]$AcceptStatus = @(200)
) {
    try {
        $resp = Invoke-WebRequest -Uri $Url -Headers $Headers -Method Get -TimeoutSec $TimeoutSec -UseBasicParsing
        return ($AcceptStatus -contains [int]$resp.StatusCode)
    } catch {
        if ($_.Exception.Response) {
            $code = [int]$_.Exception.Response.StatusCode.value__
            return ($AcceptStatus -contains $code)
        }
        return $false
    }
}

function Wait-Url(
    [string]$Url,
    [hashtable]$Headers = @{},
    [int]$Retries = 30,
    [int]$SleepSec = 1,
    [int[]]$AcceptStatus = @(200)
) {
    for ($i = 0; $i -lt $Retries; $i++) {
        if (Test-Url -Url $Url -Headers $Headers -TimeoutSec 3 -AcceptStatus $AcceptStatus) {
            return $true
        }
        Start-Sleep -Seconds $SleepSec
    }
    return $false
}

function Get-GatewayPythonProcesses() {
    Get-CimInstance Win32_Process | Where-Object {
        $_.Name -eq "python.exe" -and $_.CommandLine -match "ngram\.inference_gateway"
    }
}

function Get-CloudflaredProcesses([string]$ExpectedTunnelName) {
    Get-CimInstance Win32_Process | Where-Object {
        $_.Name -eq "cloudflared.exe" -and (
            $_.CommandLine -match "tunnel run" -or
            $_.CommandLine -match [regex]::Escape($ExpectedTunnelName)
        )
    }
}

function Get-OllamaServeProcesses() {
    Get-CimInstance Win32_Process | Where-Object {
        $_.Name -eq "ollama.exe" -and $_.CommandLine -match "\bserve\b"
    }
}

function Stop-ProcSet([array]$Procs, [string]$Label) {
    if (-not $Procs -or $Procs.Count -eq 0) {
        Write-Host "${Label}: not running"
        return
    }
    foreach ($p in $Procs) {
        try {
            Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop
            Write-Host "${Label}: stopped PID $($p.ProcessId)"
        } catch {
            Write-Warning "${Label}: failed to stop PID $($p.ProcessId): $($_.Exception.Message)"
        }
    }
}

$localGatewayUrl = "http://$GatewayHost`:$GatewayPort/health"
$effectiveTunnelUrl = $TunnelUrl
if (-not $effectiveTunnelUrl) {
    $effectiveTunnelUrl = Get-TunnelUrlFromConfig -ConfigPath $CloudflaredConfig
}
$token = Get-GatewayToken
$authHeaders = @{}
if ($token) {
    $authHeaders = @{ Authorization = "Bearer $token" }
}
$cfAccessTeamDomain = Get-SettingValue "CF_ACCESS_TEAM_DOMAIN"
$cfAccessAudience = Get-SettingValue "CF_ACCESS_AUD"
$cfAccessClientId = Get-SettingValue "NGRAM_CF_ACCESS_CLIENT_ID"
if (-not $cfAccessClientId) {
    $cfAccessClientId = Get-SettingValue "CF_ACCESS_CLIENT_ID"
}
$cfAccessClientSecret = Get-SettingValue "NGRAM_CF_ACCESS_CLIENT_SECRET"
if (-not $cfAccessClientSecret) {
    $cfAccessClientSecret = Get-SettingValue "CF_ACCESS_CLIENT_SECRET"
}
$publicAuthHeaders = @{}
foreach ($key in $authHeaders.Keys) {
    $publicAuthHeaders[$key] = $authHeaders[$key]
}
if ($cfAccessClientId -and $cfAccessClientSecret) {
    $publicAuthHeaders["CF-Access-Client-Id"] = $cfAccessClientId
    $publicAuthHeaders["CF-Access-Client-Secret"] = $cfAccessClientSecret
}

switch ($Action) {
    "on" {
        if (-not $cfAccessTeamDomain -or -not $cfAccessAudience -or -not $cfAccessClientId -or -not $cfAccessClientSecret) {
            throw (
                "Refusing to open a public tunnel without Cloudflare Access. Set " +
                "CF_ACCESS_TEAM_DOMAIN, CF_ACCESS_AUD, NGRAM_CF_ACCESS_CLIENT_ID, and " +
                "NGRAM_CF_ACCESS_CLIENT_SECRET in .env, then add the same NGRAM_CF_ACCESS_* " +
                "service-token pair to every remote caller."
            )
        }
        if (-not $token) {
            $ollamaUpNoToken = Test-Url -Url "http://127.0.0.1:11434/api/tags"
            $gatewayReachableNoToken = Test-Url -Url $localGatewayUrl -AcceptStatus @(401)
            $cloudflaredCount = @(Get-CloudflaredProcesses -ExpectedTunnelName $TunnelName).Count
            if ($ollamaUpNoToken -and $gatewayReachableNoToken -and $cloudflaredCount -gt 0) {
                Write-Host "Gateway stack already appears to be running (token not present in current shell/.env)."
                Write-Host "Set INFERENCE_GATEWAY_TOKEN in .env or shell to allow this command to start/restart services."
                return
            }
            throw "Missing gateway token. Set INFERENCE_GATEWAY_TOKEN (or NGRAM_INFERENCE_GATEWAY_TOKEN), or put one in .env."
        }

        if (Test-Url -Url "http://127.0.0.1:11434/api/tags") {
            Write-Host "ollama: already healthy"
        } else {
            Write-Step "starting ollama serve"
            $ollamaExe = Get-OllamaExecutable
            Start-Process -FilePath $ollamaExe -ArgumentList @("serve") -WindowStyle Hidden | Out-Null
            if (-not (Wait-Url -Url "http://127.0.0.1:11434/api/tags" -Retries 40 -SleepSec 1)) {
                throw "Ollama did not become healthy at http://127.0.0.1:11434."
            }
            Write-Host "ollama: healthy"
        }

        if (Test-Url -Url $localGatewayUrl -Headers $authHeaders) {
            Write-Host "gateway: already healthy"
        } else {
            Write-Step "starting inference gateway"
            $pythonExe = Get-PythonExecutable
            $childEnv = @{
                INFERENCE_GATEWAY_TOKEN = $token
                INFERENCE_GATEWAY_REQUIRE_CF_ACCESS = "1"
                CF_ACCESS_TEAM_DOMAIN = $cfAccessTeamDomain
                CF_ACCESS_AUD = $cfAccessAudience
            }
            $previousChildEnv = @{}
            try {
                # Start-Process inherits the current environment. Keeping the token out
                # of the child command line also keeps it out of process listings.
                foreach ($key in $childEnv.Keys) {
                    $previousChildEnv[$key] = [Environment]::GetEnvironmentVariable($key, "Process")
                    [Environment]::SetEnvironmentVariable($key, $childEnv[$key], "Process")
                }
                Start-Process -FilePath $pythonExe -ArgumentList @(
                    "-m",
                    "ngram.inference_gateway"
                ) -WorkingDirectory $RepoRoot -WindowStyle Hidden | Out-Null
            } finally {
                foreach ($key in $childEnv.Keys) {
                    [Environment]::SetEnvironmentVariable($key, $previousChildEnv[$key], "Process")
                }
            }
            if (-not (Wait-Url -Url $localGatewayUrl -Headers $authHeaders -Retries 50 -SleepSec 1)) {
                throw "Inference gateway did not become healthy at $localGatewayUrl."
            }
            Write-Host "gateway: healthy"
        }

        if (-not (Test-Path $CloudflaredConfig)) {
            throw "Missing cloudflared config at '$CloudflaredConfig'."
        }

        $tunnelHealthy = $false
        if ($effectiveTunnelUrl) {
            $tunnelHealthy = Test-Url -Url "$effectiveTunnelUrl/health" -Headers $publicAuthHeaders
        }
        if ($tunnelHealthy) {
            Write-Host "tunnel: already healthy ($effectiveTunnelUrl)"
        } else {
            Write-Step "starting cloudflared tunnel"
            $cloudflaredExe = Get-CloudflaredExecutable
            Start-Process -FilePath $cloudflaredExe -ArgumentList @(
                "--config",
                $CloudflaredConfig,
                "tunnel",
                "run",
                $TunnelName
            ) -WindowStyle Hidden | Out-Null
            if ($effectiveTunnelUrl) {
                if (Wait-Url -Url "$effectiveTunnelUrl/health" -Headers $publicAuthHeaders -Retries 60 -SleepSec 1) {
                    Write-Host "tunnel: healthy ($effectiveTunnelUrl)"
                } else {
                    Write-Warning "Tunnel started but health probe to '$effectiveTunnelUrl/health' did not succeed yet."
                }
            } else {
                Write-Host "tunnel: started (no tunnel URL discovered; pass -TunnelUrl to probe externally)"
            }
        }

        Write-Host ""
        Write-Host "Gateway stack is ON."
    }
    "off" {
        Stop-ProcSet -Procs (Get-CloudflaredProcesses -ExpectedTunnelName $TunnelName) -Label "cloudflared"
        Stop-ProcSet -Procs (Get-GatewayPythonProcesses) -Label "inference gateway"
        if ($LeaveOllamaRunning) {
            Write-Host "ollama: left running by request"
        } else {
            Stop-ProcSet -Procs (Get-OllamaServeProcesses) -Label "ollama"
        }
        Write-Host ""
        Write-Host "Gateway stack is OFF."
    }
    "status" {
        $ollamaHealthy = Test-Url -Url "http://127.0.0.1:11434/api/tags"
        $gatewayHealthy = $false
        if ($token) {
            $gatewayHealthy = Test-Url -Url $localGatewayUrl -Headers $authHeaders
        } else {
            # No token available: unauthenticated 401 still means process is reachable.
            $gatewayHealthy = Test-Url -Url $localGatewayUrl -AcceptStatus @(401)
        }

        $tunnelHealthy = $false
        if ($effectiveTunnelUrl) {
            if ($token) {
                $tunnelHealthy = Test-Url -Url "$effectiveTunnelUrl/health" -Headers $publicAuthHeaders
            } else {
                $tunnelHealthy = Test-Url -Url "$effectiveTunnelUrl/health" -AcceptStatus @(401)
            }
        }

        $cloudflared = @(Get-CloudflaredProcesses -ExpectedTunnelName $TunnelName)
        $gateway = @(Get-GatewayPythonProcesses)
        $ollama = @(Get-OllamaServeProcesses)

        Write-Host "Gateway status"
        Write-Host "--------------"
        Write-Host "ollama process(es): $($ollama.Count)"
        Write-Host "ollama healthy:      $ollamaHealthy"
        Write-Host "gateway process(es): $($gateway.Count)"
        Write-Host "gateway healthy:     $gatewayHealthy"
        Write-Host "cloudflared procs:   $($cloudflared.Count)"
        if ($effectiveTunnelUrl) {
            Write-Host "tunnel url:          $effectiveTunnelUrl"
            Write-Host "tunnel healthy:      $tunnelHealthy"
        } else {
            Write-Host "tunnel url:          (not set)"
            Write-Host "tunnel healthy:      (unknown)"
        }
        if (-not $token) {
            Write-Host "token source:        missing (status probes use 401 reachability)"
        } else {
            Write-Host "token source:        present"
        }
        if ($cfAccessTeamDomain -and $cfAccessAudience -and $cfAccessClientId -and $cfAccessClientSecret) {
            Write-Host "cloudflare access:   configured"
        } else {
            Write-Host "cloudflare access:   missing (tunnel start will fail closed)"
        }
    }
    "restart" {
        $scriptPath = Join-Path $ScriptRoot "gateway.ps1"
        Write-Step "gateway restart: stopping stack"
        $offSplat = @{
            Action            = "off"
            TunnelName        = $TunnelName
            CloudflaredConfig = $CloudflaredConfig
            TunnelUrl         = $TunnelUrl
            GatewayHost       = $GatewayHost
            GatewayPort       = $GatewayPort
        }
        if ($LeaveOllamaRunning) {
            $offSplat["LeaveOllamaRunning"] = $true
        }
        & $scriptPath @offSplat
        Start-Sleep -Seconds 2
        Write-Step "gateway restart: starting stack"
        $onSplat = @{
            Action            = "on"
            TunnelName        = $TunnelName
            CloudflaredConfig = $CloudflaredConfig
            TunnelUrl         = $TunnelUrl
            GatewayHost       = $GatewayHost
            GatewayPort       = $GatewayPort
        }
        & $scriptPath @onSplat
        Write-Host ""
        Write-Host "Gateway stack RESTART complete."
    }
}
