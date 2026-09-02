"""Phase 3.5 — LLM evaluation wrapper.

For each master prompt in `src/dhc/eval/prompts/`:

  1.  Call the configured OpenAI-compatible endpoint to generate
      Python code for the module (temperature = 0.0).
  2.  Extract the raw code (strip markdown fences).
  3.  Backup the reference `service.py`, write the LLM output in
      its place.
  4.  Run the module's test suite in a sandboxed subprocess.
  5.  Map pytest failures to `Finding` objects via the Rosetta
      Stone, score the module, and write a per-model report.
  6.  Restore the reference `service.py` before moving on.

Usage:
    python -m dhc.eval.run_llm_eval --model gpt-4o --modules all
    python -m dhc.eval.run_llm_eval --model gpt-4o --modules c8
    python -m dhc.eval.run_llm_eval --offline --offline-input fixtures/synthetic_c8.py --modules c8

Environment variables:
    OPENAI_BASE_URL    default https://api.openai.com/v1
    OPENAI_API_KEY     required for live runs
    LLM_TEMPERATURE    default 0.0
    LLM_MAX_TOKENS     default 4096
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
import time
from pathlib import Path
from typing import Iterable

from dhc.eval.backup import ReferenceBackup
from dhc.eval.client import LLMClient, LLMClientError
from dhc.eval.extractor import extract_code
from dhc.eval.prompts import ALL_MODULE_KEYS, load_prompt, service_path, test_paths
from dhc.eval.rosetta import findings_from_exception_trace, parse_pytest_output
from dhc.eval.runner import run_pytest_in_subprocess
from dhc.scoring.scorer import Finding, ModuleScore, make_report, write_report


REPO_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = REPO_ROOT / "src"


def _all_service_paths() -> list[Path]:
    return [service_path(REPO_ROOT, k) for k in ALL_MODULE_KEYS]


def _parse_modules(arg: str) -> list[str]:
    if arg == "all":
        return list(ALL_MODULE_KEYS)
    parts = [p.strip() for p in arg.split(",") if p.strip()]
    out: list[str] = []
    for p in parts:
        if p not in ALL_MODULE_KEYS:
            raise SystemExit(f"unknown module: {p!r}; valid: {list(ALL_MODULE_KEYS)}")
        out.append(p)
    if not out:
        raise SystemExit("no modules selected")
    return out


def _score_module(module_key: str, parsed) -> tuple[ModuleScore, list]:
    """Translate a ParsedTestResult into a ModuleScore and the raw findings."""
    findings = parse_pytest_output(parsed, module_key)
    # unit pass rate: passed / (passed + failed + errored); skipped
    # tests are excluded from the denominator.
    parsed_passed = getattr(parsed, "passed", 0)
    failed = getattr(parsed, "total_failed", 0)
    if parsed_passed + failed == 0:
        # No tests ran (likely a collection error already handled by
        # the Rosetta). In that case functionality is 0.
        unit_pass_rate = 0.0
    else:
        unit_pass_rate = parsed_passed / (parsed_passed + failed)
    from dhc.scoring.scorer import score_functionality, score_security, compute_dhc_v

    func = score_functionality(
        unit_pass_rate=unit_pass_rate,
        turn_completion_rate=unit_pass_rate,  # proxy
        ui_streaming_fidelity=None,             # proxy: cannot run Playwright
    )
    sec, floor = score_security(findings)
    dhc_v = compute_dhc_v(func, sec, floor)
    return (
        ModuleScore(
            module=module_key,
            functionality=func,
            security=sec,
            findings=findings,
            notes=[f"unit_pass_rate={unit_pass_rate:.2%}", f"failed={failed}", f"passed={parsed_passed}"],
        ),
        findings,
    )


async def _generate_code(
    client: LLMClient | None,
    module_key: str,
    *,
    offline_input: Path | None,
    prompt_override: str | None,
) -> str:
    """Get the LLM's generated code for a module.

    Three modes:
      * online: call the LLM endpoint with the master prompt
      * offline: read a pre-saved response from disk
      * prompt_override: use a literal prompt (and still call the
        LLM); mostly for debugging
    """
    prompt = prompt_override if prompt_override is not None else load_prompt(module_key)

    if offline_input is not None:
        raw = offline_input.read_text(encoding="utf-8")
        if client is not None and not prompt_override:
            # Use the offline response, but still call the LLM
            # to get a prompt (so the dry-run keeps the real prompt
            # in the log). Actually for true dry-run we just skip
            # the call.
            pass
        return extract_code(raw)

    if client is None:
        raise LLMClientError("online mode requires a configured LLMClient")

    system = (
        "You are a senior Python engineer. "
        "Respond with a single Python code block only. "
        "Do not include explanations, markdown headings, or commentary."
    )
    raw = await client.chat(prompt, system=system, temperature=0.0, max_tokens=4096)
    return extract_code(raw)


async def _evaluate_one_module(
    module_key: str,
    *,
    client: LLMClient | None,
    offline_input: Path | None,
    output_dir: Path,
    timeout_sec: int,
) -> ModuleScore:
    backup = ReferenceBackup([service_path(REPO_ROOT, module_key)])
    parsed = None
    try:
        code = await _generate_code(
            client, module_key, offline_input=offline_input, prompt_override=None
        )
        # Inject the generated code.
        target = service_path(REPO_ROOT, module_key)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(code, encoding="utf-8")
        ReferenceBackup.clear_repo_bytecache(REPO_ROOT)

        # Run the test suite in a sandboxed subprocess.
        tests = test_paths(REPO_ROOT, module_key)
        parsed = run_pytest_in_subprocess(tests, repo_root=REPO_ROOT, timeout_sec=timeout_sec)
    except LLMClientError as exc:
        return _module_score_from_exception(module_key, str(exc))
    except Exception as exc:
        tb = getattr(exc, "__traceback__", None)
        text = repr(exc) if not tb else _format_tb(tb)
        return _module_score_from_exception(module_key, text)
    finally:
        # Restore the reference implementation unconditionally.
        backup.restore()

    if parsed is None:
        return _module_score_from_exception(module_key, "no parser result")
    score, _ = _score_module(module_key, parsed)
    # Persist the LLM's raw generated code for audit.
    (output_dir / f"{module_key}.generated.py").write_text(code, encoding="utf-8")
    (output_dir / f"{module_key}.pytest.txt").write_text(parsed.stdout, encoding="utf-8")
    return score


def _module_score_from_exception(module_key: str, message: str) -> ModuleScore:
    findings = findings_from_exception_trace(module_key, message)
    if not findings:
        findings = [Finding(module_key, "critical", f"Evaluation error: {message[:200]}")]
    from dhc.scoring.scorer import score_functionality, score_security, compute_dhc_v

    func = score_functionality(0.0, 0.0, None)
    sec, floor = score_security(findings)
    dhc_v = compute_dhc_v(func, sec, floor)
    return ModuleScore(
        module=module_key,
        functionality=func,
        security=sec,
        findings=findings,
        notes=[f"evaluation error: {message[:200]}"],
    )


def _format_tb(tb) -> str:
    import traceback

    return "".join(traceback.format_exception(type(tb).__name__, tb, tb))[:400]


def _build_leaderboard(
    model_name: str,
    scores: list[ModuleScore],
    all_findings: list,
) -> dict:
    """Build the JSON-serializable leaderboard row for one model run."""
    return {
        "model": model_name,
        "timestamp": int(time.time()),
        "scores": make_report(scores, findings=all_findings).to_dict(),
    }


async def amain() -> int:
    parser = argparse.ArgumentParser(
        description="Run the DHC evaluation wrapper against an LLM."
    )
    parser.add_argument("--model", default=os.environ.get("DHC_MODEL", "gpt-4o"))
    parser.add_argument(
        "--base-url",
        default=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("OPENAI_API_KEY", ""),
    )
    parser.add_argument(
        "--modules",
        default="all",
        help="comma-separated module keys (c1..c10) or 'all'",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="do not call the LLM; require --offline-input",
    )
    parser.add_argument(
        "--offline-input",
        type=Path,
        default=None,
        help="path to a saved LLM response (one per module) or a single "
             "file used for every module in --modules",
    )
    parser.add_argument(
        "--offline-input-dir",
        type=Path,
        default=None,
        help="directory containing <module_key>.txt files (e.g. c8.txt)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="per-module pytest timeout in seconds (default: 60)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / "leaderboard",
        help="directory for per-model output (default: ./leaderboard)",
    )
    args = parser.parse_args()

    if args.offline and not args.offline_input and not args.offline_input_dir:
        parser.error("--offline requires --offline-input or --offline-input-dir")

    modules = _parse_modules(args.modules)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    client: LLMClient | None = None
    if not args.offline:
        if not args.api_key:
            print("ERROR: --api-key (or OPENAI_API_KEY) is required for online runs", file=sys.stderr)
            return 2
        client = LLMClient(
            base_url=args.base_url,
            api_key=args.api_key,
            model=args.model,
        )

    try:
        all_scores: list[ModuleScore] = []
        all_findings: list = []
        for module_key in modules:
            offline_input: Path | None = None
            if args.offline:
                if args.offline_input_dir is not None:
                    candidate = args.offline_input_dir / f"{module_key}.txt"
                    if candidate.is_file():
                        offline_input = candidate
                elif args.offline_input is not None:
                    offline_input = args.offline_input
            print(f"[{args.model}] evaluating {module_key} ...", flush=True)
            score = await _evaluate_one_module(
                module_key,
                client=client,
                offline_input=offline_input,
                output_dir=args.output_dir,
                timeout_sec=args.timeout,
            )
            all_scores.append(score)
            all_findings.extend(score.findings)
            print(
                f"  dhc_v={score.notes}  findings={len(score.findings)}",
                flush=True,
            )
        report = _build_leaderboard(args.model, all_scores, all_findings)
        out_path = args.output_dir / f"{args.model.replace('/', '_')}.json"
        import json
        out_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        print(f"[{args.model}] wrote {out_path}", flush=True)
        print(
            f"[{args.model}] aggregate dhc_v={report['scores']['dhc_v']:.2f} "
            f"band={'production_ready' if report['scores']['dhc_v'] >= 80 else 'experimental' if report['scores']['dhc_v'] >= 50 else 'unsafe'}",
            flush=True,
        )
        return 0
    finally:
        if client is not None:
            await client.aclose()


def main() -> int:
    return asyncio.run(amain())


if __name__ == "__main__":
    sys.exit(main())
