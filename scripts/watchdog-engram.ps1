# Engram watchdog: start only when process is down. No 60s logon delay.
$ErrorActionPreference = "Stop"

$Root = Split-Path $PSScriptRoot -Parent
$StartScript = Join-Path $PSScriptRoot "start-engram-windows.ps1"
$LogFile = Join-Path $Root "watchdog.log"

function Write-WatchLog {
    param([string]$Message)
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
}

function Test-EngramRunning {
    $pidFile = Join-Path $Root "engram.pid"
    if (Test-Path $pidFile) {
        try {
            $pidVal = [int](Get-Content $pidFile -Raw).Trim()
            if (Get-Process -Id $pidVal -ErrorAction SilentlyContinue) { return $true }
        } catch {}
    }

    $processes = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object {
            $cmd = $_.CommandLine
            -not [string]::IsNullOrWhiteSpace($cmd) -and
            $cmd -like '*run.py*' -and (
                $cmd -like '*Engram-Agent*' -or
                $cmd -like '*engram-ai-agents*'
            )
        }
    return [bool]$processes
}

try {
    if (Test-EngramRunning) {
        exit 0
    }

    Write-WatchLog "Engram down - invoking safe start"
    & powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File $StartScript | Out-Null

    if (Test-EngramRunning) {
        Write-WatchLog "SUCCESS: Engram recovered"
        exit 0
    }

    Write-WatchLog "WARN: Start finished but process check failed"
    exit 1
} catch {
    Write-WatchLog "ERROR: $($_.Exception.Message)"
    exit 1
}