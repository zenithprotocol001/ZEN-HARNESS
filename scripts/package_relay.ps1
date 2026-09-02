param(
    [string]$Root = (Resolve-Path "$PSScriptRoot\..").Path,
    [string]$ZipName,
    [string]$Datum
)
$ErrorActionPreference = "Stop"

$relayDir = Join-Path $Root "relay"
if (-not (Test-Path -LiteralPath $relayDir)) {
    New-Item -ItemType Directory -Path $relayDir -Force | Out-Null
}
$zipPath = Join-Path $relayDir $ZipName
if (Test-Path -LiteralPath $zipPath) { Remove-Item -LiteralPath $zipPath -Force }

$stageRoot = "harness_benchmark"
# Always stage under $env:TEMP, never under $Root, to avoid any chance
# of robocopy or .NET zip APIs writing back into the relay folder.
$envTempAbs = (Resolve-Path $env:TEMP).Path
$stage = Join-Path $envTempAbs ("dhc_stage_" + [Guid]::NewGuid().ToString("N").Substring(0,8))
# Sanity: the stage must not be under the relay dir
$relayAbs = (Resolve-Path $relayDir).Path
if ($stage.StartsWith($relayAbs, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to stage under relay dir: $stage"
}
if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
New-Item -ItemType Directory -Path $stage -Force | Out-Null
$stageTarget = Join-Path $stage $stageRoot
New-Item -ItemType Directory -Path $stageTarget -Force | Out-Null

# Clean relay dir of any stray unpacked subdirs left over from prior runs
foreach ($sub in @("apps", "fixtures", "scripts", "src", "tests", "node_modules", ".pytest_cache", "leaderboard", "llm_outputs")) {
    $p = Join-Path $relayDir $sub
    if (Test-Path -LiteralPath $p) { Remove-Item -LiteralPath $p -Recurse -Force }
}
foreach ($f in @("pyproject.toml", "pytest.ini", "package-lock.json", "serve_c1.log", "serve_c1.err.log", "serve_c1.port", "serve_c1.token", "demo_live.log", "demo_live.err.log", "heartbeat.log", "heartbeat.err.log", "heartbeat_long.log", "heartbeat_long.err.log", "heartbeat_demo.log", "heartbeat_demo.err.log", "vite.log", "vite.err.log")) {
    $p = Join-Path $relayDir $f
    if (Test-Path -LiteralPath $p) { Remove-Item -LiteralPath $p -Force }
}

# Exclude runtime artifacts and pyc caches from the staged copy.
# /XD — directories to skip
# /XF — files to skip at the repo root only (by relative path under $Root)
$excludeDirs = @("relay", ".opencode", "node_modules", ".pytest_cache", "__pycache__", "leaderboard", "llm_outputs")
# Files to exclude only when they sit at the repo root (i.e. runtime
# artifacts). Match by full relative path from the repo root.
$excludeRootFiles = @(
    "serve_c1.log", "serve_c1.err.log", "serve_c1.port", "serve_c1.token",
    "demo_live.log", "demo_live.err.log",
    "heartbeat.log", "heartbeat.err.log",
    "heartbeat_long.log", "heartbeat_long.err.log",
    "heartbeat_demo.log", "heartbeat_demo.err.log",
    "vite.log", "vite.err.log"
)
# Files to exclude anywhere in the tree by basename (runtime artifacts
# that are not part of the source).
$excludeAnywhereFiles = @(
    "vite.log", "vite.err.log"
)
$robocopyArgs = @($Root, $stageTarget, "/E", "/NFL", "/NDL", "/NJH", "/NJS", "/NC", "/NS")
foreach ($d in $excludeDirs) { $robocopyArgs += @("/XD", $d) }
& robocopy @robocopyArgs | Out-Null

# robocopy /XF matches by basename anywhere in the tree, which is too
# broad for some names (e.g. "serve_c1" would exclude src/dhc/serve_c1.py).
# Walk the staged tree and delete the root-level runtime files explicitly.
foreach ($f in $excludeRootFiles) {
    $p = Join-Path $stageTarget $f
    if (Test-Path -LiteralPath $p) { Remove-Item -LiteralPath $p -Force }
}
# Walk anywhere in the tree for the safe-by-basename runtime files.
foreach ($f in $excludeAnywhereFiles) {
    Get-ChildItem -LiteralPath $stageTarget -Recurse -File -Filter $f -ErrorAction SilentlyContinue |
        ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force }
}

# Strip any __pycache__ directories that robocopy included.
Get-ChildItem -LiteralPath $stageTarget -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
    ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force }

# Always include relay/MANIFEST.txt as the ship manifest, even though
# the relay directory is excluded from the robocopy.
$manifestSrc = Join-Path $Root "relay\MANIFEST.txt"
$manifestDst = Join-Path $stageTarget "relay\MANIFEST.txt"
if (Test-Path -LiteralPath $manifestSrc) {
    New-Item -ItemType Directory -Path (Split-Path -LiteralPath $manifestDst) -Force | Out-Null
    Copy-Item -LiteralPath $manifestSrc -Destination $manifestDst -Force
}

# Create the zip in two stages: first build at the staging dir level,
# then rewrite entries to strip the staging dir prefix.
$tmpZip = Join-Path $env:TEMP ("dhc_prezip_" + [Guid]::NewGuid().ToString("N").Substring(0,8) + ".zip")
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::CreateFromDirectory(
    $stage, $tmpZip,
    [System.IO.Compression.CompressionLevel]::Optimal, $true, [System.Text.Encoding]::UTF8
) | Out-Null

# Copy with entry-name rewrite. .NET on Windows uses backslash in
# entry FullName despite the ZIP spec, so match both.
$in = [System.IO.Compression.ZipFile]::OpenRead($tmpZip)
$out = [System.IO.Compression.ZipFile]::Open($zipPath, [System.IO.Compression.ZipArchiveMode]::Create)
$sep = [System.IO.Path]::DirectorySeparatorChar
$altSep = "/"  # ZIP spec uses / even on Windows
$prefix1 = $sep + $stageRoot + $sep
$prefix2 = $altSep + $stageRoot + $altSep
$bare1 = $sep + $stageRoot
$bare2 = $altSep + $stageRoot
foreach ($e in $in.Entries) {
    $newName = $e.FullName
    $idx = $newName.IndexOf($prefix1, [System.StringComparison]::Ordinal)
    if ($idx -ge 0) {
        $newName = $newName.Substring($idx + $prefix1.Length)
    } else {
        $idx = $newName.IndexOf($prefix2, [System.StringComparison]::Ordinal)
        if ($idx -ge 0) {
            $newName = $newName.Substring($idx + $prefix2.Length)
        } elseif ($newName -eq $bare1 -or $newName -eq $bare2) {
            continue
        } else {
            Write-Warning "Skipping entry with unexpected prefix: $($e.FullName)"
            continue
        }
    }
    if ([string]::IsNullOrEmpty($newName)) {
        Write-Warning "Skipping entry with empty name after rewrite: $($e.FullName)"
        continue
    }
    $ne = $out.CreateEntry($newName, [System.IO.Compression.CompressionLevel]::Optimal)
    $src = $e.Open()
    $dst = $ne.Open()
    $src.CopyTo($dst)
    $src.Close()
    $dst.Close()
}
$in.Dispose()
$out.Dispose()
Remove-Item -LiteralPath $tmpZip -Force
Remove-Item -LiteralPath $stage -Recurse -Force
Get-ChildItem -LiteralPath $relayDir | Select-Object Name, Length
Write-Output "DATUM: $Datum"
Write-Output "PREFIX: $stageRoot/"
