param(
    [string]$Root = (Resolve-Path "$PSScriptRoot\..").Path
)
$ErrorActionPreference = "Continue"

$failures = New-Object System.Collections.Generic.List[string]

function Assert($cond, $label) {
    if ($cond) { Write-Output "PASS: $label" }
    else { Write-Output "FAIL: $label"; $failures.Add($label) }
}

# ---- C1 ----
$c1 = Get-Content -LiteralPath (Join-Path $Root "src\dhc\modules\c1_gui_web_core/service.py") -Raw
Assert ($c1 -match "CSP_HEADER") "C1 exports CSP_HEADER"
Assert ($c1 -match "default-src 'self'") "C1 CSP has default-src 'self'"
Assert ($c1 -match "script-src 'self'") "C1 CSP has script-src 'self'"
Assert ($c1 -match "object-src 'none'") "C1 CSP has object-src 'none'"
Assert ($c1 -match "frame-ancestors 'none'") "C1 CSP has frame-ancestors 'none'"

$c1Policy = ([regex]::Match($c1, '(?s)CSP_HEADER\s*:\s*str\s*=\s*\((.+?)\)')).Groups[1].Value
$c1PolicyJoined = $c1Policy -replace "`n", " " -replace '"', ""
if ($c1PolicyJoined -notmatch "'unsafe-inline'") {
    Write-Output "PASS: C1 CSP policy string forbids 'unsafe-inline'"
} else {
    Write-Output "FAIL: C1 CSP policy string contains 'unsafe-inline'"
    $failures.Add("C1 CSP policy string contains 'unsafe-inline'")
}
if ($c1PolicyJoined -notmatch "'unsafe-eval'") {
    Write-Output "PASS: C1 CSP policy string forbids 'unsafe-eval'"
} else {
    Write-Output "FAIL: C1 CSP policy string contains 'unsafe-eval'"
    $failures.Add("C1 CSP policy string contains 'unsafe-eval'")
}
Assert ($c1 -match "class GuiWebCore") "C1 has GuiWebCore class"
Assert ($c1 -match '"/ws"') "C1 registers /ws route"
Assert ($c1 -match "_is_allowed_origin") "C1 has origin guard helper"
Assert ($c1 -match "_check_token") "C1 has bearer token comparison helper"
Assert ($c1 -match "secrets\.compare_digest") "C1 uses constant-time token comparison"
Assert ($c1 -match "secrets\.token_urlsafe") "C1 generates 256-bit token via secrets"
Assert ($c1 -match "_embed_token_in_index") "C1 embeds token in served index.html"
Assert ($c1 -match "require_token") "C1 has require_token opt-out"

$appTsx = Get-Content -LiteralPath (Join-Path $Root "apps/web/src/App.tsx") -Raw
Assert ($appTsx -match "dhc-token") "App reads dhc-token meta tag"
Assert ($appTsx -match "token=") "App sends token in WS query string"
Assert ($appTsx -match "unauthorized") "App handles 401 state"

$sanitize = Get-Content -LiteralPath (Join-Path $Root "apps\web\src\sanitize.ts") -Raw
Assert ($sanitize -match "DOMPurify") "Web sanitize uses DOMPurify"
Assert ($sanitize -match "FORBID_TAGS") "Web sanitize sets FORBID_TAGS"
Assert ($sanitize -match "script") "Web sanitize forbids script tag"

$app = Get-Content -LiteralPath (Join-Path $Root "apps\web\src/App.tsx") -Raw
Assert ($app -match "panels/ModulesPanel") "App imports ModulesPanel"
Assert ($app -match "panels/EventsPanel") "App imports EventsPanel"
Assert ($app -match "panels/PromptsPanel") "App imports PromptsPanel"
Assert ($app -match "panels/ChatPanel") "App imports ChatPanel"
Assert ($app -match 'setTab\(') "App has setTab (4-tab router)"

# v1.3.0: ModelSelect component exists and ChatPanel wires it.
$model_select = Get-Content -LiteralPath (Join-Path $Root "apps\web\src\components\ModelSelect.tsx") -Raw
Assert ($model_select -match "export function ModelSelect") "ModelSelect component is exported"
Assert ($model_select -match "/api/models") "ModelSelect fetches /api/models"
$chat_panel = Get-Content -LiteralPath (Join-Path $Root "apps\web\src\panels\ChatPanel.tsx") -Raw
Assert ($chat_panel -match "import.*ModelSelect") "ChatPanel imports ModelSelect"
Assert ($chat_panel -match 'setSessionModel|"model":') "ChatPanel PATCHes session with model"

# Positive: Markdown.tsx is the ONLY file that calls dangerouslySetInnerHTML.
# Negative: App.tsx, EventsPanel.tsx, ChatPanel.tsx, and any other
# component must NOT call dangerouslySetInnerHTML or the sanitizers
# directly. They render through the <Markdown /> and <ToolResult />
# components which live in components/Markdown.tsx.
$markdown_tsx = Get-Content -LiteralPath (Join-Path $Root "apps\web\src\components\Markdown.tsx") -Raw
Assert ($markdown_tsx -match "dangerouslySetInnerHTML") "components/Markdown.tsx is the owner of dangerouslySetInnerHTML"
Assert ($markdown_tsx -match "renderMarkdown") "components/Markdown.tsx uses renderMarkdown"
Assert ($markdown_tsx -match "renderToolResult") "components/Markdown.tsx uses renderToolResult"

# Negative invariants: HTML injection must NOT appear in App.tsx,
# EventsPanel.tsx, ChatPanel.tsx, or any other component. The
# invariant is: any of {renderMarkdown, renderToolResult,
# dangerouslySetInnerHTML} appearing in those files is a regression
# that bypasses the centralized XSS guardrail.
$forbiddenFiles = @(
    "apps\web\src\App.tsx",
    "apps\web\src\panels\EventsPanel.tsx",
    "apps\web\src\panels\ChatPanel.tsx",
    "apps\web\src\panels\ModulesPanel.tsx",
    "apps\web\src\panels\PromptsPanel.tsx",
    "apps\web\src\panels\SessionList.tsx",
    "apps\web\src\components\ModuleCard.tsx",
    "apps\web\src\components\SessionContextMenu.tsx",
    "apps\web\src\components\SearchOverlay.tsx"
)
$forbiddenSubstrings = @("renderMarkdown", "renderToolResult", "dangerouslySetInnerHTML")
foreach ($rel in $forbiddenFiles) {
    $abs = Join-Path $Root $rel
    if (-not (Test-Path -LiteralPath $abs)) { continue }
    $src = Get-Content -LiteralPath $abs -Raw
    foreach ($needle in $forbiddenSubstrings) {
        if ($src -notmatch [regex]::Escape($needle)) {
            Write-Output "PASS: $rel does NOT contain $needle"
        } else {
            Write-Output "FAIL: $rel contains $needle (must live only in components/Markdown.tsx)"
            $failures.Add("$rel contains $needle")
        }
    }
}

$events_panel = Get-Content -LiteralPath (Join-Path $Root "apps\web\src/panels/EventsPanel.tsx") -Raw
Assert ($events_panel -match "components/Markdown") "EventsPanel imports components/Markdown"

$modules_panel = Get-Content -LiteralPath (Join-Path $Root "apps\web/src/panels/ModulesPanel.tsx") -Raw
Assert ($modules_panel -match "/api/eval") "ModulesPanel posts to /api/eval"
Assert ($modules_panel -match "ModuleCard") "ModulesPanel uses ModuleCard component"

$prompts_panel = Get-Content -LiteralPath (Join-Path $Root "apps\web/src/panels/PromptsPanel.tsx") -Raw
Assert ($prompts_panel -match "/prompts") "PromptsPanel fetches /prompts"

$module_card = Get-Content -LiteralPath (Join-Path $Root "apps\web/src/components/ModuleCard.tsx") -Raw
Assert ($module_card -match "onLoad") "ModuleCard exposes onLoad"
Assert ($module_card -match "onUnload") "ModuleCard exposes onUnload"

# ---- scorer ----
$scorer = Get-Content -LiteralPath (Join-Path $Root "src\dhc\scoring\scorer.py") -Raw
Assert ($scorer -match "compute_dhc_v") "scorer has compute_dhc_v"
Assert ($scorer -match "score_functionality") "scorer has score_functionality"
Assert ($scorer -match "score_security") "scorer has score_security"
Assert ($scorer -match "make_report") "scorer has make_report"
Assert ($scorer -match "write_report") "scorer has write_report"
Assert ($scorer -match "functionality_score \* \(security_score / 100") "scorer uses multiplicative formula"
Assert ($scorer -match "security < 50") "scorer enforces security<50 hard floor"
Assert ($scorer -match "critical") "scorer handles critical findings"
Assert ($scorer -match "-100") "scorer deducts -100 for critical"
Assert ($scorer -match "-30") "scorer deducts -30 for high"
Assert ($scorer -match "-10") "scorer deducts -10 for medium"
Assert ($scorer -match "-5") "scorer deducts -5 for low"
Assert ($scorer -match "dhc-v-report.json") "scorer writes dhc-v-report.json"

$additivePattern = "0\.5 \* functionality \+ 0\.5 \* security"
if ($scorer -match $additivePattern) {
    Write-Output "FAIL: scorer contains forbidden additive formula"
    $failures.Add("scorer contains forbidden additive formula")
} else {
    Write-Output "PASS: scorer does not use additive formula"
}

# ---- cordis ----
$cord = Get-Content -LiteralPath (Join-Path $Root "src\dhc\cordis/context.py") -Raw
Assert ($cord -match 'async def dispose') "cordis Context has async dispose"
Assert ($cord -match 'reversed\(') "cordis Context disposes in reverse"
$events = Get-Content -LiteralPath (Join-Path $Root "src\dhc\cordis/events.py") -Raw
Assert ($events -match 'def off\(') "EventEmitter has off()"
Assert ($events -match 'async def waterfall') "EventEmitter has waterfall"

# ---- plugin marketplace ----
$manifest = Get-Content -LiteralPath (Join-Path $Root "src\dhc\plugins/_manifest.py") -Raw
Assert ($manifest -match "class PluginManifest") "PluginManifest class defined"
Assert ($manifest -match 'extra="forbid"') "PluginManifest extra=forbid"
Assert ($manifest -match "strict=True") "PluginManifest strict=True"
Assert ($manifest -match "sha256") "PluginManifest has sha256 field"

$loader = Get-Content -LiteralPath (Join-Path $Root "src\dhc/plugins/loader.py") -Raw
Assert ($loader -match "def load") "loader.load defined"
Assert ($loader -match "def unload") "loader.unload defined"
Assert ($loader -match "def discover") "loader.discover defined"
Assert ($loader -match "PluginIntegrityError") "loader has PluginIntegrityError"
Assert ($loader -match "compare_digest") "loader uses constant-time SHA-256 compare"
Assert ($loader -match "sha256") "loader reads SHA-256"

# Five bundled plugin manifests must each be present and valid JSON.
foreach ($plugin_id in @("rate_limiter_v1", "session_exporter_v1", "model_router_v1", "memory_store_v1", "prompt_browser_v1")) {
    $m = Get-Content -LiteralPath (Join-Path $Root "src\dhc\plugins/$plugin_id/manifest.json") -Raw
    $pid_re = [regex]::Escape($plugin_id)
    Assert ($m -match ('"id":\s*"' + $pid_re + '"')) "plugin $plugin_id manifest has matching id"
    Assert ($m -match '"version":\s*"') "plugin $plugin_id manifest has version"
    Assert ($m -match '"sha256":\s*"[a-f0-9]{64}"') "plugin $plugin_id manifest has 64-char sha256"
}

# docs/SHA-PINNING.md must record the same SHA as the manifest.
# This locks the docs to the code so a future drift is caught at
# invariant-check time, not at audit time.
$pinning = Get-Content -LiteralPath (Join-Path $Root "docs\SHA-PINNING.md") -Raw
foreach ($plugin_id in @("rate_limiter_v1", "session_exporter_v1", "model_router_v1", "memory_store_v1", "prompt_browser_v1")) {
    $m = Get-Content -LiteralPath (Join-Path $Root "src\dhc\plugins/$plugin_id/manifest.json") -Raw
    $shaMatch = [regex]::Match($m, '"sha256":\s*"([a-f0-9]{64})"')
    if ($shaMatch.Success) {
        $manifest_sha = $shaMatch.Groups[1].Value
        $pid_re = [regex]::Escape($plugin_id)
        $rowPattern = ('\|\s*`' + $pid_re + '`\s*\|\s*`([a-f0-9]{64})`\s*\|')
        $rowMatch = [regex]::Match($pinning, $rowPattern)
        if ($rowMatch.Success) {
            $doc_sha = $rowMatch.Groups[1].Value
            if ($manifest_sha -eq $doc_sha) {
                Write-Output "PASS: docs/SHA-PINNING.md records correct SHA for $plugin_id"
            } else {
                Write-Output "FAIL: docs/SHA-PINNING.md SHA for $plugin_id is $doc_sha but manifest is $manifest_sha"
                $failures.Add("SHA-PINNING.md mismatch for $plugin_id")
            }
        } else {
            Write-Output "FAIL: docs/SHA-PINNING.md missing SHA row for $plugin_id"
            $failures.Add("SHA-PINNING.md missing $plugin_id row")
        }
    } else {
        Write-Output "FAIL: could not extract sha256 from manifest of $plugin_id"
        $failures.Add("manifest of $plugin_id has no extractable sha256")
    }
}

# C1 must register the new marketplace routes
$c1 = Get-Content -LiteralPath (Join-Path $Root "src\dhc/modules/c1_gui_web_core/service.py") -Raw
Assert ($c1 -match "/api/manifest") "C1 registers /api/manifest route"
Assert ($c1 -match 'add_post\("/plugins/\{plugin_id\}"') "C1 registers POST /plugins/{id}"
Assert ($c1 -match 'add_delete\("/plugins/\{plugin_id\}"') "C1 registers DELETE /plugins/{id}"
Assert ($c1 -match "/prompts") "C1 registers /prompts route"
Assert ($c1 -match "/api/eval") "C1 registers /api/eval route"
Assert ($c1 -match "eval_pasted_code") "C1 imports eval_pasted_code from dhc.plugins._inproc_eval"

if ($failures.Count -gt 0) {
    Write-Output ""
    Write-Output "$($failures.Count) invariant(s) failed"
    exit 1
}
Write-Output ""
Write-Output "All invariants pass"
exit 0
