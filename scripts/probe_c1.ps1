# DHC C1 GuiWebCore smoke probe (PowerShell)
# Use after `python -m dhc.serve_c1` is running to verify the server.
# Defaults to http://127.0.0.1:3080. Override with -Host / -Port.

param(
    [string]$Host_ = "127.0.0.1",
    [int]$Port = 3080,
    [int]$TimeoutSec = 5
)

$ErrorActionPreference = "Continue"
$base = "http://$Host_`:$Port"
$results = @()

function Test-Endpoint($path, $expectCsp = $true) {
    $url = "$base$path"
    try {
        $resp = Invoke-WebRequest -Uri $url -Method GET -TimeoutSec $TimeoutSec -UseBasicParsing
        $csp = $resp.Headers["Content-Security-Policy"]
        $xcto = $resp.Headers["X-Content-Type-Options"]
        $ref = $resp.Headers["Referrer-Policy"]
        $xfo = $resp.Headers["X-Frame-Options"]
        $record = [ordered]@{
            path = $path
            status = $resp.StatusCode
            csp_present = [bool]$csp
            csp_excerpt = if ($csp) { $csp.Substring(0, [Math]::Min(80, $csp.Length)) + "..." } else { "(none)" }
            x_content_type_options = $xcto
            referrer_policy = $ref
            x_frame_options = $xfo
        }
        $global:results += $record
        return $record
    } catch {
        $global:results += [ordered]@{
            path = $path
            status = "ERROR"
            error = $_.Exception.Message
        }
        return $null
    }
}

Write-Host "Probing C1 GuiWebCore at $base" -ForegroundColor Cyan
Write-Host ""

$health = Test-Endpoint "/healthz"
$root = Test-Endpoint "/"

if ($health -and $root) {
    Write-Host "Health check:" -ForegroundColor Green
    Write-Host ("  status: {0}" -f $health.status)
    Write-Host ("  csp:    {0}" -f $health.csp_excerpt)
    Write-Host ""
    Write-Host "Root index:" -ForegroundColor Green
    Write-Host ("  status: {0}" -f $root.status)
    Write-Host ("  X-CTO:  {0}" -f $root.x_content_type_options)
    Write-Host ("  X-FO:   {0}" -f $root.x_frame_options)
    Write-Host ""
    $bad = @()
    if (-not $health.csp_present) { $bad += "CSP header missing on /healthz" }
    if (-not $root.csp_present)   { $bad += "CSP header missing on /" }
    if ($root.x_content_type_options -ne "nosniff") { $bad += "X-Content-Type-Options != nosniff" }
    if ($root.x_frame_options -ne "DENY") { $bad += "X-Frame-Options != DENY" }
    if ($bad.Count -gt 0) {
        Write-Host "FAIL: $($bad.Count) security header issue(s):" -ForegroundColor Red
        $bad | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
        exit 1
    }
    Write-Host "All security headers present and correct." -ForegroundColor Green
    exit 0
} else {
    Write-Host "C1 GuiWebCore is NOT reachable at $base" -ForegroundColor Red
    Write-Host "Start it with: python -m dhc.serve_c1 --port $Port" -ForegroundColor Yellow
    exit 2
}
