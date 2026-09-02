$ErrorActionPreference = "Stop"
$repo = "C:\Users\Rex\.config\opencode\harness_benchmark"
$py = "C:\Users\Rex\.config\opencode\python-runtime\python-3.14.7\python.exe"
$llmBase = $env:DHC_MOCK_LLM_URL
if (-not $llmBase) { $llmBase = "http://127.0.0.1:3099" }

# Wipe runtime artifacts.
Get-Process python -ErrorAction SilentlyContinue | Where-Object {
    $_.Path -eq $py -or $_.MainWindowTitle -like "*mock_llm*" -or $_.CommandLine -like "*serve_c1*"
} | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 1

Remove-Item -LiteralPath (Join-Path $repo "serve_c1.port") -ErrorAction SilentlyContinue -Force
Remove-Item -LiteralPath (Join-Path $repo "serve_c1.token") -ErrorAction SilentlyContinue -Force
Remove-Item -LiteralPath (Join-Path $repo "mock_llm.port") -ErrorAction SilentlyContinue -Force

$env:PYTHONPATH = "src"
$env:PYTHONIOENCODING = "utf-8"

$proc = Start-Process -FilePath $py `
  -ArgumentList @(
    "-m", "dhc.serve_c1",
    "--static-dir", "apps/web/dist",
    "--llm-base-url", $llmBase,
    "--llm-api-key", "sk-mock-1234567890"
  ) `
  -WorkingDirectory $repo `
  -RedirectStandardOutput (Join-Path $repo "serve_c1.log") `
  -RedirectStandardError (Join-Path $repo "serve_c1.err.log") `
  -WindowStyle Hidden `
  -PassThru

Write-Output "Started C1 PID $($proc.Id) (LLM=$llmBase)"
Start-Sleep -Seconds 4

$logLines = Get-Content -LiteralPath (Join-Path $repo "serve_c1.log") -ErrorAction SilentlyContinue | Select-Object -First 14
Write-Output "--- serve_c1.log ---"
$logLines | ForEach-Object { Write-Output $_ }

$portFile = Join-Path $repo "serve_c1.port"
$tokenFile = Join-Path $repo "serve_c1.token"
if (Test-Path -LiteralPath $portFile) {
    $port = (Get-Content -LiteralPath $portFile).Trim()
    Write-Output ""
    Write-Output "PORT: $port"
}
if (Test-Path -LiteralPath $tokenFile) {
    $token = (Get-Content -LiteralPath $tokenFile).Trim()
    Write-Output ("TOKEN: {0}...{1}  (len={2})" -f $token.Substring(0, 8), $token.Substring($token.Length - 4), $token.Length)
}
