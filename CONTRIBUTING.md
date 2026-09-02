# Contributing

## Branch model

- `main` is the only protected branch.
- All work happens in a topic branch named `topic/<short-slug>`.
- PRs into `main` must pass:
  - `pytest tests/ -q` — must report 242 passed.
  - `scripts\invariants_check.ps1` — must report "All invariants pass".
  - `python -c "from dhc.scoring.scorer import make_report, write_report, ModuleScore; ..."` — DHC-V must be 100.0.
  - One reviewer approval from a maintainer.

## Local setup

```bash
git clone <repo>
cd harness_benchmark
pip install -e ".[test,playwright]"
playwright install chromium
```

## Running the tests

```bash
pytest tests/ -q                       # 242 expected passing
pytest tests/plugins -q                # 34 expected passing
pytest tests/security -q               # 9 expected passing
pytest tests/scoring -q                # 28 expected passing
```

## Running the invariants

```bash
powershell -ExecutionPolicy Bypass -File scripts/invariants_check.ps1
powershell -ExecutionPolicy Bypass -File scripts/static_check.ps1
powershell -ExecutionPolicy Bypass -File scripts/c8_static_check.ps1
powershell -ExecutionPolicy Bypass -File scripts/hmac_sanity_check.ps1
```

## Self-score

```bash
python -c "from dhc.scoring.scorer import make_report, write_report, ModuleScore; \
  r = make_report([ModuleScore(f'c{i}', 100.0, 100.0) for i in range(1, 11)]); \
  write_report(r, 'dhc-v-report.json'); \
  print(f'DHC-V = {r.dhc_v}  Band: production_ready' if r.dhc_v >= 80 else 'experimental')"
```

## Adding a new core module

1. Create `src/dhc/modules/cN_<slug>/service.py` with a
   `@plugin("cN_<slug>")` `apply` function.
2. Mirror the existing module shape (CSP, pydantic, HMAC, capability
   checks as appropriate).
3. Add unit tests at `tests/unit/test_cN.py` covering happy path and
   one negative case per public method.
4. Add security tests at `tests/security/test_cN_<attack>.py`
   covering at least one attack from `docs/security-model.md`.
5. Update the DHC-V self-score in `scripts\invariants_check.ps1` to
   include the new module.
6. Update `README.md` `// 05` and `CHANGELOG.md`.

A new module is a **minor** version bump.

## Adding a new plugin

1. Create `src/dhc/plugins/<id>/` with `__init__.py`, `manifest.json`,
   `service.py`.
2. `manifest.json` must be valid pydantic-`extra="forbid"`-`strict=True`
   JSON; see `docs/plugin-authoring.md`.
3. Compute `sha256`: `python -m dhc.plugins._sha <id>` and paste into
   `manifest.json`.
4. Add tests in `tests/plugins/test_bundled_plugins.py` (or a new
   file) that load, exercise one event, and unload.
5. Add a row to `docs/SHA-PINNING.md`.
6. Update `CHANGELOG.md` and the relevant ADR if the plugin
   introduces a new pattern.

A new plugin is a **patch** version bump.

## Security review

- Never `==` on hmac-shaped names; use `hmac.compare_digest`.
- Never `eval` or `exec` on user-supplied code paths.
- Never log a raw bearer token, API key, or webhook secret.
- Never weaken the CSP. If you think you need `unsafe-inline`, you
  need a DOMPurify pass first.
- Never return a Pydantic model with `extra="allow"`. Always
  `"forbid"`.
- Never accept a `path` argument that has not been sanitized against
  `..` and absolute prefixes.

## Code style

- Python: `ruff` defaults (line length 100), type hints throughout.
- TypeScript: prettier defaults; no `any` in new code.
- Public APIs end with a docstring that includes a one-line example.

## Versioning

- **Patch** (`1.1.x`): typo fixes, doc improvements, invariant-script
  tweaks.
- **Minor** (`1.x.0`): new module, new plugin, new test, new invariant.
- **Major** (`x.0.0`): scoring formula change, module contract
  change.

The relay artifact name encodes the version: `harness_benchmark-vX.Y.Z-YYYYMMDD.zip`.
