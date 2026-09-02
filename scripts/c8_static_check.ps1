# Hardens the C8 timing directive check using pure PowerShell.
# Mirrors tests/security/test_c8_timing.py without requiring pytest.

param(
    [string]$Root = (Resolve-Path "$PSScriptRoot\..").Path
)
$ErrorActionPreference = "Stop"
$src = Join-Path $Root "src\dhc\modules\c8_webhook_dispatch\service.py"

if (-not (Test-Path -LiteralPath $src)) {
    Write-Error "C8 service not found: $src"
    exit 2
}

$content = Get-Content -LiteralPath $src -Raw -Encoding UTF8

# Check 1: hmac.compare_digest is referenced.
if ($content -notmatch 'hmac\.compare_digest') {
    Write-Output "FAIL: hmac.compare_digest not found in C8 service"
    exit 1
}
Write-Output "PASS: hmac.compare_digest present"

# Check 2: no plain == used on hmac-shaped pairs.
# Heuristic: a line that uses == and references at least two of the
# HMAC-shaped tokens (broadened set: provided, expected, digest, signature,
# computed, sig, mac, hash, hmac).
$lines = $content -split "`n"
$tokens = 'provided', 'expected', 'digest', 'signature', 'computed', 'sig', 'mac', 'hash', 'hmac'
$suspicious = $lines | Where-Object {
    $line = $_
    $has_eq = $line -match '=='
    if (-not $has_eq) { return $false }
    $hits = 0
    foreach ($t in $tokens) {
        if ($line -match "\b$t") { $hits++ }
    }
    return ($hits -ge 2)
}
if ($suspicious.Count -gt 0) {
    Write-Output "FAIL: plain == on hmac-shaped names:"
    foreach ($l in $suspicious) { Write-Output "  $l" }
    exit 1
}
Write-Output "PASS: no plain == on hmac-shaped names"

# Check 3: short-secret guard exists.
if ($content -notmatch 'secret must be at least 16 bytes') {
    Write-Output "FAIL: short-secret guard not found"
    exit 1
}
Write-Output "PASS: short-secret guard present"

exit 0
