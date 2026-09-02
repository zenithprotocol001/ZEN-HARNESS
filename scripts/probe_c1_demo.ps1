$port = (Get-Content -LiteralPath "C:\Users\Rex\.config\opencode\harness_benchmark\serve_c1.port").Trim()
$token = (Get-Content -LiteralPath "C:\Users\Rex\.config\opencode\harness_benchmark\serve_c1.token").Trim()

Write-Output "=================================================="
Write-Output " C1 GuiWebCore - Endpoint Probe"
Write-Output "   bound on 127.0.0.1:" + $port
Write-Output "=================================================="

$paths = @("/healthz", "/", "/assets/index-BHIR0INK.js", "/nonexistent.css")
foreach ($p in $paths) {
    try {
        $r = Invoke-WebRequest -Uri ("http://127.0.0.1:" + $port + $p) -Method GET -TimeoutSec 3 -UseBasicParsing
        $line = "  " + $p.PadRight(40) + " -> " + $r.StatusCode.ToString().PadLeft(3) + "  " + $r.Headers["Content-Type"].PadRight(14) + " " + $r.Content.Length.ToString() + " bytes"
        Write-Output $line
    } catch {
        $msg = $_.Exception.Message
        if ($msg.Length -gt 60) { $msg = $msg.Substring(0, 60) }
        Write-Output ("  " + $p.PadRight(40) + " -> ERR  " + $msg)
    }
}

Write-Output ""
Write-Output "--- Security headers on /healthz ---"
$r = Invoke-WebRequest -Uri ("http://127.0.0.1:" + $port + "/healthz") -UseBasicParsing -TimeoutSec 3
$hdrs = @("Content-Security-Policy", "X-Content-Type-Options", "X-Frame-Options", "Referrer-Policy", "Permissions-Policy")
foreach ($h in $hdrs) {
    $v = $r.Headers[$h]
    if ($v) {
        if ($v.Length -gt 70) { $v = $v.Substring(0, 70) + "..." }
        Write-Output ("  " + $h.PadRight(22) + " " + $v)
    } else {
        Write-Output ("  " + $h.PadRight(22) + " (MISSING)")
    }
}

Write-Output ""
Write-Output "--- Token embedded in served index.html? ---"
$r = Invoke-WebRequest -Uri ("http://127.0.0.1:" + $port + "/") -UseBasicParsing -TimeoutSec 3
$html = $r.Content
$pattern = 'name="dhc-token"\s+content="([^"]+)"'
$match = [regex]::Match($html, $pattern)
if ($match.Success) {
    $embedded = $match.Groups[1].Value
    $matches_local = ($embedded -eq $token)
    Write-Output ("  embedded: " + $embedded.Substring(0, 8) + "..." + $embedded.Substring($embedded.Length - 4) + "  (len=" + $embedded.Length + ")")
    Write-Output ("  matches file token: " + $matches_local)
} else {
    Write-Output "  ERROR: no dhc-token meta tag in served HTML"
}

function Test-WS {
    param([string]$Origin, [string]$Token, [string]$Label)
    $headers = @{
        "Origin" = $Origin
        "Upgrade" = "websocket"
        "Connection" = "Upgrade"
        "Sec-WebSocket-Version" = "13"
        "Sec-WebSocket-Key" = "dGhlIHNhbXBsZSBub25jZQ=="
    }
    if ($Token) { $headers["Authorization"] = "Bearer " + $Token }
    try {
        $r = Invoke-WebRequest -Uri ("ws://127.0.0.1:" + $port + "/ws") -Method GET -Headers $headers -TimeoutSec 3 -UseBasicParsing
        Write-Output ("  " + $Label + " ERROR: should have been rejected")
    } catch {
        $msg = $_.Exception.Message
        if ($msg -match "101") {
            Write-Output ("  " + $Label + " WS UPGRADE 101 (accepted)")
        } elseif ($msg -match "403") {
            Write-Output ("  " + $Label + " REJECTED 403 (origin guard)")
        } elseif ($msg -match "401") {
            Write-Output ("  " + $Label + " REJECTED 401 (token auth)")
        } else {
            if ($msg.Length -gt 80) { $msg = $msg.Substring(0, 80) }
            Write-Output ("  " + $Label + " rejected: " + $msg)
        }
    }
}

Write-Output ""
Write-Output "--- WS Origin Guard matrix ---"
Test-WS -Origin ("http://127.0.0.1:" + $port) -Token $token -Label "loopback + valid token  "
Test-WS -Origin ("http://127.0.0.1:" + $port) -Token ""       -Label "loopback + NO token   "
Test-WS -Origin ("http://127.0.0.1:" + $port) -Token "wrong-tok" -Label "loopback + WRONG token"
Test-WS -Origin "https://evil.example.com" -Token $token -Label "foreign + valid token  "
Test-WS -Origin "http://10.0.0.1:8080"      -Token $token -Label "LAN + valid token      "
Test-WS -Origin "http://127.0.0.1.evil.com" -Token $token -Label "prefix attack          "
