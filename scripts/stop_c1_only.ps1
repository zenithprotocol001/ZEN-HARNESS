$ErrorActionPreference = "Stop"
$repo = "C:\Users\Rex\.config\opencode\harness_benchmark"
Get-Process python -ErrorAction SilentlyContinue | ForEach-Object {
    try { Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue } catch {}
}
Start-Sleep -Seconds 1
$purge = @("serve_c1.port","serve_c1.token","serve_c1.log","serve_c1.err.log")
foreach ($f in $purge) {
    $p = Join-Path $repo $f
    if (Test-Path -LiteralPath $p) { Remove-Item -LiteralPath $p -Force }
}
Get-Process python -ErrorAction SilentlyContinue
if (Test-Path -LiteralPath (Join-Path $repo "serve_c1.port")) { Write-Output "WARN: serve_c1.port still present" } else { Write-Output "OK: serve_c1.port gone" }
