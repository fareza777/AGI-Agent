# Safe Engram autostart after Windows logon. Runs once, no loop.
$ErrorActionPreference = "Stop"

$Root = Split-Path $PSScriptRoot -Parent
$StartScript = Join-Path $PSScriptRoot "start-engram-windows.ps1"
$LogFile = Join-Path $Root "autostart.log"
$StartupDelaySec = 60

function Write-AutoLog {
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
    if (-not (Test-Path $StartScript)) {
        Write-AutoLog "ERROR: start script missing: $StartScript"
        exit 1
    }

    Write-AutoLog "Waiting ${StartupDelaySec}s after logon"
    Start-Sleep -Seconds $StartupDelaySec

    if (Test-EngramRunning) {
        Write-AutoLog "SKIP: Engram already running"
        exit 0
    }

    Write-AutoLog "Invoking safe start script"
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $StartScript | Out-File -FilePath $LogFile -Append -Encoding UTF8

    if (Test-EngramRunning) {
        Write-AutoLog "SUCCESS: Engram Telegram bot is running"
        exit 0
    }

    Write-AutoLog "WARN: Start finished but process check failed; see engram_err.log"
    exit 1
} catch {
    Write-AutoLog "ERROR: $($_.Exception.Message)"
    exit 1
}