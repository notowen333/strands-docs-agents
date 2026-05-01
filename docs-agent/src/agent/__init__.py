"""Final doc-agent (v2) — three entry points (PR / issue / comments) over one shared pipeline.

Variant of `final_doc_agent` with no on-disk fact pack. Explore emits its
observations into the main conversation; downstream phases reference them
from scrollback. Cross-run persistence is deferred to a future session
manager.

Usage:
    from doc_agent.final_doc_agent3 import run
    output = run(task)  # task is a TaskContext from doc_agent.task
"""

from doc_agent.final_doc_agent3.runner import run

__all__ = ["run"]
