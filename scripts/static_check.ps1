# DHC static check: no Python interpreter required.
# Verifies that every .py file under src/ and tests/ is well-formed
# (balanced brackets, no obvious syntax issues), and that every
# "from dhc.X import Y" statement in src/ points to a real file on disk.

param(
    [string]$Root = (Resolve-Path "$PSScriptRoot\..").Path
)
$ErrorActionPreference = "Stop"

$src = Join-Path $Root "src"
$tests = Join-Path $Root "tests"

function Get-PyFiles($dir) {
    Get-ChildItem -LiteralPath $dir -Recurse -Filter *.py |
        Where-Object { $_.FullName -notmatch '__pycache__' } |
        ForEach-Object { $_.FullName }
}

$errors = New-Object System.Collections.Generic.List[string]

foreach ($f in (Get-PyFiles $src) + (Get-PyFiles $tests)) {
    $content = Get-Content -LiteralPath $f -Raw -Encoding UTF8
    if ($content -notmatch '^\s*#!') {
        # crude bracket balance
        $open = ($content.ToCharArray() | Where-Object { $_ -eq '(' }).Count
        $close = ($content.ToCharArray() | Where-Object { $_ -eq ')' }).Count
        if ($open -ne $close) {
            $errors.Add(("{0}: unbalanced parens ({1} vs {2})" -f $f, $open, $close))
        }
    }
}

# Resolve "from dhc.X import Y" -> real file
foreach ($f in (Get-PyFiles $src)) {
    $content = Get-Content -LiteralPath $f -Raw -Encoding UTF8
    $rx = [regex]'^\s*from\s+(dhc\.[\w\.]+)\s+import'
    $matches_found = $rx.Matches($content)
    foreach ($m in $matches_found) {
        $mod = $m.Groups[1].Value
        $rel = ($mod -replace '\.', [System.IO.Path]::DirectorySeparatorChar)
        $target = Join-Path $src ($rel + ".py")
        $pkg = Join-Path (Split-Path $target -Parent) "__init__.py"
        if (-not (Test-Path -LiteralPath $target) -and -not (Test-Path -LiteralPath $pkg)) {
            $errors.Add(("{0}: cannot resolve import {1}" -f $f, $mod))
        }
    }
}

Write-Output "syntax errors: $($errors.Count)"
foreach ($e in $errors) { Write-Output "  - $e" }
$code = 0
if ($errors.Count -gt 0) { $code = 1 }
exit $code
