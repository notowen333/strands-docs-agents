"""Append-only ledger primitives.

Two layers:

1. `AppendLedger[E]` — generic thread-safe, JSONL-backed append log. Does not
   know what an entry looks like; callers supply a dataclass type and
   per-append kwargs that are forwarded to the dataclass constructor. Handles:
   - serialized append under `threading.Lock`
   - seq + timestamp stamping
   - crash-safe JSONL flush per append (so a midway crash still leaves readable rows)
   - snapshot + final JSON array dump

2. Concrete entry types for specific sweeps. `DocsValidationEntry` below is
   the schema used by experiment2's docs-validation sweep. Other sweeps can
   define their own dataclass and instantiate a new `AppendLedger`.

The abstraction exists because "thread-safe append log with disk spill" is
the reusable part — what goes in each row is the experiment-specific part.
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generic, Literal, TypeVar


E = TypeVar("E")


class AppendLedger(Generic[E]):
    """Thread-safe append-only log with JSONL flush-on-append.

    Parameterized on an entry dataclass type `E`. The entry type must:
      - be a `@dataclass`,
      - have `seq: int` and `timestamp: str` fields (the ledger stamps them),
      - be JSON-serializable via `dataclasses.asdict`.

    Instantiate once per run. All writers share one instance; readers call
    `snapshot()` or `dump_final()` after the run is quiescent.
    """

    def __init__(self, jsonl_path: Path, entry_type: type[E]) -> None:
        if not is_dataclass(entry_type):
            raise TypeError(f"entry_type must be a @dataclass, got {entry_type!r}")
        field_names = {f.name for f in fields(entry_type)}
        for required in ("seq", "timestamp"):
            if required not in field_names:
                raise TypeError(
                    f"entry_type {entry_type.__name__} must declare a `{required}` field"
                )

        self._entry_type = entry_type
        self._entries: list[E] = []
        self._lock = threading.Lock()
        self._jsonl_path = jsonl_path
        self._jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        self._jsonl_path.write_text("")  # truncate — one ledger, one run

    def append(self, **entry_kwargs: Any) -> E:
        """Construct an entry (stamping seq + timestamp) and append it.

        Thread-safe. Flushes the row to JSONL before returning so the on-disk
        trail always reflects the in-memory list.
        """
        with self._lock:
            seq = len(self._entries)
            timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
            entry = self._entry_type(
                seq=seq,
                timestamp=timestamp,
                **entry_kwargs,
            )
            self._entries.append(entry)
            with self._jsonl_path.open("a") as f:
                f.write(json.dumps(asdict(entry)) + "\n")
            return entry

    def snapshot(self) -> list[E]:
        """Point-in-time copy of the entries. Safe to iterate outside the lock."""
        with self._lock:
            return list(self._entries)

    def dump_final(self, path: Path) -> None:
        """Write a consolidated JSON array of all entries for downstream consumers."""
        snap = self.snapshot()
        path.write_text(
            json.dumps([asdict(e) for e in snap], indent=2) + "\n"
        )

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


# -----------------------------------------------------------------------------
# Concrete entry type for the docs-validation sweep (experiment2).
# Other sweeps can define their own dataclass and pass it to AppendLedger.
# -----------------------------------------------------------------------------

DocsValidationStatus = Literal["FAIL", "UNVERIFIABLE", "UNCLEAR_PROSE"]


@dataclass(frozen=True)
class DocsValidationEntry:
    """One row in the docs-validation ledger.

    The ledger only records things a human (or the issue-cutter) might act on.
    Claims the validator verified as correct are NOT recorded — they're dead
    weight. The fact that the validator ran is tracked in validator_results.json.

    Status meanings:
      - FAIL         — claim checked against on-disk SDK source, it contradicts.
      - UNVERIFIABLE — claim's authoritative source is NOT in the corpus
                       (e.g. docs references a package outside sdk-python/
                       or sdk-typescript/). Pipeline can't check it. Not a
                       bug per se; a gap-in-corpus signal.
      - UNCLEAR_PROSE — page prose is hard to follow for a first-time reader.

    FAIL entries should populate source_file + source_lines + reason + suggested_fix.
    UNVERIFIABLE entries should populate reason naming what's missing from the
    corpus; source_file / source_lines stay null.
    UNCLEAR_PROSE entries cite the page itself and quote the confusing spans.
    """

    seq: int
    timestamp: str
    agent_id: str
    docs_page: str
    claim: str
    status: DocsValidationStatus
    doc_lines: list[int] = field(default_factory=list)
    source_file: str | None = None
    source_lines: list[int] | None = None
    reason: str | None = None
    suggested_fix: str | None = None
    quoted_excerpts: list[str] | None = None


def make_docs_validation_ledger(jsonl_path: Path) -> AppendLedger[DocsValidationEntry]:
    """Convenience constructor for the docs-validation-specific ledger."""
    return AppendLedger(jsonl_path, DocsValidationEntry)


def counts_by_status(
    ledger: AppendLedger[DocsValidationEntry],
) -> dict[str, int]:
    """Summary helper scoped to the docs-validation schema."""
    counts: dict[str, int] = {
        "FAIL": 0, "UNVERIFIABLE": 0, "UNCLEAR_PROSE": 0,
    }
    for e in ledger.snapshot():
        counts[e.status] = counts.get(e.status, 0) + 1
    return counts
