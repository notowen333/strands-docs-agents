"""Enumerate the docs pages to validate in a sweep.

Scope: `.mdx` under `corpus/docs/src/content/docs/`. We exclude `.md` because
the Strands docs site's user-facing pages are all MDX; plain `.md` is usually
auto-generated or scaffolding. We also skip `node_modules`, build dirs, and
index redirect pages with no body content.
"""

from __future__ import annotations

from pathlib import Path

CONTENT_SUBPATH = "docs/src/content/docs"
EXCLUDE_DIR_NAMES = {"node_modules", ".build", "dist", "coverage", ".astro"}


def discover_docs_pages(corpus_dir: Path) -> list[Path]:
    """Return all `.mdx` files under `corpus_dir/docs/src/content/docs/`.

    Paths are absolute. Results are sorted for deterministic run ordering.
    """
    root = (corpus_dir / CONTENT_SUBPATH).resolve()
    if not root.exists():
        raise FileNotFoundError(
            f"Docs content root does not exist: {root}. "
            f"Expected corpus layout is <corpus>/{CONTENT_SUBPATH}/."
        )

    pages: list[Path] = []
    for path in root.rglob("*.mdx"):
        if any(part in EXCLUDE_DIR_NAMES for part in path.parts):
            continue
        pages.append(path.resolve())

    return sorted(pages)


def relative_to_content(corpus_dir: Path, page: Path) -> str:
    """Return a page path relative to the content root — the string form the
    ledger uses (e.g. `user-guide/concepts/plugins/skills.mdx`)."""
    root = (corpus_dir / CONTENT_SUBPATH).resolve()
    return str(page.resolve().relative_to(root))
