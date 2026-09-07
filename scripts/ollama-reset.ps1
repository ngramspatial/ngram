param(
    [int]$ContextLength = 16384,
    [int]$MaxLoadedModels = 1,
    [string]$KeepAlive = "60s",
    [int]$NumParallel = 1,
    [switch]$SkipStart
)

$ErrorActionPreference = "Stop"

function Write-Step([string]$Message) {
    Write-Host "==> $Message"
}

function Show-GpuSummary() {
    $smi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
    if (-not $smi) {
        Write-Host "GPU summary: nvidia-smi not found"
        return
    }
    & $smi.Source --query-gpu=name,memory.total,memory.used,memory.free,utilization.gpu --format=csv,noheader
}

Write-Step "stopping gateway stack"
ngram gateway off

Write-Step "killing any leftover ollama.exe processes"
$ollamaProcs = Get-CimInstance Win32_Process | Where-Object { $_.Name -eq "ollama.exe" }
if ($ollamaProcs) {
    foreach ($p in $ollamaProcs) {
        try {
            Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop
            Write-Host "stopped ollama PID $($p.ProcessId)"
        } catch {
            Write-Warning "failed to stop ollama PID $($p.ProcessId): $($_.Exception.Message)"
        }
    }
} else {
    Write-Host "no ollama processes found"
}

Start-Sleep -Seconds 2

Write-Host ""
Write-Host "Pre-start checks"
Write-Host "----------------"
$remaining = Get-CimInstance Win32_Process | Where-Object { $_.Name -eq "ollama.exe" }
if ($remaining) {
    Write-Host "ollama processes remaining: $($remaining.Count)"
    $remaining | Select-Object ProcessId, CommandLine | Format-Table -AutoSize
} else {
    Write-Host "ollama processes remaining: 0"
}
Show-GpuSummary

Write-Step "persisting safe ollama settings (User scope)"
[Environment]::SetEnvironmentVariable("OLLAMA_MAX_LOADED_MODELS", "$MaxLoadedModels", "User")
[Environment]::SetEnvironmentVariable("OLLAMA_KEEP_ALIVE", "$KeepAlive", "User")
[Environment]::SetEnvironmentVariable("OLLAMA_CONTEXT_LENGTH", "$ContextLength", "User")
[Environment]::SetEnvironmentVariable("OLLAMA_NUM_PARALLEL", "$NumParallel", "User")
$env:OLLAMA_MAX_LOADED_MODELS = "$MaxLoadedModels"
$env:OLLAMA_KEEP_ALIVE = "$KeepAlive"
$env:OLLAMA_CONTEXT_LENGTH = "$ContextLength"
$env:OLLAMA_NUM_PARALLEL = "$NumParallel"
Write-Host "OLLAMA_MAX_LOADED_MODELS=$env:OLLAMA_MAX_LOADED_MODELS"
Write-Host "OLLAMA_KEEP_ALIVE=$env:OLLAMA_KEEP_ALIVE"
Write-Host "OLLAMA_CONTEXT_LENGTH=$env:OLLAMA_CONTEXT_LENGTH"
Write-Host "OLLAMA_NUM_PARALLEL=$env:OLLAMA_NUM_PARALLEL"

if ($SkipStart) {
    Write-Host ""
    Write-Host "Cleanup done. Stack left OFF by request (-SkipStart)."
    exit 0
}

Write-Step "starting gateway stack"
ngram gateway on
ngram gateway status

Write-Step "warming gemma4:26b with a tiny local request"
$url = "http://127.0.0.1:11434/v1/chat/completions"
$payload = @{
    model = "gemma4:26b"
    messages = @(@{ role = "user"; content = "reply with exactly ok" })
    temperature = 0
    max_tokens = 16
    stream = $false
} | ConvertTo-Json -Depth 6
$sw = [System.Diagnostics.Stopwatch]::StartNew()
try {
    $resp = Invoke-WebRequest -Uri $url -Method Post -ContentType "application/json" -Body $payload -TimeoutSec 120 -UseBasicParsing
    $code = [int]$resp.StatusCode
} catch {
    if ($_.Exception.Response) {
        $code = [int]$_.Exception.Response.StatusCode.value__
    } else {
        $code = -1
    }
}
$sw.Stop()
Write-Host "warmup_status=$code ms=$([math]::Round($sw.Elapsed.TotalMilliseconds,1))"

Write-Host ""
Write-Host "Post-start checks"
Write-Host "-----------------"
ollama ps
Show-GpuSummary
