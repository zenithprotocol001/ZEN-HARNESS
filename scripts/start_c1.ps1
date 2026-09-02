# Start C1 GuiWebCore in the background and write logs.
$ErrorActionPreference = "Stop"

$repoRoot = "C:\Users\Rex\.config\opencode\harness_benchmark"
$py = "C:\Users\Rex\.config\opencode\python-runtime\python-3.14.7\python.exe"
$outLog = Join-Path $repoRoot "serve_c1.log"
$errLog = Join-Path $repoRoot "serve_c1.err.log"
Remove-Item -LiteralPath $outLog -ErrorAction SilentlyContinue -Force
Remove-Item -LiteralPath $errLog -ErrorAction SilentlyContinue -Force

$env:PYTHONPATH = "src"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUNBUFFERED = "1"

$proc = Start-Process -FilePath $py `
  -ArgumentList @("-m", "dhc.serve_c1", "--host", "127.0.0.1", "--port", "3081") `
  -WorkingDirectory $repoRoot `
  -RedirectStandardOutput $outLog `
  -RedirectStandardError $errLog `
  -PassThru `
  -WindowStyle Hidden

Write-Output "PID: $($proc.Id)"
Start-Sleep -Seconds 5

$running = Get-Process -Id $proc.Id -ErrorAction SilentlyContinue
if ($running) {
    Write-Output "Process alive: PID $($running.Id) ($($running.ProcessName))"
    Write-Output "StartTime: $($running.StartTime)"
} else {
    Write-Output "Process exited."
    if (Test-Path -LiteralPath $errLog) {
        Write-Output "--- stderr ---"
        Get-Content -LiteralPath $errLog -ErrorAction SilentlyContinue | Select-Object -First 40
    }
    if (Test-Path -LiteralPath $outLog) {
        Write-Output "--- stdout ---"
        Get-Content -LiteralPath $outLog -ErrorAction SilentlyContinue | Select-Object -First 40
    }
    exit 1
}

Write-Output ""
Write-Output "--- TCP listeners on port 3081 ---"
Get-NetTCPConnection -LocalPort 3081 -ErrorAction SilentlyContinue | Format-Table -AutoSize

Write-Output "--- stdout so far ---"
if (Test-Path -LiteralPath $outLog) {
    Get-Content -LiteralPath $outLog | Select-Object -First 20
}

Write-Output ""
Write-Output "--- HTTP probe /healthz ---"
try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:3081/healthz" -Method GET -TimeoutSec 3 -UseBasicParsing
    Write-Output "STATUS: $($r.StatusCode)"
    Write-Output "BODY:   $($r.Content)"
    Write-Output "CSP:    $($r.Headers['Content-Security-Policy'])"
    Write-Output "X-CTO:  $($r.Headers['X-Content-Type-Options'])"
    Write-Output "RP:     $($r.Headers['Referrer-Policy'])"
    Write-Output "X-FO:   $($r.Headers['X-Frame-Options'])"
} catch {
    Write-Output "PROBE FAILED: $($_.Exception.Message)"
    exit 2
}
