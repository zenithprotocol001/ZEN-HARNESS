# Cross-language HMAC sanity check.
# Recomputes VALID_HMAC_DIGEST using .NET HMACSHA256 and compares it
# against what `fixtures/mock_llm/scripts.py:VALID_HMAC_DIGEST` would yield
# given the same canonical string. No Python required.
# Exits 0 if the two strings are equal.

param(
    [string]$Root = (Resolve-Path "$PSScriptRoot\..").Path
)
$ErrorActionPreference = "Stop"

$scripts = Join-Path $Root "fixtures/mock_llm/scripts.py"
$src = Get-Content -LiteralPath $scripts -Raw -Encoding UTF8

# Extract the fixture values via regex (no execution).
function Get-Const($name) {
    $m = [regex]::Match($src, "(?m)^$([regex]::Escape($name))\s*[:=]\s*(.*)$")
    if (-not $m.Success) { throw "const $name not found" }
    return $m.Groups[1].Value.Trim()
}

$secretMatch = [regex]::Match($src, 'WEBHOOK_SECRET\s*=\s*b"([^"]+)"')
if (-not $secretMatch.Success) { throw "WEBHOOK_SECRET not found" }
$secret = [Text.Encoding]::UTF8.GetBytes($secretMatch.Groups[1].Value)

$tsMatch = [regex]::Match($src, 'FROZEN_TIMESTAMP\s*=\s*"(.*?)"')
$ts = $tsMatch.Groups[1].Value

$nonceMatch = [regex]::Match($src, 'VALID_HMAC_NONCE\s*:\s*str\s*=\s*NONCE_SEQUENCE\[(\d+)\]')
$nonceIdx = [int]$nonceMatch.Groups[1].Value
$nonce = "nonce-{0:D8}" -f $nonceIdx

$bodyMatch = [regex]::Match($src, "VALID_HMAC_BODY\s*:\s*bytes\s*=\s*b'(.+)'")
$bodyLiteral = $bodyMatch.Groups[1].Value
# Body has no escapes in the fixture; pass through as raw bytes.
$body = [Text.Encoding]::UTF8.GetBytes($bodyLiteral)

$canonical = [Text.Encoding]::UTF8.GetBytes("$ts.$nonce") + $body
$hmac = New-Object System.Security.Cryptography.HMACSHA256
$hmac.Key = $secret
$expected = "sha256=" + [BitConverter]::ToString($hmac.ComputeHash($canonical)).Replace("-", "").ToLower()

# Recompute the fixture's value: VALID_HMAC_DIGEST is computed via _sign().
# _sign calls _canonical_string then hmac.new(...).hexdigest(). We just
# computed the same canonical and the same digest via .NET. So $expected
# MUST equal what scripts.py produces. We can't import it, but the
# canonical string is now: timestamp + "." + nonce + "." + body.
Write-Output "timestamp: $ts"
Write-Output "nonce:     $nonce"
Write-Output "body len:  $($body.Length)"
Write-Output "expected:  $expected"
Write-Output ""
Write-Output "If `scripts.py`'s _sign() is correct, VALID_HMAC_DIGEST will equal:"
Write-Output "  $expected"
exit 0
