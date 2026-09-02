$ErrorActionPreference = "Stop"
$repo = "C:\Users\Rex\.config\opencode\harness_benchmark"

Get-Process python -ErrorAction SilentlyContinue | ForEach-Object {
    try { Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue } catch {}
}
Start-Sleep -Seconds 1

$purge = @(
    "serve_c1.port","serve_c1.token","serve_c1.log","serve_c1.err.log",
    "demo_live.log","demo_live.err.log",
    "heartbeat.log","heartbeat.err.log",
    "heartbeat_demo.log","heartbeat_demo.err.log",
    "heartbeat_long.log","heartbeat_long.err.log"
)
foreach ($f in $purge) {
    $p = Join-Path $repo $f
    if (Test-Path -LiteralPath $p) {
        Remove-Item -LiteralPath $p -Force
        Write-Output "removed: $f"
    }
}

$alive = Get-Process python -ErrorAction SilentlyContinue
if ($alive) {
    Write-Output "WARN: python still alive:"
    $alive | Format-Table Id, StartTime -AutoSize | Out-String | Write-Output
} else {
    Write-Output "OK: no python processes"
}

Get-ChildItem -LiteralPath $repo -File | Where-Object { $_.Name -in $purge } | Format-Table Name, Length -AutoSize
