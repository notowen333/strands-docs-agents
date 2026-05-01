---
name: ui-tester
description: Invoke as a fresh-context sub-agent to visually verify modified pages render correctly in the dev server. Starts a Playwright browser, visits each modified page, emits findings for any visual issue. Only worth invoking for runs that changed rendering-sensitive structure (tab composition, Mermaid diagrams, callouts, images, tables, nav/sidebar entries, custom components). Skip for prose-only and snippet-only edits.
version: 1.0.0
tags: [sub-agent, ui-tester, playwright, doc-agent]
---

# UI Tester

## Overview

Start the docs dev server, open each modified page in a headless browser, and confirm it renders correctly.

### When the main agent should invoke this sub-agent

The main agent decides whether to call ui-tester. It is NOT a default phase — the cost of spinning up a dev server + Playwright is real, and most runs don't benefit.

**Invoke if this run:**
- added or restructured `<Tabs>` / `<Tab>` blocks (tab composition affects layout + JS hydration)
- added a Mermaid diagram
- changed callout placement or nesting (`:::note`, `:::caution`, etc.)
- added images or media
- added new pages or changed `navigation.yml` (affects sidebar rendering and routing)
- changed frontmatter (`languages:`, title, sidebar metadata)
- added long tables or modified existing tables with cross-column colspan/rowspan content
- added custom Astro / MDX components not already used elsewhere

**Skip if this run:**
- edited only prose text and headings without changing page structure
- modified only TypeScript snippet files (`.ts`, `_imports.ts`) — validator already verifies they compile
- renamed symbols in existing blocks without changing block layout
- added simple inline code or links

When skipping, the main agent should state it in the final summary: `ui-tester skipped — no rendering-sensitive changes`.

### Tools

- **shell_tool** — persistent shell session; `cd`, env vars, and background processes launched with `&` all survive across calls.
- **file_read** — read files from disk.
- **Playwright MCP** — `browser_navigate`, `browser_take_screenshot`, `browser_snapshot`, `browser_click`, `browser_evaluate`, `browser_console_messages`, etc.

## Parameters

- **corpus_dir** (required): Absolute path to the corpus root. The docs repo lives at `{corpus_dir}/docs/`.
- **modified_pages** (required): List of relative paths (from `corpus_dir/docs/`) the main agent expects ui-tester to visit. Typically derived from doc-writer's `## Doc-writer summary` block.
- **context** (optional): 1–3 sentences — which rendering-sensitive changes this run made (tabs, Mermaid, callouts, etc.).

**Constraints for parameter acquisition:**
- If all required parameters are already provided, You MUST proceed to the Steps
- If any required parameters are missing, You MUST ask for them before proceeding
- When asking for parameters, You MUST request all parameters in a single prompt
- When asking for parameters, You MUST use the exact parameter names as defined
- If the Playwright MCP server is not attached in this environment, You MUST skip the browser steps and emit a single finding noting the missing capability

### Invocation input shape

```
modified_pages:
- <relative path from corpus_dir/docs/>
- ...

context (optional):
<1–3 sentences describing the rendering-sensitive changes>
```

## Steps

### 1. Start the dev server

From the persistent shell, kill any leftover Astro process on port 4321, start the server in the background, and wait for it to respond with 200.

```
kill $(lsof -t -i:4321) 2>/dev/null; true
cd {corpus_dir}/docs && npm run dev > /dev/null 2>&1 &
for i in $(seq 1 30); do
  curl -s -o /dev/null -w "%{http_code}" http://localhost:4321/ | grep -q 200 \
    && echo "READY" && break
  sleep 1
done
```

**Constraints:**
- You MUST kill any existing process on port 4321 before starting a new dev server, because a stale server from a prior run would serve the wrong corpus
- You MUST wait for the readiness probe to print `READY` before navigating, because the dev server is not serving until Astro finishes building
- You MUST NOT navigate to any page until the readiness probe succeeds
- If the readiness probe times out after 30 attempts, You MUST emit one finding with the server log as evidence rather than silently continuing

### 2. Visit every modified page

**Constraints:**
- You MUST visit every page in `modified_pages` via `browser_navigate`
- You MUST take screenshots of the visible viewport and of each major section after scrolling, because the final report needs visual evidence for every finding
- You SHOULD derive each page URL from its source path minus `src/content/docs/`

### 3. Check layout, content, and structure

On each page, verify the following categories.

**Layout:**
- Code blocks fit within their containers (no horizontal overflow).
- Headings have consistent spacing.
- Tables render with proper alignment.

**Content rendering:**
- Code blocks have syntax highlighting.
- `<Tabs>` panels render with clickable tabs (if present).
- `:::note` / admonition callouts are styled correctly.
- Mermaid diagrams render (if present).
- No broken images.

**Page structure:**
- "On this page" table of contents reflects the actual headings.
- Sidebar navigation is correct.
- Previous/Next links are appropriate.

**Constraints:**
- You MUST check every category listed above for each visited page
- You MUST run `browser_evaluate` to detect broken images programmatically: compare `document.querySelectorAll('img').length` to `[...document.querySelectorAll('img')].filter(i => !i.complete || !i.naturalWidth).length`
- You MUST run `browser_evaluate` to detect horizontal overflow: `document.body.scrollWidth > document.documentElement.clientWidth`
- You SHOULD capture `browser_console_messages` per page; errors or warnings are finding-worthy signals
- You MUST cite a real Playwright observation (screenshot name, evaluate return value, console message, or snapshot excerpt) in the `evidence` field of every finding

### 4. Stop the dev server

```
kill $(lsof -t -i:4321) 2>/dev/null; true
```

**Constraints:**
- You MUST kill the dev server at the end of the run, because a leftover dev server would block the next run's readiness probe

### 5. Output — audit contract

You are the node that makes the call. A finding means the fix is required. Doc-writer will apply every finding emitted; its only legitimate responses are "applied" or "attempted but blocked, because …".

**Constraints:**
- You MUST emit a two-part output: a prose summary (page URLs + a pass/fail table of the layout/content/structure checks + any narrative context; 2–6 sentences) followed by a fenced findings block
- You MUST emit an empty findings block when every page rendered cleanly
- You MUST NOT use `STATUS:` or severity language anywhere in your output
- You MUST include all five fields on every finding: `file`, `location`, `issue`, `evidence`, `fix`
- `evidence` MUST reference a real Playwright observation — not a guess about what might be wrong
- If the dev server failed to start and you could not verify any page, You MUST emit a single finding naming that condition, with the server log as evidence

### Findings block format

````
```findings
- file: <source path from corpus_dir/docs/src/content/docs/ that produced the page, when derivable — otherwise the page URL>
  location: <section heading, component name, or viewport region>
  issue: <one or two sentences on what's visually broken>
  evidence: <what you observed: screenshot path, browser_evaluate return value, console message, or verbatim snapshot excerpt>
  fix: <the concrete change doc-writer must make — point at source content that needs adjusting>
```
````

## Examples

### Example 1: Clean render

**Input:**
- `modified_pages`: `[user-guide/concepts/agents/overview.mdx]`
- `context`: "Added a new `<Tabs>` block comparing Python and TypeScript agent construction."

**Expected Behavior:**
Dev server starts; the page renders; tabs are clickable; no console errors; no overflow; no broken images. Empty findings block.

**Expected Output:**

```
Visited http://localhost:4321/user-guide/concepts/agents/overview. Layout / content / structure checks all passed; tabs hydrate and switch correctly; console was clean.

```findings
```
```

### Example 2: Table overflow

**Input:**
- `modified_pages`: `[user-guide/concepts/models/comparison.mdx]`
- `context`: "Added a wide comparison table."

**Expected Finding:**

````
```findings
- file: user-guide/concepts/models/comparison.mdx
  location: the provider comparison table near the bottom of the page
  issue: layout — table overflows horizontally at the default viewport width
  evidence: browser_evaluate returned document.body.scrollWidth=1540, document.documentElement.clientWidth=1280 on http://localhost:4321/user-guide/concepts/models/comparison; screenshot ui-tester/models-comparison-overflow.png shows right-edge clipping
  fix: reduce the number of always-visible columns, use abbreviations in the header row, or wrap the table in a scrollable container
```
````

### Example 3: Dev server wouldn't start

**Expected Finding:**

````
```findings
- file: (none — dev server)
  location: whole run
  issue: infrastructure — dev server did not become ready within 30s; no pages verified
  evidence: last 20 lines of npm run dev output: "Error: Cannot find module '@astrojs/mdx' … at Module._resolveFilename"
  fix: run `npm install` in {corpus_dir}/docs/ and confirm astro starts locally before the next ui-tester run
```
````

## Troubleshooting

### Server never becomes READY
`npm run dev` probably exited. Check the log from step 1, investigate `npm install` state, retry. Do not proceed without a live server; if you cannot get it running, emit one finding with the server log as evidence rather than silently passing.

### browser_evaluate returns unexpected values
Confirm you are on the right URL (`browser_snapshot` the page first). The dev server serves from `localhost:4321`; each doc page is routed by its file path minus `src/content/docs/`.

### Playwright MCP is not attached
Emit one finding noting the missing capability. Do not try to verify pages without a browser — a text-only `curl` check cannot tell whether Mermaid rendered or tabs hydrated.
