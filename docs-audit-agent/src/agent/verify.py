"""Trust check for a completed experiment2 sweep.

Answers one question: "is this run's output trustworthy enough to feed into
the issue-cutter?"

Checks (each is ✓/⚠/✗):
  1. Completion — every validator_results row reports no error and no timeout.
  2. Policy — FAIL entries with a non-null source_file cite `sdk-python/` or
     `sdk-typescript/` (not `docs/`, not other paths). Statuses are in-vocabulary.
     Catches the circular-citation failure mode.
  3. Coverage — every validator produced a final message; no anomalously short
     runs that suggest the validator gave up early.

Note: the ledger only records actionable findings (FAIL / UNVERIFIABLE /
UNCLEAR_PROSE). Clean claims are silently skipped. Pages with zero ledger
entries are therefore normal, NOT a trust signal — we look at
`validator_results.json` (elapsed time, error state) for completion evidence.

Usage:
    python -m doc_agent.experiment2.verify <run_dir>

Exits 0 if all checks pass (✓ only). Exits 1 if any hard check fails (✗).
Soft warnings (⚠) don't affect exit code; they surface for eyeballing.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

# A FAIL entry's source_file (when non-null) MUST start with one of these.
# Anything else — `docs/`, `examples/`, etc. — is a policy violation.
SDK_PREFIXES = ("sdk-python/", "sdk-typescript/")
VALID_STATUSES = {"FAIL", "UNVERIFIABLE", "UNCLEAR_PROSE"}
# Validators that ran for less than this many seconds likely didn't scan the
# page properly. Soft warning only; a page could legitimately scan fast.
SHORT_RUN_THRESHOLD_SECONDS = 30.0


class Check:
    """One named check with a ✓/⚠/✗ status and human-readable detail."""

    def __init__(self, name: str, status: str, detail: str) -> None:
        self.name = name
        self.status = status  # "pass" | "warn" | "fail"
        self.detail = detail

    @property
    def mark(self) -> str:
        return {"pass": "✓", "warn": "⚠", "fail": "✗"}[self.status]


def _load(run_dir: Path) -> tuple[list[dict], list[dict]]:
    ledger_path = run_dir / "ledger.final.json"
    results_path = run_dir / "validator_results.json"
    if not ledger_path.exists():
        raise SystemExit(f"missing: {ledger_path}")
    if not results_path.exists():
        raise SystemExit(f"missing: {results_path}")
    ledger = json.loads(ledger_path.read_text())
    results = json.loads(results_path.read_text())
    return ledger, results


def _check_completion(results: list[dict]) -> list[Check]:
    """Did every validator finish and emit entries, or did some time out / crash?"""
    checks: list[Check] = []

    timed_out = [r for r in results if r.get("error") and "timeout" in r.get("error", "").lower()]
    crashed = [r for r in results if r.get("error") and "timeout" not in r.get("error", "").lower()]

    if timed_out:
        pages = ", ".join(r["docs_page"] for r in timed_out[:3])
        suffix = "..." if len(timed_out) > 3 else ""
        checks.append(Check(
            "validators timed out",
            "fail",
            f"{len(timed_out)} page(s): {pages}{suffix}",
        ))
    else:
        checks.append(Check("validators timed out", "pass", "0"))

    if crashed:
        pages = ", ".join(r["docs_page"] for r in crashed[:3])
        suffix = "..." if len(crashed) > 3 else ""
        checks.append(Check(
            "validators crashed",
            "fail",
            f"{len(crashed)} page(s): {pages}{suffix}",
        ))
    else:
        checks.append(Check("validators crashed", "pass", "0"))

    return checks


def _check_policy(ledger: list[dict]) -> list[Check]:
    """Are FAIL citations real SDK paths? Are statuses in-vocabulary?"""
    checks: list[Check] = []

    # Invalid statuses (would mean the tool schema leaked something unexpected
    # OR that a stale PASS-era ledger is being re-verified).
    bad_status = [e for e in ledger if e["status"] not in VALID_STATUSES]
    if bad_status:
        sample = ", ".join(f"{e['status']!r}" for e in bad_status[:3])
        checks.append(Check(
            "entries with invalid status",
            "fail",
            f"{len(bad_status)}: {sample}",
        ))
    else:
        checks.append(Check("entries with invalid status", "pass", "0"))

    # FAIL entries with a non-null source_file must cite SDK (null is allowed
    # for FAIL — it means "symbol nowhere in SDK source").
    bad_fail = [
        e for e in ledger
        if e["status"] == "FAIL"
        and e.get("source_file")
        and not _has_sdk_source(e.get("source_file"))
    ]
    if bad_fail:
        sample = "; ".join(
            f"{e['docs_page']} → {e.get('source_file')!r}" for e in bad_fail[:3]
        )
        checks.append(Check(
            "FAIL with non-SDK source_file",
            "fail",
            f"{len(bad_fail)} (first 3: {sample})",
        ))
    else:
        checks.append(Check("FAIL with non-SDK source_file", "pass", "0"))

    # UNVERIFIABLE entries should NOT cite a source_file (the whole point is
    # that we couldn't find one). If they do, something's wrong.
    bad_unverifiable = [
        e for e in ledger
        if e["status"] == "UNVERIFIABLE" and e.get("source_file")
    ]
    if bad_unverifiable:
        sample = "; ".join(
            f"{e['docs_page']} → {e.get('source_file')!r}"
            for e in bad_unverifiable[:3]
        )
        checks.append(Check(
            "UNVERIFIABLE with non-null source_file",
            "fail",
            f"{len(bad_unverifiable)} (first 3: {sample})",
        ))
    else:
        checks.append(Check("UNVERIFIABLE with non-null source_file", "pass", "0"))

    return checks


def _has_sdk_source(source_file: str | None) -> bool:
    if not source_file:
        return False
    return any(source_file.startswith(p) for p in SDK_PREFIXES)


def _check_coverage(results: list[dict]) -> list[Check]:
    """Did every validator actually do work? Any anomalously-short runs?

    Zero ledger entries on a page is NOT a trust problem in the current
    design — validators only record actionable findings. A squeaky-clean
    page would produce zero entries. Instead we look at validator_results:
    did the validator report no error, and did it run long enough to
    plausibly scan the page?
    """
    checks: list[Check] = []

    missing_final_message = [
        r for r in results
        if not r.get("error") and not (r.get("final_message") or "").strip()
    ]
    if missing_final_message:
        sample = ", ".join(r["docs_page"] for r in missing_final_message[:3])
        suffix = "..." if len(missing_final_message) > 3 else ""
        checks.append(Check(
            "validators with empty final message",
            "warn",
            f"{len(missing_final_message)}: {sample}{suffix}",
        ))
    else:
        checks.append(Check("validators with empty final message", "pass", "0"))

    short_runs = [
        r for r in results
        if not r.get("error")
        and r.get("elapsed_seconds", 0) < SHORT_RUN_THRESHOLD_SECONDS
    ]
    if short_runs:
        sample = ", ".join(
            f"{r['docs_page']} ({r['elapsed_seconds']:.0f}s)" for r in short_runs[:3]
        )
        suffix = "..." if len(short_runs) > 3 else ""
        checks.append(Check(
            f"validators that finished in <{SHORT_RUN_THRESHOLD_SECONDS:.0f}s",
            "warn",
            f"{len(short_runs)}: {sample}{suffix}",
        ))
    else:
        checks.append(Check(
            f"validators that finished in <{SHORT_RUN_THRESHOLD_SECONDS:.0f}s",
            "pass",
            "0",
        ))

    return checks


def _distribution_summary(ledger: list[dict], results: list[dict]) -> list[str]:
    """Printable per-page finding distribution for the report. Not a check, just info."""
    findings_by_page: dict[str, int] = {r["docs_page"]: 0 for r in results}
    for e in ledger:
        if e["agent_id"].startswith("validator-timeout-"):
            continue
        p = e["docs_page"]
        if p in findings_by_page:
            findings_by_page[p] += 1
    counts = sorted(findings_by_page.values())
    elapsed = sorted(r.get("elapsed_seconds", 0) for r in results)
    if not counts:
        return ["  no pages scanned"]
    return [
        f"  pages scanned:              {len(counts)}",
        f"  median findings per page:   {statistics.median(counts):.0f}",
        f"  findings count range:       [{min(counts)}, {max(counts)}]",
        f"  median validator runtime:   {statistics.median(elapsed):.0f}s",
        f"  validator runtime range:    [{min(elapsed):.0f}s, {max(elapsed):.0f}s]",
    ]


def _status_counts(ledger: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {s: 0 for s in VALID_STATUSES}
    for e in ledger:
        counts[e["status"]] = counts.get(e["status"], 0) + 1
    return counts


def verify(run_dir: Path) -> int:
    ledger, results = _load(run_dir)

    sections: list[tuple[str, list[Check]]] = [
        ("completion", _check_completion(results)),
        ("policy", _check_policy(ledger)),
        ("coverage", _check_coverage(results)),
    ]

    lines: list[str] = []
    lines.append(f"=== trust check for {run_dir.name} ===")
    lines.append(f"run_dir: {run_dir}")
    lines.append("")

    # Top-line counts
    ts = _status_counts(ledger)
    lines.append(
        "ledger status counts:  "
        f"FAIL={ts.get('FAIL', 0)}  "
        f"UNVERIFIABLE={ts.get('UNVERIFIABLE', 0)}  "
        f"UNCLEAR_PROSE={ts.get('UNCLEAR_PROSE', 0)}"
    )
    lines.append(
        f"pages reported by driver:  {len(results)}"
    )
    lines.append("")

    any_fail = False
    any_warn = False
    for name, checks in sections:
        lines.append(f"{name}:")
        for c in checks:
            lines.append(f"  {c.mark} {c.name}: {c.detail}")
            if c.status == "fail":
                any_fail = True
            elif c.status == "warn":
                any_warn = True
        lines.append("")

    lines.append("distribution:")
    for line in _distribution_summary(ledger, results):
        lines.append(line)
    lines.append("")

    if any_fail:
        verdict = "NOT TRUSTWORTHY — hard checks failed. Do not pass to issue-cutter without review."
        exit_code = 1
    elif any_warn:
        verdict = "TRUSTWORTHY WITH CAVEATS — soft flags worth eyeballing, but no hard failures."
        exit_code = 0
    else:
        verdict = "TRUSTWORTHY — all checks passed."
        exit_code = 0

    lines.append(f"VERDICT: {verdict}")
    report = "\n".join(lines)

    print(report)

    # Persist the report alongside the ledger for later reference.
    (run_dir / "trust_report.txt").write_text(report + "\n")

    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a trust check against a completed experiment2 sweep."
    )
    parser.add_argument(
        "run_dir",
        type=Path,
        help="Path to runs/<timestamp>/ produced by `run.py`.",
    )
    args = parser.parse_args()

    if not args.run_dir.is_dir():
        parser.error(f"not a directory: {args.run_dir}")
    return verify(args.run_dir)


if __name__ == "__main__":
    sys.exit(main())
