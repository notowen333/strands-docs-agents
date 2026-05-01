"""One-shot validator agent for a single docs page.

Called from `run.py` inside a thread pool. Each call constructs a fresh Agent
with read-only tools + a ledger_append tool bound to the shared Ledger. The
agent reads the page, extracts claims, verifies them against SDK source, and
records findings via the ledger tool. Its prose return value is only used for
run-level monitoring — the real output is what ended up in the ledger.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from strands import Agent

from doc_agent.experiment2.discover import relative_to_content
from doc_agent.experiment2.ledger import AppendLedger, DocsValidationEntry
from doc_agent.experiment2.ledger_tool import make_ledger_tool
from doc_agent.model import create_model_1m
from doc_agent.tools import file_read
from doc_agent.tools.glob_tool import glob_tool
from doc_agent.tools.grep_tool import grep_tool

PROMPT_PATH = Path(__file__).parent / "prompts" / "validator.md"


@dataclass
class ValidatorResult:
    """What the driver captures for run-level logging. Ledger entries are not here."""
    agent_id: str
    docs_page: str
    elapsed_seconds: float
    final_message: str
    error: str | None = None


def _build_validator_agent(
    docs_page: Path,
    corpus_dir: Path,
    ledger: AppendLedger[DocsValidationEntry],
    agent_id: str,
) -> Agent:
    system_prompt = PROMPT_PATH.read_text()
    # Lightweight substitution — the prompt references {corpus_dir}; docs_page
    # is passed in the task input rather than the system prompt so the same
    # system prompt can be reused across pages.
    system_prompt = system_prompt.replace("{corpus_dir}", str(corpus_dir))

    return Agent(
        name=agent_id,
        model=create_model_1m(),
        system_prompt=system_prompt,
        tools=[
            file_read,
            glob_tool,
            grep_tool,
            make_ledger_tool(ledger, agent_id),
        ],
    )


def run_validator_on_page(
    docs_page: Path,
    corpus_dir: Path,
    ledger: AppendLedger[DocsValidationEntry],
) -> ValidatorResult:
    """Validate one `.mdx` page. Appends findings to the shared ledger.

    Exceptions are caught and returned in the result's `error` field — one
    crashing validator shouldn't take down the pool.
    """
    rel_page = relative_to_content(corpus_dir, docs_page)
    agent_id = f"validator-{rel_page.replace('/', '_').replace('.mdx', '')}"

    started = time.monotonic()
    final_message = ""
    error: str | None = None

    try:
        agent = _build_validator_agent(docs_page, corpus_dir, ledger, agent_id)
        task_prompt = (
            f"Validate the docs page at `{docs_page}`.\n\n"
            f"Relative path (use this when calling `ledger_append`): "
            f"`{rel_page}`\n\n"
            f"Follow your system prompt's procedure: read the page, extract "
            f"every factual claim, verify each against on-disk SDK source, "
            f"and call `ledger_append` once per claim. End with a one-line "
            f"summary."
        )
        result = agent(task_prompt)
        final_message = str(result).strip().splitlines()[-1] if str(result).strip() else ""
    except Exception as e:  # noqa: BLE001 — we want to swallow anything
        error = f"{type(e).__name__}: {e}"
        # Record the crash in the ledger so the page isn't silently missed.
        ledger.append(
            agent_id=agent_id,
            docs_page=rel_page,
            claim="validator crashed before completing",
            status="FAIL",
            reason=error,
            suggested_fix="Re-run this page in isolation to reproduce; the page was not validated.",
        )

    elapsed = time.monotonic() - started
    return ValidatorResult(
        agent_id=agent_id,
        docs_page=rel_page,
        elapsed_seconds=elapsed,
        final_message=final_message,
        error=error,
    )
