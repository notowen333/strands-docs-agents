"""CLI: load the issue-cutter skill into a one-shot agent and file GH issues.

Usage:
    python -m doc_agent.experiment2.cli_cut_issues <ledger.final.json> \
        [--repo strands-agents/docs] [--dry-run]

This is a deliberately-separate entrypoint from `run.py`. The validator sweep
(run.py) and the issue-filing phase (this script) are independent so you can
iterate on the sweep without paying the filing cost.

The agent loads the issue-cutter skill via AgentSkills. Its tools are the
minimum needed to read the ledger and talk to GitHub: `file_read` + `shell_tool`
(for `gh` CLI calls). When `github_tools` from `doc_agent.tools` is populated,
it's added to the toolset too.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from strands import Agent
from strands.vended_plugins.skills import AgentSkills

from doc_agent.model import create_model_1m
from doc_agent.tools import file_read
from doc_agent.tools.shell_tool import shell_tool

try:
    from doc_agent.tools.github_tools import github_tools  # type: ignore
except ImportError:
    github_tools = None

HERE = Path(__file__).parent
SKILLS_DIR = HERE / "skills"


def _build_agent() -> Agent:
    tools = [file_read, shell_tool]
    if github_tools is not None:
        tools.append(github_tools)

    return Agent(
        name="issue_cutter",
        model=create_model_1m(),
        system_prompt=(
            "You are the issue-cutter for the experiment2 docs-validation "
            "sweep. Your full procedure is in the `issue-cutter` skill; "
            "invoke that skill before doing anything else."
        ),
        tools=tools,
        plugins=[AgentSkills(skills=SKILLS_DIR)],
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="File GH issues from a finalized experiment2 ledger."
    )
    parser.add_argument(
        "ledger",
        type=Path,
        help="Path to ledger.final.json produced by `run.py`.",
    )
    parser.add_argument(
        "--repo",
        default="strands-agents/docs",
        help="Target repo for issue filing.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print proposed issues without creating them.",
    )
    args = parser.parse_args()

    if not args.ledger.exists():
        parser.error(f"Ledger not found: {args.ledger}")

    agent = _build_agent()
    dry_run_marker = "DRY_RUN=true\n" if args.dry_run else ""
    task_prompt = (
        f"{dry_run_marker}"
        f"Final ledger at: `{args.ledger}`\n"
        f"Target repo: `{args.repo}`\n\n"
        f"Invoke the `issue-cutter` skill. Read the ledger, group findings, "
        f"dedupe against existing issues in `{args.repo}`, and file new "
        f"issues for each non-duplicate group. End with the `=== RUN "
        f"SUMMARY ===` block."
    )
    result = agent(task_prompt)
    output = str(result)

    # Persist the summary alongside the ledger for traceability.
    summary_path = args.ledger.with_name("issue_cutter_summary.md")
    summary_path.write_text(output + "\n")
    print(f"\nsummary written to {summary_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
