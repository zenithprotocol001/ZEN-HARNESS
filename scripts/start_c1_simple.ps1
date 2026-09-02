$ErrorActionPreference = "Stop"
$repoRoot = "C:\Users\Rex\.config\opencode\harness_benchmark"
$py = "C:\Users\Rex\.config\opencode\python-runtime\python-3.14.7\python.exe"
$outLog = Join-Path $repoRoot "serve_c1.log"
$errLog = Join-Path $repoRoot "serve_c1.err.log"
Remove-Item -LiteralPath $outLog -ErrorAction SilentlyContinue -Force
Remove-Item -LiteralPath $errLog -ErrorAction SilentlyContinue -Force

Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 1

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
Start-Sleep -Seconds 6
$running = Get-Process -Id $proc.Id -ErrorAction SilentlyContinue
if ($running) {
    Write-Output "Process alive"
} else {
    Write-Output "Process exited"
}
Get-Content -LiteralPath $outLog -ErrorAction SilentlyContinue | Select-Object -First 20
Write-Output "--- ERR ---"
Get-Content -LiteralPath $errLog -ErrorAction SilentlyContinue | Select-Object -First 20
Write-Output "--- TCP ---"
Get-NetTCPConnection -LocalPort 3081 -ErrorAction SilentlyContinue | Format-Table -AutoSize
