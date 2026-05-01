"""Entry point for the final doc-agent pipeline.

One main agent runs explore + doc-writer + npm validation + (audit ->
single fix pass) in one loop. The audit sub-agent is the only fresh-context
verifier — it runs once after the first write, its findings become a single
in-loop fix pass, then the run ends. No parallel trio, no refiner sub-agent,
no multi-pass fix loop.

Entry: `run(task)` takes a TaskContext, returns the main agent's final output string.
"""

from __future__ import annotations

import os
from pathlib import Path

from mcp import StdioServerParameters, stdio_client
from strands import Agent
from strands.tools.mcp import MCPClient
from strands.vended_plugins.skills import AgentSkills
from strands_tools import file_write

from doc_agent.final_doc_agent3.prompt_builder import build_main_prompt
from doc_agent.final_doc_agent3.sub_agents import (
    create_audit_agent,
    create_ui_tester_agent,
)
from doc_agent.model import create_model_1m
from doc_agent.task import TaskContext
from doc_agent.tools import file_read
from doc_agent.tools.glob_tool import glob_tool
from doc_agent.tools.grep_tool import grep_tool
from doc_agent.tools.shell_tool import shell_tool

HERE = Path(__file__).parent
SKILLS_DIR = HERE / "skills"


def _playwright_mcp_client() -> MCPClient:
    """Start Playwright MCP for the ui-tester sub-agent."""
    screenshot_dir = Path.cwd() / "screenshots"
    screenshot_dir.mkdir(exist_ok=True)
    return MCPClient(
        lambda: stdio_client(
            StdioServerParameters(
                command="npx",
                args=[
                    "@playwright/mcp@latest",
                    "--headless",
                    "--caps=vision",
                    f"--outputDir={screenshot_dir}",
                ],
            )
        ),
        startup_timeout=60,
    )


def _build_main_agent(
    task: TaskContext,
    corpus_dir: Path,
    include_ui_tester: bool,
) -> Agent:
    """Wire the main agent with its preamble+body prompt, skills, and sub-agent tools."""
    system_prompt = build_main_prompt(task.kind, corpus_dir)

    # Sub-agents — only audit is wired in. Validator and refiner used to be
    # separate sub-agents that ran in parallel; now the main agent runs npm
    # checks directly (faster) and self-applies mechanical presentation rules
    # in its §9 self-check (catches the same class of issues refiner flagged
    # without the round-trip). Audit stays as a fresh-context sub-agent
    # because factual verification benefits from independence.
    audit_agent = create_audit_agent(corpus_dir)
    tools = [
        file_read,
        file_write,
        glob_tool,
        grep_tool,
        shell_tool,
        audit_agent.as_tool(),
    ]

    if include_ui_tester:
        playwright = _playwright_mcp_client()
        ui_tester_agent = create_ui_tester_agent(corpus_dir, playwright)
        tools.append(ui_tester_agent.as_tool())

    # Skills — dynamically invocable by the main agent based on their
    # description triggers. contextualize-issue fires on INPUT TYPE: issue;
    # contextualize-comments fires on INPUT TYPE: comments.
    skills_plugin = AgentSkills(skills=SKILLS_DIR)

    return Agent(
        name=f"doc_agent_{task.kind}",
        model=create_model_1m(),
        system_prompt=system_prompt,
        tools=tools,
        plugins=[skills_plugin],
    )


def _log_usage(label: str, result) -> None:
    metrics = getattr(result, "metrics", None)
    usage = getattr(metrics, "accumulated_usage", {}) if metrics else {}
    usage = usage or {}
    i = usage.get("inputTokens", 0) or 0
    o = usage.get("outputTokens", 0) or 0
    t = usage.get("totalTokens", 0) or 0
    print(
        f"=== [doc-agent/{label}] cumulative=in={i:,} out={o:,} total={t:,} ===",
        flush=True,
    )


def run(task: TaskContext) -> str:
    """Run the doc-agent pipeline end-to-end for the given task.

    Respects SKIP_UI_TEST env var. Returns the main agent's final output string.

    No safety-net re-prompt: the preamble caps the in-loop fix pass at 1 so
    wall-clock time is bounded. If findings remain after that single pass the
    agent emits `outcome: has_unresolved_findings` and a human reviewer
    resolves the rest.
    """
    corpus_dir = _resolve_corpus_dir()
    include_ui_tester = not os.environ.get("SKIP_UI_TEST")

    agent = _build_main_agent(task, corpus_dir, include_ui_tester=include_ui_tester)

    print(
        f"\n=== [doc-agent/{task.kind}] starting "
        f"{task.repo}#{task.number} ===",
        flush=True,
    )
    result = agent(task.as_prompt())
    output = str(result)
    _log_usage(f"{task.kind}/pass-1", result)

    print(f"\n=== [doc-agent/{task.kind}] done ===\n", flush=True)
    return output


def _resolve_corpus_dir() -> Path:
    """Locate the corpus root from env or the default project path."""
    from doc_agent.corpus import CORPUS_DIR, ensure_corpus

    ensure_corpus()
    return CORPUS_DIR
