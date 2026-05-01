"""Driver: discover docs pages, fan out validators, write the ledger.

Usage:
    python -m doc_agent.experiment2.run [--sample N] [--max-workers K]

The driver:
  1. Enumerates `.mdx` pages under the corpus docs tree.
  2. Spawns a ThreadPoolExecutor of `max_workers` validators, each bound to
     a shared AppendLedger via a closure-tool.
  3. Waits for all validators to finish.
  4. Dumps the consolidated ledger to JSON.

Issue filing is a separate phase. See `cli_cut_issues.py` — it loads the
`issue-cutter` skill into a one-shot agent that reads the final ledger and
files GH issues. Keep that step out of this driver so you can iterate on the
validator loop without paying the filing cost.

Each run lives under `experiment2/runs/<timestamp>/`:
  - `ledger.jsonl` — append-only trail, one entry per line (crash-safe).
  - `ledger.final.json` — consolidated JSON array after the sweep.
  - `validator_results.json` — per-page runtime + error info (not ledger content).
"""

from __future__ import annotations

import argparse
import json
import random
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from doc_agent.corpus import CORPUS_DIR, ensure_corpus
from doc_agent.experiment2.discover import discover_docs_pages
from doc_agent.experiment2.ledger import counts_by_status, make_docs_validation_ledger
from doc_agent.experiment2.validator import run_validator_on_page

HERE = Path(__file__).parent
RUNS_DIR = HERE / "runs"


def _make_run_dir() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = RUNS_DIR / stamp
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def _log(line: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {line}", flush=True)


def _select_pages(pages: list[Path], sample: int | None) -> list[Path]:
    """Randomly sample `sample` pages from the full list, deterministic via seed."""
    if sample is None or sample >= len(pages):
        return pages
    rng = random.Random(0xD0C)  # deterministic sampling for reproducible runs
    return sorted(rng.sample(pages, sample))


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the experiment2 docs validation sweep.")
    parser.add_argument(
        "--sample",
        type=int,
        default=None,
        help="Validate a random N-page sample instead of the full tree. Useful for cost tuning.",
    )
    parser.add_argument(
        "--pages",
        nargs="+",
        default=None,
        help=(
            "Explicit page paths to validate, relative to corpus/docs/src/content/docs/ "
            "(e.g. user-guide/concepts/agents/agent-loop.mdx). Overrides --sample."
        ),
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=1,
        help=(
            "Max concurrent validator agents. Default 1 (safe smoke path — "
            "bump to 4 or 8 for real sweeps once you trust the setup)."
        ),
    )
    parser.add_argument(
        "--per-page-timeout",
        type=float,
        default=900.0,
        help=(
            "Timeout in seconds for each individual validator agent. "
            "If exceeded the validator is marked as timed-out in the ledger "
            "and the worker moves on. Default 900s (15 min)."
        ),
    )
    args = parser.parse_args()

    ensure_corpus()
    run_dir = _make_run_dir()
    _log(f"run_dir: {run_dir}")

    if args.pages:
        content_root = CORPUS_DIR / "docs/src/content/docs"
        pages = [(content_root / p).resolve() for p in args.pages]
        missing = [p for p in pages if not p.exists()]
        if missing:
            _log(f"ERROR — pages not found: {[str(p) for p in missing]}")
            return 2
        _log(f"running on {len(pages)} explicit page(s): {[p.name for p in pages]}")
    else:
        pages = _select_pages(discover_docs_pages(CORPUS_DIR), args.sample)
        _log(f"discovered {len(pages)} pages to validate (sample={args.sample})")

    ledger = make_docs_validation_ledger(run_dir / "ledger.jsonl")

    # Fan out validators. Each one runs to completion independently; ledger
    # appends are thread-safe. Per-page timeout is enforced via future.result(
    # timeout=...) — on expiry we record a ledger entry for the stuck page and
    # move on. Note that ThreadPoolExecutor can't actually kill the underlying
    # thread, so a hung agent will keep consuming its pool slot until it
    # eventually returns; max_workers acts as a soft cap rather than a hard one
    # in that scenario.
    results = []
    submitted = 0
    completed = 0
    timed_out = 0
    started = time.monotonic()

    with ThreadPoolExecutor(max_workers=args.max_workers) as pool:
        futures: dict = {}
        for page in pages:
            fut = pool.submit(run_validator_on_page, page, CORPUS_DIR, ledger)
            futures[fut] = page
            submitted += 1
        _log(
            f"submitted {submitted} validators with "
            f"max_workers={args.max_workers}, per_page_timeout={args.per_page_timeout}s"
        )

        pending = set(futures.keys())
        while pending:
            done, pending = wait(
                pending,
                timeout=args.per_page_timeout,
                return_when=FIRST_COMPLETED,
            )
            if not done:
                # No future made progress during the timeout window. Pick the
                # still-pending validator that's been running longest and mark
                # it timed-out in the ledger, so the ledger reflects the gap.
                # We can't actually kill the worker thread, so the agent may
                # finish later; its result is ignored.
                stuck = next(iter(pending))
                stuck_page = futures[stuck]
                rel = stuck_page.relative_to(CORPUS_DIR) if stuck_page.is_relative_to(CORPUS_DIR) else stuck_page
                _log(f"TIMEOUT after {args.per_page_timeout}s on {stuck_page.name} — recording gap and moving on")
                ledger.append(
                    agent_id=f"validator-timeout-{stuck_page.stem}",
                    docs_page=str(rel),
                    claim="validator exceeded per-page timeout",
                    status="FAIL",
                    reason=(
                        f"Validator did not finish within {args.per_page_timeout}s. "
                        f"The underlying thread may still be running; its output is ignored."
                    ),
                    suggested_fix="Re-run this page in isolation to diagnose the hang.",
                )
                pending.discard(stuck)
                timed_out += 1
                continue
            for fut in done:
                page = futures[fut]
                try:
                    res = fut.result()
                except Exception as e:  # noqa: BLE001 — defensive; run_validator already catches most
                    _log(f"UNEXPECTED validator crash on {page.name}: {e}")
                    continue
                completed += 1
                results.append(res)
                status = "ok" if res.error is None else f"err: {res.error}"
                _log(
                    f"[{completed}/{submitted}] {res.docs_page} "
                    f"({res.elapsed_seconds:.1f}s) — {status}"
                )

    elapsed = time.monotonic() - started
    _log(
        f"all validators done in {elapsed:.1f}s "
        f"(completed={completed}, timed_out={timed_out})"
    )

    # Dump results + ledger snapshots.
    (run_dir / "validator_results.json").write_text(
        json.dumps([asdict(r) for r in results], indent=2) + "\n"
    )
    ledger.dump_final(run_dir / "ledger.final.json")
    counts = counts_by_status(ledger)
    _log(f"ledger: {counts}")
    _log(
        f"done. To file issues from this ledger, run: "
        f"python -m doc_agent.experiment2.cli_cut_issues {run_dir}/ledger.final.json"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
