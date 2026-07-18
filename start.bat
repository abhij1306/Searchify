@echo off
setlocal

set "ROOT=%~dp0"

echo Clearing Searchify ports 8000 and 3000...
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$ports = 8000,3000; foreach ($port in $ports) { $listeners = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue; foreach ($listener in $listeners) { Stop-Process -Id $listener.OwningProcess -Force -ErrorAction SilentlyContinue } }; $workers = Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and ($_.CommandLine -like '*app.workers.audit_worker*' -or $_.CommandLine -like '*app.workers.site_health_worker*') }; foreach ($worker in $workers) { Stop-Process -Id $worker.ProcessId -Force -ErrorAction SilentlyContinue }"

timeout /t 2 /nobreak >nul

echo Starting Searchify backend...
start "Searchify Backend" powershell.exe -NoLogo -NoExit -ExecutionPolicy Bypass -Command "Set-Location -LiteralPath '%ROOT%backend'; uv run uvicorn app.main:app --host 127.0.0.1 --port 8000"

echo Starting Searchify audit worker...
start "Searchify Worker" powershell.exe -NoLogo -NoExit -ExecutionPolicy Bypass -Command "Set-Location -LiteralPath '%ROOT%backend'; uv run python -m app.workers.audit_worker"

echo Starting Searchify site health worker...
start "Searchify Site Health Worker" powershell.exe -NoLogo -NoExit -ExecutionPolicy Bypass -Command "Set-Location -LiteralPath '%ROOT%backend'; uv run python -m app.workers.site_health_worker"

echo Starting Searchify frontend with pnpm...
start "Searchify Frontend" powershell.exe -NoLogo -NoExit -ExecutionPolicy Bypass -Command "Set-Location -LiteralPath '%ROOT%frontend'; pnpm.cmd dev"

echo Searchify startup commands launched.
endlocal
