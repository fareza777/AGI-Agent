@echo off
setlocal
cd /d "%~dp0"
set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"

echo [Engram] Stopping any existing bot instances...
powershell -NoProfile -Command ^
  "$root = '%ROOT%'; " ^
  "$procs = @(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\"); " ^
  "$targets = New-Object System.Collections.Generic.List[object]; " ^
  "foreach ($p in $procs) { " ^
  "  $cmd = $p.CommandLine; " ^
  "  if ($cmd -notlike '*run.py*') { continue }; " ^
  "  if ($cmd -like '*Engram-Agent*' -or $cmd -like '*Engram AI Agents*') { [void]$targets.Add($p); continue }; " ^
  "  if ($cmd -like '*\.venv\Scripts\python.exe*') { " ^
  "    $par = $procs | Where-Object { $_.ProcessId -eq $p.ParentProcessId } | Select-Object -First 1; " ^
  "    if ($par -and ($par.CommandLine -like ('*' + $root + '*') -or $par.CommandLine -like '*start_engram*')) { [void]$targets.Add($p) } " ^
  "  } " ^
  "}; " ^
  "$ids = @($targets | ForEach-Object { $_.ProcessId }); " ^
  "$children = @($procs | Where-Object { $ids -contains $_.ParentProcessId }); " ^
  "$all = @($targets + $children) | Sort-Object ProcessId -Unique; " ^
  "foreach ($p in $all) { Write-Host ('  stop PID ' + $p.ProcessId + ': ' + $p.CommandLine); Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }; " ^
  "Start-Sleep -Seconds 2"

if exist engram.pid del /f engram.pid 2>nul

echo [Engram] Starting single instance (venv)...
"%~dp0.venv\Scripts\python.exe" run.py

endlocal
