$ErrorActionPreference = "Stop"
$repo = "C:\Users\Rex\.config\opencode\harness_benchmark"
$py = "C:\Users\Rex\.config\opencode\python-runtime\python-3.14.7\python.exe"

# Find an open port (just use 3099 by default; if it's taken we'll
# discover in tests).
$port = 3099

# Wipe any stale state.
Remove-Item -LiteralPath (Join-Path $repo "mock_llm.log") -ErrorAction SilentlyContinue -Force
Remove-Item -LiteralPath (Join-Path $repo "mock_llm.err.log") -ErrorAction SilentlyContinue -Force

$env:PYTHONPATH = "src"
$env:PYTHONIOENCODING = "utf-8"

$proc = Start-Process -FilePath $py `
  -ArgumentList @("-m", "tests.fixtures.mock_llm", "--host", "127.0.0.1", "--port", "$port") `
  -WorkingDirectory $repo `
  -RedirectStandardOutput (Join-Path $repo "mock_llm.log") `
  -RedirectStandardError (Join-Path $repo "mock_llm.err.log") `
  -WindowStyle Hidden `
  -PassThru

Write-Output "mock_llm PID $($proc.Id), port $port"
Start-Sleep -Seconds 2

# Probe the health endpoint.
try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:${port}/healthz" -UseBasicParsing -TimeoutSec 5
    Write-Output "GET /healthz -> $($r.StatusCode)"
    Write-Output "Body: $($r.Content)"
} catch {
    Write-Output "ERROR: mock LLM not responding: $_"
    exit 2
}
