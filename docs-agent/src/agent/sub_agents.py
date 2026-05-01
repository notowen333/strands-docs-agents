"""Load sub-agents (audit, ui-tester) from disk and wire them as tools.

Each sub-agent lives in `sub_agents/<name>/`:
  - system_prompt.md — the agent's system prompt (pure prose, no frontmatter)
  - meta.yaml         — { name: str, description: str } — name is the tool
                        identifier the main agent sees; description is what
                        the main agent reads to decide when to invoke.

This module deliberately does NOT use `Skill.from_file` — these are sub-agent
system prompts, not skills. `SKILL.md` was a loader-convenience misnomer in
earlier iterations; `system_prompt.md` + `meta.yaml` says what it is.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from strands import Agent

from doc_agent.directive import with_directive
from doc_agent.model import create_model_1m
from doc_agent.tools import file_read
from doc_agent.tools.glob_tool import glob_tool
from doc_agent.tools.grep_tool import grep_tool
from doc_agent.tools.shell_tool import shell_tool

HERE = Path(__file__).parent
SUB_AGENTS_DIR = HERE / "sub_agents"


def _load_sub_agent_parts(sub_agent_name: str) -> tuple[str, str, str]:
    """Return (name, description, system_prompt_body) for a sub-agent directory."""
    dir_ = SUB_AGENTS_DIR / sub_agent_name
    meta = yaml.safe_load((dir_ / "meta.yaml").read_text())
    body = (dir_ / "system_prompt.md").read_text()
    return meta["name"], meta["description"], body


def create_audit_agent(corpus_dir: Path) -> Agent:
    """Audit sub-agent — fresh-context cross-SDK + coverage + consistency checks.

    Invoked once by the main agent after write + npm validation finish. The
    main agent feeds audit's findings into a single in-loop fix pass (no
    sub-agent for that — it's just more turns in the main agent).
    """
    name, description, body = _load_sub_agent_parts("audit")
    prompt = with_directive(body.replace("{corpus_dir}", str(corpus_dir)))

    return Agent(
        name=name,
        description=description,
        model=create_model_1m(),
        system_prompt=prompt,
        tools=[file_read, glob_tool, grep_tool, shell_tool],
    )


def create_ui_tester_agent(corpus_dir: Path, playwright_tool) -> Agent:
    """UI-tester sub-agent — spins a dev server + Playwright to visually verify pages.

    Kept in a sub-agent because MCP output (snapshots, screenshots, console)
    is bulky and the main agent doesn't need the raw material afterwards.
    """
    name, description, body = _load_sub_agent_parts("ui-tester")
    prompt = with_directive(body.replace("{corpus_dir}", str(corpus_dir)))

    tools = [file_read, shell_tool]
    if playwright_tool is not None:
        tools.append(playwright_tool)

    return Agent(
        name=name,
        description=description,
        model=create_model_1m(),
        system_prompt=prompt,
        tools=tools,
    )
