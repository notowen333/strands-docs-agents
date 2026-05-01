"""Run-level directive injected into every agent's system prompt.

Set RUN_DIRECTIVE in the environment to pipe a short override through all
agents. Use this to correct a specific assumption ("XYZ was renamed to ZZZ")
without editing each agent's prompt.
"""

import os


def get_directive() -> str:
    return os.environ.get("RUN_DIRECTIVE", "").strip()


def with_directive(system_prompt: str) -> str:
    """Prepend a prominent directive block to a system prompt, if one is set."""
    directive = get_directive()
    if not directive:
        return system_prompt

    return (
        "## ⚠ RUN DIRECTIVE — highest priority, overrides any conflicting "
        "guidance below\n\n"
        f"{directive}\n\n"
        "---\n\n"
        + system_prompt
    )
