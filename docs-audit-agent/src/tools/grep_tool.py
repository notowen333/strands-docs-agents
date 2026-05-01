"""Grep tool — search file contents by pattern(s) with structured output.

Wraps `ripgrep` (`rg`) via subprocess. Returns structured match records
(file, line number, matched text, optional context) as a list — no
prose parsing required on the agent side. Bounded result count with a
`truncated` signal when the match count exceeds the limit.

Use this instead of `file_read mode="search"` when you want
greppable line-level matches across many files.

Supports multi-pattern search: pass a list to `patterns` to run all
patterns in one ripgrep invocation (ripgrep combines them via `-e`).
Each match record includes which pattern matched, so the agent can
issue N separate symbol lookups in a single tool call rather than
serializing N tool roundtrips.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from pathlib import Path

from strands import tool


@tool(
    name="grep",
    description=(
        "Search file contents for one or more patterns. Returns structured "
        "match records (file, line, text, pattern) — faster and cheaper "
        "than reading files. Pass `pattern` for a single search OR "
        "`patterns` (a list) to batch many symbol lookups into one call. "
        "Use `fixed=True` for literal strings (no regex escaping). "
        "Supports `word=True` for word-boundary matching, `include` glob "
        "filtering, and `context_lines` for surrounding lines."
    ),
)
def grep_tool(
    pattern: str | None = None,
    patterns: list[str] | None = None,
    path: str | None = None,
    fixed: bool = False,
    word: bool = False,
    case_insensitive: bool = False,
    include: str | None = None,
    context_lines: int = 0,
    limit: int = 200,
) -> dict:
    """Run ripgrep for one or more patterns under `path` and return structured matches.

    Args:
        pattern: Single search pattern. Regex unless `fixed=True`.
            Mutually exclusive with `patterns`.
        patterns: List of search patterns. All patterns are run in one
            ripgrep invocation; each match record carries the pattern
            that matched it. Use this to batch symbol lookups.
        path: Directory (or single file) to search. Defaults to cwd.
        fixed: Treat `pattern`/`patterns` as literal strings (no regex escapes).
        word: Match only whole words (`--word-regexp`).
        case_insensitive: Case-insensitive search.
        include: Glob filter for which file paths to search (e.g.
            `*.mdx`). Passes to ripgrep's `--glob` flag.
        context_lines: Include N lines of context before and after each
            match.
        limit: Cap on total match records returned. If more matches
            exist, the response's `truncated` field is True.

    Returns:
        A dict with:
        - `matches`: list of `{file, line, text, pattern, before, after}` records
        - `num_matches`: number of matches returned
        - `files_with_matches`: deduplicated list of matching file paths
        - `matches_per_pattern`: dict mapping each pattern to its match count
        - `truncated`: True if the match count exceeded limit
        - `duration_ms`: wall clock for the ripgrep call
    """
    start = time.time()

    # Normalize inputs: exactly one of `pattern` / `patterns` must be provided.
    if pattern is not None and patterns is not None:
        raise ValueError("Pass either `pattern` OR `patterns`, not both.")
    if pattern is None and not patterns:
        raise ValueError("Must provide `pattern` (single) or `patterns` (list).")
    pat_list: list[str] = [pattern] if pattern is not None else list(patterns or [])
    if not pat_list:
        raise ValueError("`patterns` must be a non-empty list.")

    if not shutil.which("rg"):
        raise RuntimeError(
            "ripgrep (`rg`) is not installed. Install via `brew install "
            "ripgrep` or `apt install ripgrep`."
        )

    root = Path(path).expanduser().resolve() if path else Path.cwd()
    if not root.exists():
        raise FileNotFoundError(f"Path does not exist: {root}")

    cmd = ["rg", "--json"]
    if fixed:
        cmd.append("--fixed-strings")
    if word:
        cmd.append("--word-regexp")
    if case_insensitive:
        cmd.append("--ignore-case")
    if context_lines > 0:
        cmd.extend(["--context", str(context_lines)])
    if include:
        cmd.extend(["--glob", include])
    cmd.extend(["--max-count", str(limit + 1)])

    # Pass every pattern via -e so ripgrep ORs them. ripgrep reports
    # matches with the combined pattern; we recover per-pattern attribution
    # ourselves by re-testing each match's text below.
    for p in pat_list:
        cmd.extend(["-e", p])
    cmd.extend(["--", str(root)])

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        raise TimeoutError(
            f"ripgrep timed out after 30s for patterns {pat_list!r}"
        )

    # rg exits 1 when no matches; anything higher is a real error.
    if proc.returncode not in (0, 1):
        raise RuntimeError(
            f"ripgrep failed (exit {proc.returncode}): {proc.stderr.strip()}"
        )

    # Precompile matchers for per-pattern attribution. Mirror the flags
    # ripgrep is using. For fixed + word we build a word-boundary literal.
    def _match_tester(p: str):
        if fixed:
            needle = re.escape(p)
            if word:
                needle = rf"\b{needle}\b"
        else:
            needle = rf"\b(?:{p})\b" if word else p
        flags = re.IGNORECASE if case_insensitive else 0
        return re.compile(needle, flags)

    compiled = [(p, _match_tester(p)) for p in pat_list]

    def _attribute(text: str) -> str | None:
        """Return the first pattern that matches `text`, or None."""
        for p, rx in compiled:
            if rx.search(text):
                return p
        return None

    matches: list[dict] = []
    files_with_matches: set[str] = set()
    matches_per_pattern: dict[str, int] = {p: 0 for p in pat_list}
    cwd = Path.cwd()
    pending_before: list[str] = []

    for raw_line in proc.stdout.splitlines():
        if not raw_line.strip():
            continue
        try:
            evt = json.loads(raw_line)
        except json.JSONDecodeError:
            continue

        kind = evt.get("type")
        data = evt.get("data", {}) or {}

        if kind == "context":
            text = (data.get("lines", {}) or {}).get("text", "")
            pending_before.append(text.rstrip("\n"))
            if len(pending_before) > max(context_lines, 0):
                pending_before = pending_before[-context_lines:]
            continue

        if kind != "match":
            continue

        file_path_obj = Path((data.get("path") or {}).get("text", ""))
        try:
            resolved = file_path_obj.resolve()
            file_path = (
                str(resolved.relative_to(cwd))
                if resolved.is_relative_to(cwd)
                else str(resolved)
            )
        except OSError:
            file_path = str(file_path_obj)

        files_with_matches.add(file_path)

        line_no = data.get("line_number")
        text = (data.get("lines", {}) or {}).get("text", "").rstrip("\n")
        matched_pattern = _attribute(text) or pat_list[0]
        matches_per_pattern[matched_pattern] = (
            matches_per_pattern.get(matched_pattern, 0) + 1
        )

        matches.append(
            {
                "file": file_path,
                "line": line_no,
                "text": text,
                "pattern": matched_pattern,
                "before": list(pending_before) if context_lines > 0 else [],
                "after": [],
            }
        )
        pending_before = []

        if len(matches) > limit:
            break

    truncated = len(matches) > limit
    matches = matches[:limit]

    return {
        "matches": matches,
        "num_matches": len(matches),
        "files_with_matches": sorted(files_with_matches),
        "matches_per_pattern": matches_per_pattern,
        "truncated": truncated,
        "duration_ms": round((time.time() - start) * 1000),
    }
