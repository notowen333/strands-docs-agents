---
name: contextualize-issue
description: Hydrate a GitHub issue into a complete context blob before exploration begins. Follows links (referenced issues, PRs, commits, gists), pulls comment threads, resolves ambiguous scope cues, and produces a consolidated "what this issue is actually asking for" brief. Run this as the first action when the input is an issue; skip entirely for PRs.
---

# Contextualize Issue

## Purpose

An issue is unstructured natural language. Before explore can resolve symbols, doc-writer can choose scope, or the auditor can verify anything, we need to know **what the issue is actually asking for** and **what its references point to**.

This skill runs in the main conversation (not as a sub-agent). The hydrated context stays in context for every downstream skill to read.

You are an **interpreter of intent**, not a planner of changes. Record what the issue asks for and what the referenced material contains. Don't decide which docs pages to touch — that's doc-writer's call later.

## When to run

- **Run this skill first** if the task prompt begins with `INPUT TYPE: issue`.
- **Skip this skill entirely** if `INPUT TYPE: pr`. A PR diff is already structured scope; there's nothing to hydrate.

Invoke before explore. Explore's fact-gathering depends on knowing which symbols / concepts / docs pages are in scope, which in turn depends on this skill's output.

## Parameters

- **corpus_dir** (required): Absolute path to the corpus root.
- **issue_repo** (required): The `<org>/<repo>` the issue belongs to (e.g. `strands-agents/docs`).
- **issue_number** (required): The issue's `#N`.

## Source of truth

- **The issue body + comments are the primary source** for what the user wants.
- **Linked PRs / commits / issues are factual context** — what shipped, what was discussed, what's related.
- **On-disk SDK source and docs are the ground truth** for anything the issue references.
- If the issue says something that contradicts on-disk source (e.g. "update the docs about `MyClass`" but `MyClass` doesn't exist on disk), record the contradiction in `open_questions`. Don't guess.

## Steps

### 1. Pull the full issue

Use `github_tools` to fetch the complete issue:

- Body
- All comments (in order, with author and timestamp)
- Labels
- Current state (open/closed) and any linked PRs (via GitHub's issue-PR linkage, not just URLs in the text)

Some of this may already be in the task prompt. Re-fetch to catch anything the task loader didn't include (especially comments added after the task was queued).

### 2. Extract every reference from the body and comments

Walk the body and every comment. Record every instance of:

- **GitHub issue / PR links** — `#123`, `owner/repo#123`, or full URLs
- **Commit hashes** — full or shortened, with or without repo prefix
- **File paths** — anything that looks like a path (`src/foo/bar.py`, `docs/src/content/docs/...`)
- **Symbols and identifiers** — class / function / method / package names
- **Gists, external docs, blog posts, tickets** — any URL

For each reference, you'll fetch or note its content in step 3.

### 3. Hydrate each reference

For each reference from step 2:

- **GitHub issue / PR links** — use `github_tools` to fetch title + body + a summary of comments (don't paste whole comment threads; summarize them). For PRs, also include whether they're merged and a short description of what shipped (you can pull the diff if it's small, or summarize from the PR body).
- **Commit hashes** — use `github_tools` to fetch the commit message and the list of files changed. Don't paste full diffs unless small.
- **File paths** — check whether the path exists under `{corpus_dir}`. If yes, note where. If no, flag as `open_questions` ("file X referenced but not found on disk").
- **Symbols** — check whether the identifier exists in `{corpus_dir}/sdk-python/` or `{corpus_dir}/sdk-typescript/` via `grep`. Note locations; don't deep-read definitions (that's explore's job). Just confirm it exists and note where.
- **External URLs** — record the URL and title as-is. Don't fetch arbitrary URLs — they may be unstable, slow, or unrelated. If the issue author cites a blog post, the title + URL is enough context.

### 4. Reconcile the ask

After hydration, write a structured brief. This is the skill's **primary output** — everything downstream reads it.

Required sections:

- **Stated ask** — 1–3 sentences, restating what the issue is literally requesting. Use the issue's own phrasing where possible; don't paraphrase into what you think they meant.
- **Interpreted scope** — 1–3 sentences on what concrete work this seems to entail given the hydrated references. Be honest if the scope is still ambiguous; say so.
- **In-scope areas** — a list of docs pages, SDK modules, or concept areas the issue appears to concern, based on references and stated ask. No action plan; just "these areas look relevant to look at next."
- **Out-of-scope clarifications** — if the issue explicitly says "don't touch X" or "unrelated to Y", record that.
- **Open questions** — anything materially ambiguous: contradictions between what the issue says and what's on disk, references that didn't resolve, scope cues that conflict across comments, author asks that can't be answered without a human.

### 5. Emit the brief

Write the brief as markdown in your final message using this structure:

```markdown
## Issue context — strands-agents/<repo>#<N>: <title>

**URL:** <issue url>
**Labels:** <labels>
**State:** <open|closed> <any linked PR info>

### Stated ask

<verbatim or near-verbatim>

### Interpreted scope

<your read>

### In-scope areas

- <docs page / module / concept> — <one line on why it's relevant>
- ...

### Out-of-scope

- <anything explicitly deferred>

### Referenced material

#### Linked issues / PRs
- #<N> (<state>): <title> — <one-line summary of what matters from it>
- ...

#### Linked commits
- <hash>: <message> — touched <N> files <under path prefix if relevant>

#### External references
- [<title>](<url>)

#### Symbols / paths confirmed on disk
- `<symbol>` — sdk-python/… / sdk-typescript/…
- `<path>` — exists at <relative path>

### Open questions

- <contradictions, unresolved references, human-needed clarifications>
```

End your message after the brief. Do not proceed to explore or doc-writing — those are separate skills.

## Tool usage

- `github_tools` — fetch issue / PR / commit content. Use for everything GitHub-side.
- `grep` — confirm symbols / paths exist on disk. Narrow (`fixed=True, word=True`), don't fan out.
- `glob` — optional; use if you need to enumerate files matching a path hint before grep.
- `file_read mode="view"` — use sparingly; only for small files the issue references directly. Explore is the heavy reader.

## What NOT to do

- **Don't plan docs changes.** No "should add X", no "we need to update Y". That's doc-writer's call after explore.
- **Don't fetch full PR diffs** unless the PR is small (< ~200 lines changed). Summarize from the PR body and commit messages.
- **Don't read SDK source files to characterize symbols.** Explore does that in the next phase. You're just confirming the symbol exists and noting where.
- **Don't chase every link recursively.** Hydrate one hop from the issue body + comments. If a linked PR mentions another PR, don't hydrate that second PR unless it's materially load-bearing for the ask.
- **Don't fetch arbitrary external URLs.** Title + URL is enough; explore / doc-writer can navigate further if needed.

## Troubleshooting

### The issue is too vague to brief
Write what you can. Put the ambiguity in `Open questions` with enough detail that a human could resolve it. Explore will see the open questions; if they materially block work, it'll flag them too and doc-writer will surface them in its final output.

### A referenced PR is huge
Don't pull the diff. Use the PR title, description, and commit messages to summarize what shipped. Explore will pull the diff if it actually needs to inspect changes.

### The issue contradicts on-disk source
Record both in `Open questions`. Don't try to resolve the contradiction yourself — explore and doc-writer will decide whose word to trust (usually disk).

### A comment adds scope after the original body
Comments supersede the body when they narrow or clarify scope. Record the most recent authoritative framing as the `Stated ask`; keep older framings visible in `Open questions` if they conflict with it.
