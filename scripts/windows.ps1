param(
    [ValidateSet('start', 'stop', 'status')][string]$Action = 'start',
    [ValidateRange(1, 65535)][int]$ApiPort = 8000,
    [ValidateRange(1, 65535)][int]$WebPort = 5173
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$runtimeDir = Join-Path $projectRoot 'work\windows-main'
$statePath = Join-Path $runtimeDir 'process.json'
$pythonPath = Join-Path $projectRoot 'backend\.venv\Scripts\python.exe'
$devPath = Join-Path $PSScriptRoot 'dev.py'

function Get-ManagedProcess {
    if (!(Test-Path -LiteralPath $statePath)) { return $null }
    $state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
    $process = Get-Process -Id $state.pid -ErrorAction SilentlyContinue
    if (!$process) { return $null }
    $details = Get-CimInstance Win32_Process -Filter "ProcessId = $($state.pid)"
    if ($process.StartTime.ToUniversalTime().Ticks.ToString() -ne $state.started -or
        $details.ExecutablePath -ne $pythonPath -or
        !$details.CommandLine.Contains($devPath)) {
        throw 'Saved PID no longer belongs to this launcher. No process was stopped.'
    }
    return $process
}

$managed = Get-ManagedProcess
if ($Action -eq 'stop') {
    if ($managed) {
        # Only this verified supervisor and its children; never kill by port or image name.
        & taskkill.exe /PID $managed.Id /T /F
        if ($LASTEXITCODE -ne 0) { throw 'Could not stop the managed process tree.' }
    }
    if (Test-Path -LiteralPath $statePath) { Remove-Item -LiteralPath $statePath }
    Write-Host 'CampusInbox launcher stopped. QQ bridge and Ollama are managed separately.'
    exit 0
}
if ($managed) {
    $state = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
    Write-Host "CampusInbox supervisor PID $($managed.Id): $($state.url)"
    exit 0
}
if ($Action -eq 'status') { Write-Host 'CampusInbox launcher is not running.'; exit 0 }
if (!(Test-Path -LiteralPath $pythonPath)) { throw 'Run scripts/setup.py first.' }
if ($ApiPort -eq $WebPort) { throw 'API and frontend ports must differ.' }
foreach ($port in @($ApiPort, $WebPort)) {
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $port)
    try { $listener.Start() }
    catch { throw "127.0.0.1:$port is occupied. Choose another port; no process was stopped." }
    finally { $listener.Stop() }
}
New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
$process = Start-Process -FilePath $pythonPath -WorkingDirectory $projectRoot -WindowStyle Hidden `
    -ArgumentList @(('"' + $devPath + '"'), '--no-reload', '--api-port', $ApiPort, '--web-port', $WebPort) `
    -RedirectStandardOutput (Join-Path $runtimeDir 'stdout.log') `
    -RedirectStandardError (Join-Path $runtimeDir 'stderr.log') -PassThru
@{ pid = $process.Id; started = $process.StartTime.ToUniversalTime().Ticks.ToString();
   url = "http://127.0.0.1:$WebPort" } | ConvertTo-Json | Set-Content -LiteralPath $statePath
try {
    $ready = $false
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        if ($process.HasExited) { throw 'Supervisor exited. Check work/windows-main/stderr.log.' }
        try {
            $health = Invoke-RestMethod "http://127.0.0.1:$WebPort/api/health" -TimeoutSec 2
            $bridge = Invoke-RestMethod "http://127.0.0.1:$WebPort/api/connectors/qq/onebot" -TimeoutSec 2
            if ($health.service -eq 'campus-inbox' -and $bridge.credential_supported) {
                $ready = $true
                break
            }
        } catch { Start-Sleep -Milliseconds 500 }
    }
    if (!$ready) { throw 'Startup health check failed. Check work/windows-main/stderr.log.' }
    Write-Host "CampusInbox ready: http://127.0.0.1:$WebPort"
    Write-Host "API docs: http://127.0.0.1:$ApiPort/docs"
    Write-Host 'Stop: powershell -File scripts/windows.ps1 stop'
} catch {
    $managed = Get-ManagedProcess
    if ($managed) { & taskkill.exe /PID $managed.Id /T /F | Out-Null }
    Remove-Item -LiteralPath $statePath -ErrorAction SilentlyContinue
    throw
}
