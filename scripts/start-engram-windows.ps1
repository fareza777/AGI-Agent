# Safe manual/background start for Engram Agent on Windows.
$ErrorActionPreference = "Stop"

$Root = Split-Path $PSScriptRoot -Parent
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
$RunScript = Join-Path $Root "run.py"
$PyvenvCfg = Join-Path $Root ".venv\pyvenv.cfg"

function Get-EngramPython {
    if (Test-Path $PyvenvCfg) {
        $executable = Get-Content $PyvenvCfg | Where-Object { $_ -match '^\s*executable\s*=\s*(.+)$' } | ForEach-Object {
            $matches[1].Trim()
        } | Select-Object -First 1
        if ($executable -and (Test-Path $executable)) {
            return $executable
        }
    }
    if (Test-Path $VenvPython) { return $VenvPython }
    return $null
}

$Python = Get-EngramPython
$PidFile = Join-Path $Root "engram.pid"
$AppLockFile = Join-Path $Root "engram.lock"
$LockFile = Join-Path $Root ".engram.starting.lock"
$LogFile = Join-Path $Root "service.log"
$OutLog = Join-Path $Root "engram_out.log"
$ErrLog = Join-Path $Root "engram_err.log"

function Test-PidAlive {
    param([int]$ProcessId)
    if ($ProcessId -le 0) { return $false }
    $p = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    return [bool]$p
}

function Test-EngramProcessRunning {
    if (Test-Path $PidFile) {
        try {
            $pidVal = [int](Get-Content $PidFile -Raw).Trim()
            if (Test-PidAlive $pidVal) { return $true }
        } catch {}
    }

    $processes = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object {
            $cmd = $_.CommandLine
            if ([string]::IsNullOrWhiteSpace($cmd)) { return $false }
            $cmd -like '*run.py*' -and (
                $cmd -like '*engram-ai-agents*' -or
                $cmd -like '*Engram-Agent*'
            )
        }
    return [bool]$processes
}

function Test-EngramHealthy {
    if (-not (Test-EngramProcessRunning)) { return $false }
    if (-not (Test-Path $ErrLog)) { return $true }
    $tail = Get-Content $ErrLog -Tail 5 -ErrorAction SilentlyContinue
    if ($tail -match 'connected as @') { return $true }
    return $true
}

if (-not (Test-Path $Python)) {
    Write-Error "Python venv not found: $Python"
    exit 1
}

if (-not (Test-Path $RunScript)) {
    Write-Error "run.py not found: $RunScript"
    exit 1
}

if (Test-Path $LockFile) {
    $lockAge = (Get-Date) - (Get-Item $LockFile).LastWriteTime
    if ($lockAge.TotalMinutes -lt 10) {
        Write-Host "Engram start already in progress (lock file, age $([int]$lockAge.TotalSeconds)s)"
        exit 0
    }
    Remove-Item $LockFile -Force -ErrorAction SilentlyContinue
}

if (Test-EngramProcessRunning) {
    Write-Host "Engram already running (existing python process)"
    exit 0
}

if (Test-Path $PidFile) {
    try {
        $stalePid = [int](Get-Content $PidFile -Raw).Trim()
        if (-not (Test-PidAlive $stalePid)) {
            Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
            if (Test-Path $AppLockFile) { Remove-Item $AppLockFile -Force -ErrorAction SilentlyContinue }
        }
    } catch {
        Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    }
}

Set-Content -Path $LockFile -Value (Get-Date -Format "o") -Encoding UTF8

try {
    # Hidden PowerShell parent keeps python alive (direct Start-Process python exits quickly).
    $escapedRoot = $Root.Replace("'", "''")
    $escapedPython = $Python.Replace("'", "''")
    $escapedRunScript = $RunScript.Replace("'", "''")
    $command = "Set-Location -LiteralPath '$escapedRoot'; & '$escapedPython' '$escapedRunScript'"

    $launch = Start-Process `
        -FilePath "powershell.exe" `
        -ArgumentList @("-NoProfile", "-WindowStyle", "Hidden", "-Command", $command) `
        -WorkingDirectory $Root `
        -WindowStyle Hidden `
        -PassThru

    Add-Content -Path $LogFile -Value "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] Launch requested via hidden PowerShell PID $($launch.Id)" -Encoding UTF8

    $healthy = $false
    for ($i = 1; $i -le 24; $i++) {
        Start-Sleep -Seconds 5
        if (Test-EngramProcessRunning) {
            $healthy = $true
            break
        }
    }

    if ($healthy) {
        if (Test-Path $PidFile) {
            Write-Host "Engram started (PID $(Get-Content $PidFile -Raw))"
        } else {
            Write-Host "Engram started (launcher PID $($launch.Id))"
        }
        exit 0
    }

    Write-Host "Engram start requested but process check failed. See $ErrLog"
    exit 1
} finally {
    if (Test-Path $LockFile) {
        Remove-Item $LockFile -Force -ErrorAction SilentlyContinue
    }
}