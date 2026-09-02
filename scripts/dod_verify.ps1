$ErrorActionPreference = "Continue"
$repo = "C:\Users\Rex\.config\opencode\harness_benchmark"
$py = "C:\Users\Rex\.config\opencode\python-runtime\python-3.14.7\python.exe"
$zip = Join-Path $repo "relay\harness_benchmark-v1.3.0-20260902.zip"

$results = @()

function Pass($name) { $script:results += [pscustomobject]@{name=$name; status="PASS"} }
function Fail($name, $detail) { $script:results += [pscustomobject]@{name=$name; status="FAIL: $detail"} }

# 1. 388 tests (v1.3.0: 318 + 70 new)
Write-Output ">>> pytest tests/ -q"
$env:PYTHONPATH = "src"
$out = & $py -m pytest tests/ -q 2>&1 | Out-String
if ($out -match "(\d+) passed") {
    $n = [int]$Matches[1]
    if ($n -ge 388) { Pass "pytest: $n passed (>=388)" } else { Fail "pytest: >=388 passed" "got $n" }
} else {
    Fail "pytest: parse failed" $out.Substring(0, [Math]::Min(200, $out.Length))
}

# 2. Invariants
Write-Output ">>> scripts/invariants_check.ps1"
$out = & powershell -ExecutionPolicy Bypass -File (Join-Path $repo "scripts\invariants_check.ps1") 2>&1 | Out-String
if ($out -match "All invariants pass") { Pass "invariants: all pass" } else { Fail "invariants" $out.Substring(0, [Math]::Min(400, $out.Length)) }

# 3. Scorer self-score
Write-Output ">>> scorer self-score"
$env:PYTHONPATH = "src"
$out = & $py -c "from dhc.scoring.scorer import make_report, write_report, ModuleScore; r = make_report([ModuleScore(f'c{i}', 100.0, 100.0) for i in range(1, 11)]); write_report(r, 'dhc-v-report.json'); print(r.dhc_v)" 2>&1 | Out-String
if ($out.Trim() -eq "100.0") { Pass "scorer: DHC-V=100.0" } else { Fail "scorer" $out.Trim() }

# 4. v1.2.0 live smoke (19 checks)
Write-Output ">>> v1.2.0 chat smoke"
$env:PYTHONPATH = "src"
$out = & $py "C:\Users\Rex\.config\opencode\harness_benchmark\tests\chat\smoke_v12.py" 2>&1 | Out-String
if ($out -match "Pass:\s*(\d+)\s*Fail:\s*(\d+)") {
    $p = [int]$Matches[1]
    $f = [int]$Matches[2]
    if ($p -ge 19 -and $f -eq 0) { Pass "v1.2.0 chat smoke: $p/19 passed" } else { Fail "v1.2.0 chat smoke" "Pass=$p Fail=$f" }
} else {
    Fail "v1.2.0 chat smoke: parse failed" $out.Substring(0, [Math]::Min(300, $out.Length))
}

# 5. Zip contents
Write-Output ">>> zip contents"
if (-not (Test-Path -LiteralPath $zip)) {
    Fail "zip exists" "no $zip"
    # Skip all remaining zip-related checks.
    $zip_present = $false
} else {
    $zip_present = $true
}
if ($zip_present) {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $z = [System.IO.Compression.ZipFile]::OpenRead($zip)
    $names = $z.Entries.FullName
    $z.Dispose()

    # 5a. No runtime artifacts
    $bad = $names | Where-Object { $_ -match "(^|\\)serve_c1\.(log|err\.log|port|token)($|\\)|(^|\\)heartbeat.*\.(log|err\.log)($|\\)|(^|\\)demo_live\.(log|err\.log)($|\\)|(^|\\)vite\.(log|err\.log)($|\\)|__pycache__|\.pytest_cache" }
    if ($bad) { Fail "zip: no runtime artifacts" ($bad -join ", ") } else { Pass "zip: no runtime artifacts" }

    # 5b. All 22 doc files present (v1.2.0 adds 3 chat docs + 2 ADRs;
    #     v1.2.1 adds 1 ADR; v1.3.0 adds 2 docs + 3 ADRs)
    $want = @(
        "docs/README.md",
        "docs/architecture.md",
        "docs/security-model.md",
        "docs/plugin-authoring.md",
        "docs/SHA-PINNING.md",
        "docs/chat-architecture.md",
        "docs/session-storage.md",
        "docs/secrets-model.md",
        "docs/v1.3.0-technical-spec.md",
        "docs/v1.3.0-test-plan.md",
        "docs/adr/0001-three-tab-ui.md",
        "docs/adr/0002-plugin-marketplace-with-sha-integrity.md",
        "docs/adr/0003-ephemeral-port-and-bearer-token.md",
        "docs/adr/0004-chat-and-sessions.md",
        "docs/adr/0005-markdown-component.md",
        "docs/adr/0006-model-selection.md",
        "docs/adr/0007-api-key-management.md",
        "docs/adr/0008-retry-policy.md",
        "docs/adr/0009-provider-abstraction.md",
        "CHANGELOG.md",
        "CONTRIBUTING.md",
        "GLOSSARY.md",
        "relay/MANIFEST.txt"
    )
    $miss = @()
    foreach ($f in $want) {
        $hit = $names | Where-Object { $_ -eq $f -or $_ -replace "\\","/" -eq $f }
        if (-not $hit) { $miss += $f }
    }
    if ($miss) { Fail "zip: all 23 doc files" ("missing: " + ($miss -join ", ")) } else { Pass "zip: all 23 doc files present" }

    # 5c. README in zip is v1.3.0
    $z = [System.IO.Compression.ZipFile]::OpenRead($zip)
    $readmeEntry = $z.Entries | Where-Object { $_.FullName -eq "README.md" -or $_.FullName -replace "\\","/" -eq "README.md" } | Select-Object -First 1
    if ($readmeEntry) {
        $reader = New-Object System.IO.StreamReader($readmeEntry.Open())
        $readme = $reader.ReadToEnd()
        $reader.Close()
        if ($readme -match "v1\.3\.0") { Pass "zip: README is v1.3.0" } else { Fail "zip: README is v1.3.0" "banner not v1.3.0" }
    } else {
        Fail "zip: README present" "no README.md in zip"
    }
    $z.Dispose()
}

# 6. SHA-PINNING.md matches the 5 manifests
Write-Output ">>> SHA pinning consistency"
$pinning = Get-Content -LiteralPath (Join-Path $repo "docs\SHA-PINNING.md") -Raw
$ok = $true
foreach ($plugin_id in @("rate_limiter_v1","session_exporter_v1","model_router_v1","memory_store_v1","prompt_browser_v1")) {
    $m = Get-Content -LiteralPath (Join-Path $repo "src\dhc\plugins\$plugin_id\manifest.json") -Raw
    $sm = [regex]::Match($m, '"sha256":\s*"([a-f0-9]{64})"')
    if (-not $sm.Success) { $ok = $false; break }
    $manifest_sha = $sm.Groups[1].Value
    $rowPattern = ('\|\s*`' + $plugin_id + '`\s*\|\s*`([a-f0-9]{64})`\s*\|')
    $rm = [regex]::Match($pinning, $rowPattern)
    if (-not $rm.Success) { $ok = $false; break }
    if ($rm.Groups[1].Value -ne $manifest_sha) { $ok = $false; break }
}
if ($ok) { Pass "SHA pinning: all 5 docs/manifests in sync" } else { Fail "SHA pinning consistency" "see script" }

# 7. Source files in zip
if ($zip_present) {
    $z = [System.IO.Compression.ZipFile]::OpenRead($zip)
    $srcFiles = $z.Entries | Where-Object { $_.FullName -like "src/dhc/plugins/*/manifest.json" -or $_.FullName -like "src\dhc\plugins\*\manifest.json" }
    $z.Dispose()
    if ($srcFiles.Count -eq 5) { Pass "zip: 5 plugin manifests" } else { Fail "zip: 5 plugin manifests" "got $($srcFiles.Count)" }
}

# 8. Root layout: no staging dir prefix
if ($zip_present) {
    $z = [System.IO.Compression.ZipFile]::OpenRead($zip)
    $hasStaging = $z.Entries | Where-Object { $_.FullName -match "(^|\\)harness_benchmark[\\/]" }
    $z.Dispose()
    if ($hasStaging) { Fail "zip: no staging prefix" ($hasStaging[0].FullName) } else { Pass "zip: no staging prefix" }
}

# 9. v1.2.x + v1.3.0 source files in zip
if ($zip_present) {
    $z = [System.IO.Compression.ZipFile]::OpenRead($zip)
    $names = $z.Entries.FullName
    $z.Dispose()
    $want120 = @(
        "src/dhc/cordis/secrets.py",
        "src/dhc/services/session_manager.py",
        "src/dhc/services/model_registry.py",
        "src/dhc/integrations/base.py",
        "src/dhc/integrations/openai_client.py",
        "src/dhc/integrations/anthropic_client.py",
        "src/dhc/integrations/openrouter_client.py",
        "tests/fixtures/mock_llm.py",
        "apps/web/src/panels/ChatPanel.tsx",
        "apps/web/src/panels/SessionList.tsx",
        "apps/web/src/components/Markdown.tsx",
        "apps/web/src/components/ModelSelect.tsx",
        "apps/web/src/components/SearchOverlay.tsx",
        "apps/web/src/types/chat.ts"
    )
    $miss120 = @()
    foreach ($f in $want120) {
        $hit = $names | Where-Object { $_ -eq $f -or $_ -replace "\\","/" -eq $f }
        if (-not $hit) { $miss120 += $f }
    }
    if ($miss120) { Fail "zip: v1.2.x+v1.3.0 source files" ("missing: " + ($miss120 -join ", ")) } else { Pass "zip: all 14 v1.2.x+v1.3.0 source files present" }
}

# Summary
Write-Output ""
Write-Output "============================================"
$results | Format-Table -AutoSize | Out-String | Write-Output
$pass = ($results | Where-Object { $_.status -eq "PASS" }).Count
$fail = ($results | Where-Object { $_.status -like "FAIL*" }).Count
Write-Output "Pass: $pass  Fail: $fail"
if ($fail -gt 0) { exit 1 } else { exit 0 }
