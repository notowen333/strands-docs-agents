"""Glob tool — enumerate filesystem paths by pattern(s).

Wraps Python's `glob.glob` with a bounded result set and structured
output. The agent supplies one glob pattern (via `pattern`) or a list
(via `patterns`); the tool returns matching file paths grouped by
pattern, with a `truncated` signal when results exceed the limit.

Supports multi-pattern enumeration: pass a list to `patterns` to run
all patterns in one tool call. Each returned file is attributed to the
pattern(s) that matched it.

Inspired by the standard glob pattern used across doc-agent tools.
"""

from __future__ import annotations

import time
from glob import glob as _glob
from pathlib import Path

from strands import tool


@tool(
    name="glob",
    description=(
        "Enumerate file paths by glob pattern. Pass `pattern` for a single "
        "glob OR `patterns` (a list) to batch many pattern lookups into "
        "one call. Use this to discover what exists under a directory "
        "without reading file contents. Supports `**` for recursive search. "
        "Returns up to `limit` file paths (default 100). Set `path` to "
        "scope the search; leave empty to search relative to the current "
        "working directory."
    ),
)
def glob_tool(
    pattern: str | None = None,
    patterns: list[str] | None = None,
    path: str | None = None,
    limit: int = 100,
) -> dict:
    """Return file paths matching one or more glob patterns under `path`.

    Args:
        pattern: A single glob pattern such as `**/*.mdx` or
            `src/content/docs/user-guide/**/*.ts`. Mutually exclusive
            with `patterns`.
        patterns: List of glob patterns. All patterns are enumerated in
            one tool call; the response's `files_per_pattern` dict maps
            each pattern to the list of files it matched.
        path: Directory to search relative to. Defaults to the current
            working directory. Must be absolute or resolvable.
        limit: Maximum number of unique paths to return. Defaults to 100.
            If more paths match, the response's `truncated` field is True
            and the agent should narrow the pattern.

    Returns:
        A dict with:
        - `filenames`: deduplicated list of matching file paths (absolute
          when the pattern reached outside the current working directory,
          relative otherwise)
        - `num_files`: number of unique paths returned (after applying limit)
        - `files_per_pattern`: dict mapping each pattern to the list of
          files it matched (same values as `filenames` but broken out)
        - `truncated`: True if the unfiltered unique-file count exceeded limit
        - `duration_ms`: wall clock for the glob call
    """
    start = time.time()

    if pattern is not None and patterns is not None:
        raise ValueError("Pass either `pattern` OR `patterns`, not both.")
    if pattern is None and not patterns:
        raise ValueError("Must provide `pattern` (single) or `patterns` (list).")
    pat_list: list[str] = [pattern] if pattern is not None else list(patterns or [])
    if not pat_list:
        raise ValueError("`patterns` must be a non-empty list.")

    root = Path(path).expanduser().resolve() if path else Path.cwd()

    if not root.exists():
        raise FileNotFoundError(f"Directory does not exist: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"Path is not a directory: {root}")

    cwd = Path.cwd()
    files_per_pattern: dict[str, list[str]] = {}
    all_files: set[str] = set()

    for pat in pat_list:
        matches = _glob(str(root / pat), recursive=True)
        pattern_files: list[str] = []
        for m in matches:
            p = Path(m).resolve()
            if not p.is_file():
                continue
            rel = (
                str(p.relative_to(cwd))
                if p.is_relative_to(cwd)
                else str(p)
            )
            pattern_files.append(rel)
            all_files.add(rel)
        pattern_files.sort()
        files_per_pattern[pat] = pattern_files

    filenames = sorted(all_files)
    truncated = len(filenames) > limit
    filenames = filenames[:limit]

    return {
        "filenames": filenames,
        "num_files": len(filenames),
        "files_per_pattern": files_per_pattern,
        "truncated": truncated,
        "duration_ms": round((time.time() - start) * 1000),
    }
