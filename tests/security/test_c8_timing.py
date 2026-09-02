"""C8 security tests: HMAC, replay, malformed payload, and the timing directive.

The timing directive has two parts:

1.  STATIC (mandatory): the implementation must NOT use `==` to compare
    `hmac`-shaped values. We AST-walk the service module and fail the test
    if we find a `Compare` node whose operands are likely HMAC hex digests,
    or a direct `==` on the `provided` / `expected` locals.

2.  BEHAVIORAL (best-effort): measure wall-clock for matching vs. mismatching
    signatures. The ratio must be bounded. Marked `xfail(strict=False)` so
    noisy CI does not break the build, but kept as a documented check.
"""

from __future__ import annotations

import ast
import hashlib
import hmac
import statistics
import time
from pathlib import Path
from typing import Iterable

import pytest

from dhc.fixtures.mock_llm.scripts import (
    FROZEN_EPOCH_MS,
    NONCE_SEQUENCE,
    VALID_HMAC_BODY,
    VALID_HMAC_DIGEST,
    VALID_HMAC_NONCE,
    VALID_HMAC_TIMESTAMP,
    WEBHOOK_SECRET,
)
from dhc.modules.c8_webhook_dispatch.service import (
    InvalidSignature,
    WebhookDispatch,
    verify_signature,
)


SERVICE_FILE = (
    Path(__file__).resolve().parents[2] / "src" / "dhc" / "modules" / "c8_webhook_dispatch" / "service.py"
)


# Names that are highly likely to be assigned an HMAC digest or comparable
# secret value. The check fires when two of these are compared with `==`.
_HMAC_SHAPED_NAMES = {
    "provided",
    "expected",
    "digest",
    "signature",
    "computed",
    "got",
    "want",
    "actual",
    "their",
    "ours",
    "a_sig",
    "b_sig",
    "a_digest",
    "b_digest",
    "sig_a",
    "sig_b",
    "hmac_val",
    "hmac_value",
    "sig_hex",
    "mac",
    "mac_hex",
    "hash_hex",
    "hex_a",
    "hex_b",
    "left",
    "right",
}


def _is_hmac_shaped_name(node: ast.AST) -> bool:
    if not isinstance(node, ast.Name):
        return False
    name = node.id.lower()
    if name in _HMAC_SHAPED_NAMES:
        return True
    for token in ("hmac", "sig", "mac", "hash", "digest"):
        if token in name:
            return True
    return False


def _parse_module() -> ast.Module:
    return ast.parse(SERVICE_FILE.read_text(encoding="utf-8"), filename=str(SERVICE_FILE))


def _ast_uses_hmac_compare_digest() -> bool:
    tree = _parse_module()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "hmac"
                and func.attr == "compare_digest"
            ):
                return True
    return False


def _ast_has_plain_eq_on_hmac_shaped_pairs() -> list[tuple[int, str]]:
    suspicious: list[tuple[int, str]] = []
    tree = _parse_module()
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            for op in node.ops:
                if not isinstance(op, ast.Eq):
                    continue
                if len(node.comparators) != 1:
                    continue
                left = node.left
                right = node.comparators[0]
                if _is_hmac_shaped_name(left) and _is_hmac_shaped_name(right):
                    suspicious.append((node.lineno, ast.unparse(node)))
    return suspicious


def test_c8_uses_hmac_compare_digest():
    assert _ast_uses_hmac_compare_digest(), "C8 must call hmac.compare_digest"


def test_c8_does_not_use_plain_eq_on_hmac_shaped_pairs():
    bad = _ast_has_plain_eq_on_hmac_shaped_pairs()
    assert not bad, f"plain == used on hmac-shaped values: {bad}"


def test_c8_compare_digest_module_call_present():
    src = SERVICE_FILE.read_text(encoding="utf-8")
    assert "hmac.compare_digest" in src


@pytest.mark.parametrize(
    "tampered",
    [
        "sha256=" + "0" * 64,
        "sha256=" + "f" * 64,
        "sha256=" + "a" * 64,
    ],
)
def test_c8_tampered_digests_always_rejected(tampered: str):
    with pytest.raises(InvalidSignature):
        verify_signature(
            secret=WEBHOOK_SECRET,
            body=VALID_HMAC_BODY,
            timestamp=VALID_HMAC_TIMESTAMP,
            nonce=VALID_HMAC_NONCE,
            signature_header=tampered,
        )


@pytest.mark.xfail(reason="noisy on CI; informational only", strict=False)
def test_c8_compare_digest_is_constant_time():
    def measure(sig_hex: str) -> float:
        digest = "sha256=" + sig_hex
        samples: list[float] = []
        for _ in range(2000):
            t0 = time.perf_counter_ns()
            try:
                verify_signature(
                    secret=WEBHOOK_SECRET,
                    body=VALID_HMAC_BODY,
                    timestamp=VALID_HMAC_TIMESTAMP,
                    nonce=VALID_HMAC_NONCE,
                    signature_header=digest,
                )
            except InvalidSignature:
                pass
            samples.append(time.perf_counter_ns() - t0)
        return statistics.median(samples)

    good_sig = VALID_HMAC_DIGEST.split("=", 1)[1]
    bad_sig_a = good_sig[:-1] + ("0" if good_sig[-1] != "0" else "1")
    bad_sig_b = "0" + good_sig[1:]

    t_good = measure(good_sig)
    t_bad_a = measure(bad_sig_a)
    t_bad_b = measure(bad_sig_b)

    ratio_a = max(t_good, t_bad_a) / min(t_good, t_bad_a)
    ratio_b = max(t_good, t_bad_b) / min(t_good, t_bad_b)
    assert ratio_a < 10.0
    assert ratio_b < 10.0


def test_c8_synthetic_tampered_body_rejected():
    tampered = VALID_HMAC_BODY + b" "
    with pytest.raises(InvalidSignature):
        verify_signature(
            secret=WEBHOOK_SECRET,
            body=tampered,
            timestamp=VALID_HMAC_TIMESTAMP,
            nonce=NONCE_SEQUENCE[10],
            signature_header=VALID_HMAC_DIGEST,
        )


def test_c8_nonce_store_does_not_grow_unbounded():
    from dhc.modules.c8_webhook_dispatch.service import NonceStore

    store = NonceStore(max_size=8)
    for n in range(32):
        store.check_and_record(f"n-{n}", now_ms=FROZEN_EPOCH_MS)
    assert len(store._store) == 8
