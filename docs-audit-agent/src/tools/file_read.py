"""`file_read` with a response-size guardrail.

`strands_tools.file_read` will happily expand a glob or traverse a directory in
`view` mode and return the concatenated contents of every match. A single bad
path can put millions of tokens into the conversation, blow the model's
context window, and fail the node irrecoverably.

This wrapper delegates to the real tool but caps the size of each text block
in the response. When a block would exceed the cap, it's truncated and
replaced with a concrete error message telling the agent how to recover
(`mode="find"` for discovery, `mode="lines"` for ranges, etc.).

The guard runs at the tool layer — it catches the failure mode regardless of
what the model passed in, so we don't have to trust an SOP rule to hold.
"""

from typing import Any

from strands.types.tools import ToolResult, ToolUse
from strands_tools import file_read as _file_read


MAX_BLOCK_CHARS = 200_000
"""Upper bound on a single text block in a file_read response.

At ~4 chars/token this is ~50K tokens — large enough for any individual
source file we care about, small enough that a bad glob can't compound
into a 2M-token blowup across a multi-block response.
"""

MAX_TOTAL_CHARS = 400_000
"""Upper bound on the sum of all text blocks in a single file_read response."""

TOOL_SPEC = _file_read.TOOL_SPEC


def _truncation_notice(original_chars: int, limit: int, path_hint: str = "") -> str:
    return (
        f"\n\n[TRUNCATED BY GUARD: response would have been {original_chars:,} "
        f"characters; limit is {limit:,}.{' Path: ' + path_hint if path_hint else ''} "
        f"Use mode='find' to enumerate matches without reading their contents, "
        f"or mode='lines' with start_line/end_line to read a specific range.]"
    )


def file_read(tool: ToolUse, **kwargs: Any) -> ToolResult:
    """Forward to `strands_tools.file_read` with a size cap on the response."""
    result = _file_read.file_read(tool, **kwargs)

    content = result.get("content") or []
    if not content:
        return result

    path_hint = ""
    try:
        path_hint = str(tool.get("input", {}).get("path", ""))
    except Exception:
        pass

    guarded_content: list[dict[str, Any]] = []
    running_total = 0
    total_truncated = False

    for block in content:
        if not isinstance(block, dict) or "text" not in block:
            guarded_content.append(block)
            continue

        text = block["text"]
        if not isinstance(text, str):
            guarded_content.append(block)
            continue

        if len(text) > MAX_BLOCK_CHARS:
            text = text[:MAX_BLOCK_CHARS] + _truncation_notice(
                len(block["text"]), MAX_BLOCK_CHARS, path_hint
            )

        remaining = MAX_TOTAL_CHARS - running_total
        if remaining <= 0:
            total_truncated = True
            break

        if len(text) > remaining:
            text = text[:remaining] + _truncation_notice(len(text), remaining, path_hint)
            total_truncated = True
            guarded_content.append({"text": text})
            running_total = MAX_TOTAL_CHARS
            break

        guarded_content.append({"text": text})
        running_total += len(text)

    if total_truncated:
        guarded_content.append(
            {
                "text": (
                    "\n\n[GUARD: further content omitted — response exceeded "
                    f"{MAX_TOTAL_CHARS:,} total characters. This usually means "
                    "the path matched many files. Narrow the path to a single "
                    "file or use mode='find' first.]"
                )
            }
        )

    return {
        "toolUseId": result.get("toolUseId", "default-id"),
        "status": result.get("status", "success"),
        "content": guarded_content,
    }
