"""Factory binding a `DocsValidationEntry` ledger to a per-agent tool.

Each validator agent gets its own `ledger_append` tool instance — same
underlying ledger, different `agent_id` stamped onto every entry. Strands only
sees the tool's parameter schema; the shared state is invisible to the agent.
"""

from __future__ import annotations

from typing import Callable, Literal

from strands import tool

from doc_agent.experiment2.ledger import (
    AppendLedger,
    DocsValidationEntry,
    DocsValidationStatus,
)


def make_ledger_tool(
    ledger: AppendLedger[DocsValidationEntry],
    agent_id: str,
) -> Callable:
    """Return a `@tool`-decorated function that records into the given ledger."""

    @tool(
        name="ledger_append",
        description=(
            "Record a single actionable finding into the shared run ledger. "
            "Only call this for claims you want a human to see — DO NOT record "
            "claims that check out against source (those aren't ledger "
            "material; skip them silently). Status values: "
            "\"FAIL\" — claim contradicts on-disk SDK source (cite source_file "
            "+ source_lines + reason + suggested_fix); "
            "\"UNVERIFIABLE\" — authoritative SDK source is not in the corpus, "
            "claim cannot be checked (set source_file=null and name the missing "
            "package in reason); "
            "\"UNCLEAR_PROSE\" — page-level clarity issue (quote confusing "
            "spans in quoted_excerpts). "
            "Returns {seq, status} so you can confirm the write landed."
        ),
    )
    def ledger_append(
        docs_page: str,
        claim: str,
        status: Literal["FAIL", "UNVERIFIABLE", "UNCLEAR_PROSE"],
        doc_lines: list[int] | None = None,
        source_file: str | None = None,
        source_lines: list[int] | None = None,
        reason: str | None = None,
        suggested_fix: str | None = None,
        quoted_excerpts: list[str] | None = None,
    ) -> dict:
        """Append an entry. Only actionable findings go here — FAIL claims,
        UNVERIFIABLE claims (gap-in-corpus), UNCLEAR_PROSE pages. Silently
        skip claims that verify cleanly."""
        entry = ledger.append(
            agent_id=agent_id,
            docs_page=docs_page,
            claim=claim,
            status=status,
            doc_lines=doc_lines or [],
            source_file=source_file,
            source_lines=source_lines,
            reason=reason,
            suggested_fix=suggested_fix,
            quoted_excerpts=quoted_excerpts,
        )
        return {"seq": entry.seq, "status": entry.status}

    return ledger_append
