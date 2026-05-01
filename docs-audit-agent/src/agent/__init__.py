"""experiment2 — reverse-validation docs sweep.

Many concurrent validator agents read each docs page, verify every factual
claim against on-disk SDK source, and record findings into a shared ledger.
A separate CLI (`cli_cut_issues.py`) reads the finalized ledger and files
GH issues via the `issue-cutter` skill.

Two entrypoints:
    python -m doc_agent.experiment2.run            # validation sweep
    python -m doc_agent.experiment2.cli_cut_issues # issue filing
"""

from doc_agent.experiment2.ledger import (
    AppendLedger,
    DocsValidationEntry,
    DocsValidationStatus,
    counts_by_status,
    make_docs_validation_ledger,
)
from doc_agent.experiment2.validator import ValidatorResult, run_validator_on_page

__all__ = [
    "AppendLedger",
    "DocsValidationEntry",
    "DocsValidationStatus",
    "counts_by_status",
    "make_docs_validation_ledger",
    "ValidatorResult",
    "run_validator_on_page",
]
