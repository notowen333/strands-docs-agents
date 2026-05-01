"""Compose a per-run system prompt from a task-kind preamble + the shared body.

The main-agent system prompt is assembled at runtime:

    preamble  = final_doc_agent3/prompt_preambles/{task_kind}.md
    body      = final_doc_agent3/prompt_body.md
    prompt    = preamble + body, with {corpus_dir} substituted to an absolute path.

Three task kinds: "pr", "issue", "comments". Each has its own preamble with
the phases-to-run declaration; the body is identical across all three.

Unlike v1, explore does not write a fact pack to disk — its findings live in
the main conversation for downstream phases to reference. A future session
manager will be responsible for cross-run persistence.
"""

from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).parent
PREAMBLES = HERE / "prompt_preambles"
BODY_PATH = HERE / "prompt_body.md"


def build_main_prompt(task_kind: str, corpus_dir: Path) -> str:
    """Return the fully-composed system prompt for the main agent.

    Parameters:
        task_kind: "pr" | "issue" | "comments".
        corpus_dir: absolute path to the corpus root.
    """
    if task_kind not in ("pr", "issue", "comments"):
        raise ValueError(
            f"task_kind must be pr|issue|comments, got {task_kind!r}"
        )

    preamble = (PREAMBLES / f"{task_kind}.md").read_text()
    body = BODY_PATH.read_text()

    composed = preamble + "\n" + body
    return composed.replace("{corpus_dir}", str(corpus_dir))
