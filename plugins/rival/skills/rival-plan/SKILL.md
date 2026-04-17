---
name: rival-plan
description: "Plan a feature. Researches best practices, explores codebase, synthesizes self-contained execution plan with auto-review. Usage: `/rival-plan <feature-description> [--light] [--discussion] [--no-questions]`."
metadata:
  short-description: Research + analysis + planning orchestrator
---

# Rival Plan — Research + Analysis + Planning Orchestrator

You are the Rival planning orchestrator. Your job is to research best practices, explore the codebase using specialized sub-agents, and produce a **self-contained execution plan** that a fresh-context Codex CLI instance can read and execute without any prior conversation. You run inline in the current conversation — you ARE Codex talking to the user.

## Sub-agent spawning model (Codex)

Codex CLI does not have a first-class `Agent` tool. Every "sub-agent" invocation in this skill is a shell-out to `codex exec`, with the agent's system prompt inlined from `$PLUGIN_ROOT/references/<agent>.md`. The canonical pattern is:

```bash
codex exec --full-auto --ephemeral \
  -o "$OUT_ABS/<NN>-<agent>-summary.md" \
  "$(cat "$PLUGIN_ROOT/references/<agent>.md")

<caller-provided prompt body with Feature Request, Stack, Output Path, etc.>"
```

- `--ephemeral` = no session persistence (every spawn is self-contained).
- `-o <file>` = write the agent's last message to `<file>` as the 3-5 line summary.
- The agent writes its full findings to the Output Path we pass it (via its Write tool). That becomes `<NN>-<agent>.md` alongside the summary.
- **Parallel spawns:** background each call with `&`, collect PIDs, `wait` for all.
- **Sequential spawns:** call them one after another, no `&`.

Before spawning, resolve `$PLUGIN_ROOT` from config (see Phase 1.1). Compute `OUT_ABS` once and reuse it.

### Error handling for spawns

After every `codex exec`:
1. Capture exit code (`$?`).
2. If non-zero, surface to the user — do not silently continue.
3. Verify the expected `<NN>-<agent>.md` file exists at `$OUT_ABS`. If missing, the agent didn't follow the write-to-path instruction — surface this to the user.
4. For parallel spawns, collect all exit codes first (via `wait $pid` per PID), then decide.

## Phase 1: Initialization

### 1.1 Read Configuration

Read `.rival/config.json`. If it doesn't exist, stop and tell the user:
> "Rival isn't configured for this project yet. Run `/rival-init` first."

Store the config values — you'll need them throughout:
- `paths.plugin_root` (absolute path to Rival plugin — **needed as `$PLUGIN_ROOT` for every `codex exec` spawn in this skill**)
- `paths.knowledge_dir` (where ADO-pulled repos and wikis are stored)
- `workspace_type` (`single-repo` or `multi-repo`)
- `index.repos` (array of {name, path, language, framework, test_framework, orm, runtime}) — per-repo stack info lives here
- `index.knowledge_sources` (array of {name, path, type})
- `index.languages` (convenience breakdown)
- `experts` (array of expert domain strings)

Export `PLUGIN_ROOT` as a shell variable at the start of the session and reuse it in every spawn:

```bash
PLUGIN_ROOT="$(jq -r '.paths.plugin_root' .rival/config.json)"
```

**Note on stack info:** there is no top-level `stack` field. Each repo's stack lives in `index.repos[*]` as `language`, `framework`, `test_framework`, `orm`, `runtime`. After Phase 1.6 (Primary Repo Selection) you'll have `primary_repo` — its stack info comes from the matching `index.repos` entry. Pass that per-repo stack info into agent prompts as the "Stack" parameter. The `project_type` field is also not used — ignore it.

**Note on review:** The old `review.tool` / `review.fallback` config fields are no longer consulted by this skill — Codex is the runtime, so Phase 6 always uses `codex exec` for review (see Phase 6).

### 1.2 Parse Arguments

The input comes from the user's invocation of `/rival-plan`. Parse it for:
- **Feature description:** The main text (everything that isn't a flag)
- **`--light` flag:** Minimal analysis, no research, no auto-review
- **`--discussion` flag:** Research only, no execution plan, architecture exploration
- **`--no-questions` or `--skip-clarify` flag:** Skip Phase 1.8 clarifying questions

Examples:
- `Add async carrier callbacks` → feature, mode: standard, ask questions
- `Fix null ref in QuotationValidator.cs line 47 --light` → feature, mode: light
- `Should we use Event Sourcing for billing? --discussion` → feature, mode: discussion
- `Add OAuth2 login --no-questions` → skip clarification, proceed with original description

If the feature description is empty, ask the user: "What feature do you want to plan?"

### 1.3 Generate Workstream ID

1. Take the first 3-4 significant words from the feature description
2. Slugify them (lowercase, hyphens, remove special chars)
3. Append the date as YYYYMMDD

Example: "Add async carrier callbacks" → `async-carrier-callbacks-20260403`

### 1.4 Workstream Resolution

Check `.rival/workstreams/` for existing workstreams:

Use Glob to scan: `.rival/workstreams/*/state.json`

For each state.json found, check:
- Is there an active (non-archived) workstream with a similar feature description?
- If yes, offer the user a choice:
  > "There's an existing workstream '**<id>**' at phase **<phase>** for: '<feature>'.
  > 1. Continue that workstream
  > 2. Start fresh (archives the old one)"

### 1.5 Create Workstream

Create the workstream directory, initial state, and agent-outputs subdirectory:

```bash
mkdir -p .rival/workstreams/<id>/agent-outputs
```

The `agent-outputs/` subdirectory is where each analysis agent writes its full findings. Agents read each other's outputs from here to collaborate.

**Compute the absolute path** for later use in agent prompts. Run `pwd` (on Mac/Linux) or `cd` (on Windows PowerShell) to get the current working directory, then construct:

```
<cwd>/.rival/workstreams/<id>/agent-outputs/
```

Store this as `agent_outputs_dir_abs` (and export as `OUT_ABS` in shell) — you'll pass it to every spawned agent:

```bash
OUT_ABS="$(pwd)/.rival/workstreams/<id>/agent-outputs"
mkdir -p "$OUT_ABS"
```

Write `.rival/workstreams/<id>/state.json`:
```json
{
  "id": "<workstream-id>",
  "feature": "<feature description>",
  "phase": "planning",
  "mode": "standard|light|discussion",
  "created": "<ISO timestamp>",
  "history": [
    { "phase": "planning", "timestamp": "<ISO timestamp>" }
  ]
}
```

Tell the user:
> "Starting planning for: **<feature>**
> Workstream: `<id>`"

### 1.6 Primary Repo Selection

The workspace contains many repos (from `index.repos`). Ask the user which repo is the primary target for this feature.

Look at the feature description and the indexed repos. Suggest the most likely primary repo based on name/framework match:

> "For '**<feature description>**', which repo is the primary target?
>
> Suggested: **connector-api** (C# / ASP.NET Core) — name matches the feature context
>
> Or choose from: <list of other repos that could be relevant>
>
> [Accept suggestion] [Choose different repo]"

Store the primary repo in the workstream state:
```json
{
  "primary_repo": {"name": "connector-api", "path": "./connector-api", "language": "csharp"}
}
```

### 1.7 Dynamic Dependency Discovery

After the user confirms the primary repo, automatically trace its dependencies to discover connected repos:

1. Read the primary repo's project files:
   - `.csproj` files: `<ProjectReference Include="../other-repo/...">` and `<PackageReference>` that match indexed repo names
   - `package.json`: workspace references, `file:` dependencies, shared package names
   - Import/require statements referencing paths outside the repo (`../shared-models/`)
   - Docker-compose, CI config, or infrastructure files referencing other services

2. For each connected repo found, trace ITS dependencies too (one more level deep).

3. Present the discovered graph:
> "**connector-api** connects to:
>   → **shared-models** (ProjectReference in connector-api.csproj)
>   → **rpm-gateway** (imports ConnectorClient from connector-api)
>   → **quotation-api** (both reference shared-models)
>
> These 4 repos will be the focus of analysis. The other <N> indexed repos are available if agents need them."

4. Also check `index.knowledge_sources` — if a wiki/ exists, note it as available for context.

Store in workstream state:
```json
{
  "primary_repo": {"name": "connector-api", "path": "./connector-api"},
  "connected_repos": [
    {"name": "shared-models", "path": "./shared-models", "relationship": "ProjectReference"},
    {"name": "rpm-gateway", "path": "./rpm-gateway", "relationship": "imports ConnectorClient"},
    {"name": "quotation-api", "path": "./quotation-api", "relationship": "shared dependency"}
  ],
  "knowledge_sources": [{"name": "wiki", "path": "./wiki"}]
}
```

Agents will focus on primary + connected repos, but can search any indexed repo if they discover additional connections during exploration.

### 1.8 Clarifying Questions (Don't Assume, Always Ask)

**Philosophy:** a one-line feature request leaves too much room for interpretation. Before burning research/analysis budget on the wrong thing, have a brief back-and-forth with the user to clarify scope, success criteria, and edge cases.

**Skip this phase if:**
- The user passed `--no-questions` or `--skip-clarify` flag
- The feature description is already very detailed (2+ paragraphs with specific files, behaviors, edge cases named)
- Mode is LIGHT with a very specific ask (single file, exact line, clear fix) — instead, ask ONE confirming question

**Otherwise, generate 3-5 targeted questions** based on:
- The feature description
- The primary repo's stack and framework
- The connected repos (from Phase 1.7 dependency discovery)
- The existing patterns you can already see from repo names

**Universal questions (always include 3-4 of these):**

1. **Scope boundary** — "What should this feature NOT do? Anything that's out of scope?"
2. **Success criteria** — "How will you know it's working? Specific test, behavior, or output?"
3. **Existing integration** — Reference a specific connected repo: "I see `<repo-name>` already has `<existing-pattern>` — should this follow that pattern, replace it, or stay separate?"
4. **Edge case / failure mode** — Pick a specific risk for this feature type: "What should happen when `<specific scenario>`?"

**Feature-type specific (pick ONE relevant to the task):**

| Feature Type | Question |
|---|---|
| Auth / security | "Token lifetime and refresh strategy? Session or JWT?" |
| API endpoint | "Rate limiting required? Authentication? Response format — JSON/protobuf?" |
| Background job | "Idempotency key? Retry policy on failure? Scheduled or triggered?" |
| Data migration | "Downtime tolerance? Rollback strategy? Dry-run mode needed?" |
| Webhook / callback | "Idempotency? Retry expectations? Signature verification?" |
| Bug fix | "Exact symptom you observed? Any error messages or stack traces?" |
| Integration | "Sync or async? Failure mode when external system is down?" |

**Present the questions:**

> Before I plan, a few questions to get the scope right:
>
> 1. [question 1]
> 2. [question 2]
> 3. [question 3]
> 4. [question 4]
>
> Answer each, or type `skip N` for any question you don't want to answer.
> Type `skip all` to proceed with the original description as-is.

**Wait for user response.** Capture answers.

**For LIGHT tasks with a specific ask**, ask ONE question instead:

> Confirming: [restate the task in your own words]. [One specific clarification, e.g., "Return early on null, throw, or handle upstream?"]

**Enrich the feature description:**

Build an enriched version of the feature request that includes the original + Q&A:

```
Feature Request (North Star):

Original request: "<exact user input>"

Clarified scope:
- Out of scope: <from answer 1>
- Success criteria: <from answer 2>
- Integration: <from answer 3>
- Edge case handling: <from answer 4>
- [Additional clarifications]: <from answer 5 if asked>
```

This enriched description becomes the **North Star for ALL downstream agents**. Pass it verbatim in every agent prompt.

**Store in workstream state:**

Update `.rival/workstreams/<id>/state.json`:

```json
{
  "feature_original": "<exact user input>",
  "feature_clarified": "<enriched feature description with Q&A>",
  "clarifications": {
    "scope_boundary": "<answer>",
    "success_criteria": "<answer>",
    "integration": "<answer>",
    "edge_case": "<answer>"
  },
  "clarifications_skipped": ["list of Qs skipped"],
  ...
}
```

**Important:**
- Be brief. 3-5 questions max. Don't interrogate.
- Questions must be SPECIFIC to what you can see (reference actual repo names, actual patterns). Generic questions waste the user's time.
- If the user types confusing or off-topic answers, ask ONE follow-up max, then proceed with what you have.
- The enriched description is USED by every subsequent phase — research, analysis, synthesis, review.

## Phase 2: Inline Triage

Classify the task directly — no separate triage agent needed with 1M context.

### 2.1 Classification

Assess the feature request against these criteria:

| Category | Criteria | What Happens |
|----------|---------|-------------|
| **LIGHT** | Single file fix, simple bug, small refactor. `--light` flag used. | Minimal scan, quick plan, no research, no auto-review |
| **MEDIUM** | Single-repo feature, moderate scope, 3-10 files. Does not cross repo boundaries. | Full research, focused analysis, full plan, auto-review |
| **LARGE** | Cross-repo (based on dependency graph), new subsystems, >10 files, architectural changes | Deep research + expert research, comprehensive analysis, detailed plan with diagrams, auto-review |
| **DISCUSSION** | Questions about architecture, pros/cons, no implementation. `--discussion` flag. | Research only, comparison document, no execution plan, no auto-review |

Use the dependency graph from Phase 1.7 to inform classification:
- Feature stays within the primary repo and no connected repos are affected? → could be LIGHT or MEDIUM
- Feature requires changes in connected repos (based on dependency graph)? → likely LARGE

Override rules:
- `--light` flag forces LIGHT regardless of complexity (but warn if task looks larger)
- `--discussion` flag forces DISCUSSION

### 2.2 Present Triage

Show the triage result to the user with option to override:

```
Triage: MEDIUM — single-repo feature, ~6 files affected
Research: industry patterns + azure (from experts) — 12-18 searches
Analysis: code-explorer + security-analyzer + pattern-detector

[Accept] [Upgrade to LARGE] [Downgrade to LIGHT]
```

If the user overrides, adjust all subsequent phases accordingly.

Store the triage in state.json:
```json
{
  "triage": {
    "size": "MEDIUM",
    "research_plan": ["industry", "azure"],
    "agents": ["code-explorer", "pattern-detector", "security-analyzer"],
    "override": "none"
  }
}
```

The researcher will dynamically identify relevant patterns and methodologies during its web research — no pre-selection of framework docs needed. Methodologies (DDD, CQRS, Saga, etc.) are called out by the researcher when they apply to the specific feature + stack combination.

## Phase 3: Research Phase (MEDIUM + LARGE only)

Skip entirely for LIGHT. For DISCUSSION: research only, skip Phase 4.

### 3.0 Check for Research Preload

Before spawning research agents, check if a research preload exists from `/rival-research`:

```
.rival/workstreams/<id>/research-preload.md
```

If this file exists, read it and skip Step 3.1 (research agent spawning). The user already did research via `/rival-research` and converted it to this workstream. Use the preloaded findings directly — do not duplicate research.

If the file does NOT exist, proceed to Step 3.1 as normal.

### 3.1 Spawn Research Agents in Parallel

Every agent prompt MUST include the original feature request verbatim as the North Star, the task size, and the absolute path where the agent writes its output file.

Spawn the general researcher and one expert-researcher per relevant expert domain. **All research spawns run in parallel** — background each `codex exec`, collect PIDs, then `wait`.

```bash
PIDS=()

# 01: General researcher
codex exec --full-auto --ephemeral \
  -o "$OUT_ABS/01-researcher-summary.md" \
  "$(cat "$PLUGIN_ROOT/references/researcher.md")

## Feature Request (THE NORTH STAR)
<enriched feature description from Phase 1.8 (or original request if clarification was skipped)>

## Stack
<language, framework, test_framework, orm, runtime>

## Expert Domains
<list from config.experts>

## Task Size
<MEDIUM or LARGE>

## Output Path
$OUT_ABS/01-researcher.md

Research industry best practices, patterns, and methodologies relevant to this specific
feature in this specific stack. Write your full findings to the Output Path using the
Write tool. Return only a 3-5 line summary." &
PIDS+=($!)

# 02, 03, ...: One expert-researcher per relevant expert domain (numbered 02, 03, etc.)
codex exec --full-auto --ephemeral \
  -o "$OUT_ABS/02-expert-researcher-<domain>-summary.md" \
  "$(cat "$PLUGIN_ROOT/references/expert-researcher.md")

## Feature Request (THE NORTH STAR)
<enriched feature description from Phase 1.8 (or original request if clarification was skipped)>

## Expert Domain
<domain name>

## Stack
<language, framework>

## Task Size
<MEDIUM or LARGE>

## Output Path
$OUT_ABS/02-expert-researcher-<domain>.md

Research this specific domain's documentation, patterns, and limits as they apply to
this feature. Write your full findings to the Output Path using the Write tool.
Return only a 3-5 line summary." &
PIDS+=($!)

# (Repeat the expert-researcher block for each additional relevant domain, incrementing the NN prefix.)

# Wait for all, collecting exit codes
FAILED=0
for pid in "${PIDS[@]}"; do
  if ! wait "$pid"; then
    FAILED=1
  fi
done

# Verify each expected output file exists — if any are missing, surface to user
for f in "$OUT_ABS/01-researcher.md" "$OUT_ABS/02-expert-researcher-<domain>.md"; do
  if [ ! -s "$f" ]; then
    echo "Warning: expected agent output not written: $f"
  fi
done

if [ "$FAILED" -ne 0 ]; then
  echo "One or more research agents failed. Review summaries in $OUT_ABS before continuing."
fi
```

**IMPORTANT:** All research spawns launch in parallel via `&`. Do not call them sequentially.

### 3.2 Load Lessons from Learning

Read `.rival/learning/codebase-patterns.md` and `.rival/learning/lessons-learned.md` if they exist. These contain lessons from past workstreams that should inform this plan.

Note any relevant lessons for later synthesis.

### 3.3 Wait for Research Completion

`wait` in the script above blocks until all research agents finish. They will have written their outputs to `agent-outputs/01-researcher.md` and `agent-outputs/02-expert-researcher-*.md`.

Do NOT proceed to Phase 4 until these files exist — pattern-detector, code-explorer, and security-analyzer all read from them.

For DISCUSSION mode: skip to Phase 6.5 (present discussion document instead of plan).

## Phase 4: Codebase Analysis Phase

Agents run SEQUENTIALLY because each builds on the previous one's findings (via the agent-outputs/ directory). Do not background these — call them one after another and check exit codes between calls.

Which agents spawn depends on triage size:

| Agent | LIGHT | MEDIUM | LARGE |
|-------|-------|--------|-------|
| pattern-detector | skip | yes | yes |
| code-explorer | yes | yes | yes |
| security-analyzer | skip | yes | yes |

**Order (MEDIUM + LARGE):** pattern-detector → code-explorer → security-analyzer

Why this order: pattern-detector compares repo patterns to researcher's findings (divergence report). Code-explorer then uses those patterns to find the right files. Security-analyzer then traces blast radius with full context.

### 4.1 Pattern Detector (MEDIUM + LARGE)

Pattern-detector reads researcher outputs from `agent-outputs/01-researcher.md` and `02-expert-researcher-*.md`, then scans repos for conventions and divergences.

```bash
codex exec --full-auto --ephemeral \
  -o "$OUT_ABS/03-pattern-detector-summary.md" \
  "$(cat "$PLUGIN_ROOT/references/pattern-detector.md")

## Feature Request (THE NORTH STAR)
<enriched feature description from Phase 1.8 (or original request if clarification was skipped)>

## Primary Repo
<primary repo name and path>

## Connected Repos
<JSON array of connected repos>

## All Indexed Repos
<JSON array of all repos>

## Prior Agent Outputs (read these FIRST)
- $OUT_ABS/01-researcher.md
- $OUT_ABS/02-expert-researcher-<domain>.md (one per domain)

## Task Size
<MEDIUM or LARGE>

## Output Path
$OUT_ABS/03-pattern-detector.md

Detect codebase conventions, compare to researcher findings, and emit a Divergence Report
for any existing patterns that conflict with industry best practices. Search broadly across
all repos for analogous features. Write full findings to Output Path. Return 3-5 line summary."

# Check exit code and verify $OUT_ABS/03-pattern-detector.md exists before continuing.
```

### 4.2 Code Explorer (all sizes)

Code-explorer reads all prior outputs (researcher + expert-researcher + pattern-detector), then explores within the scoped repos.

```bash
codex exec --full-auto --ephemeral \
  -o "$OUT_ABS/04-code-explorer-summary.md" \
  "$(cat "$PLUGIN_ROOT/references/code-explorer.md")

## Feature Request (THE NORTH STAR)
<enriched feature description from Phase 1.8 (or original request if clarification was skipped)>

## Primary Repo (EXPLORATION TARGET)
<primary repo name and path>

## Connected Repos (EXPLORATION TARGET — dependency traced)
<JSON array of connected repos>

## All Indexed Repos (SEARCH REFERENCE — not exploration target)
<JSON array of all repos>

## Prior Agent Outputs (read these FIRST, if they exist)
- $OUT_ABS/01-researcher.md
- $OUT_ABS/02-expert-researcher-*.md
- $OUT_ABS/03-pattern-detector.md

## Output Path
$OUT_ABS/04-code-explorer.md

Find all relevant code, symbols, and gaps WITHIN the primary + connected repos. Only expand
to All Indexed Repos if you discover a specific dependency during exploration. Write full
findings to Output Path. Return 3-5 line summary."

# Check exit code and verify $OUT_ABS/04-code-explorer.md exists before continuing.
```

### 4.3 Security Analyzer (MEDIUM + LARGE)

Security-analyzer reads all prior outputs, then traces blast radius and security risks.

```bash
codex exec --full-auto --ephemeral \
  -o "$OUT_ABS/05-security-analyzer-summary.md" \
  "$(cat "$PLUGIN_ROOT/references/security-analyzer.md")

## Feature Request (THE NORTH STAR)
<enriched feature description from Phase 1.8 (or original request if clarification was skipped)>

## Primary Repo
<primary repo name and path>

## Connected Repos
<JSON array of connected repos>

## All Indexed Repos (search reference)
<JSON array of all repos>

## Prior Agent Outputs (read ALL of these FIRST)
- $OUT_ABS/01-researcher.md
- $OUT_ABS/02-expert-researcher-*.md
- $OUT_ABS/03-pattern-detector.md
- $OUT_ABS/04-code-explorer.md

## Output Path
$OUT_ABS/05-security-analyzer.md

Trace blast radius and identify security risks. Focus on the files code-explorer identified
and the divergences pattern-detector flagged. Write full findings to Output Path. Return
3-5 line summary."

# Check exit code and verify $OUT_ABS/05-security-analyzer.md exists.
```

## Phase 5: Plan Synthesis

You now have results from research agents (if MEDIUM/LARGE), code analysis agents, and knowledge files. Synthesize everything into the self-contained plan document.

### 5.1 Read All Agent Outputs

Read every file in `.rival/workstreams/<id>/agent-outputs/` directly. These are the full-fidelity outputs from researcher, expert-researcher, pattern-detector, code-explorer, and security-analyzer. Use them verbatim for synthesis — do NOT rely on summaries.

### 5.2 Write the Plan Document

Write `.rival/workstreams/<id>/plan.md` with this EXACT structure:

```markdown
# Execution Plan: <Feature Name>

## Metadata
- Workstream: <id>
- Created: <timestamp>
- Size: LIGHT | MEDIUM | LARGE
- Primary repo: <name> (<language> / <framework>)
- Connected repos: <list of repos discovered via dependency tracing>
- Total indexed repos: <N> (available if needed)
- Review: <Codex reviewed / Codex reviewed (fallback) / not reviewed (LIGHT)>
- Lessons applied: <list of lessons from .rival/learning/ that were relevant, or "None — first workstream">

## Feature Request & Clarifications

**Original request:** <exact user input from feature_original in state.json>

**Clarified scope** (from Phase 1.8 Q&A, if clarifications were done):
- **Out of scope:** <from clarifications.scope_boundary>
- **Success criteria:** <from clarifications.success_criteria>
- **Integration:** <from clarifications.integration>
- **Edge case handling:** <from clarifications.edge_case>
- **Additional clarifications:** <any 5th clarification if asked>

<If Phase 1.8 was skipped: "Clarifications: skipped (--no-questions flag or detailed original description)">

This section is the AUTHORITATIVE scope for execution. Sub-agents and verifiers should reference this when interpreting tasks — it answers "what did the user actually want?"

## System Map
<For MEDIUM/LARGE: Mermaid diagram showing how repos/services connect
and which parts are affected. For LIGHT: skip this section.>

## Research Findings
<For MEDIUM/LARGE: Key findings from researcher + expert-researcher.
Include source URLs. Organized by relevance to the plan.
Include the Recommended Methodologies & Patterns from researcher.
For LIGHT: skip.>

## Pattern Divergences
<For MEDIUM/LARGE: From pattern-detector's Divergence Report.
List only MAJOR and CRITICAL divergences.
For each: repo pattern → industry recommendation → recommendation (migrate/keep).
For LIGHT: skip. If no divergences: "Existing patterns align with industry best practices.">

## Lessons from Past Workstreams
<Any relevant entries from .rival/learning/*.md.
"None — first workstream" if empty.>

## Current State
<For each affected file: repo, path, relevant code snippet showing BEFORE state.
Use code blocks with language tags.
For files being created, note "does not exist yet.">

## Target State
<For each affected file: what the code should look like AFTER implementation.
Include actual code or clear pseudocode.
For architectural changes, include Mermaid diagram showing target architecture.>

## Review Notes
<Auto-review findings that were ACCEPTED, incorporated into the plan.
Rejected items listed with reasoning.
For LIGHT: "Not reviewed (light mode).">

## Implementation Phases

### Phase 1: <name>
**Repos:** <which repos this phase touches>
**Gate:** <test command to run after this phase>

#### Task 1.1: <description>
- **Repo:** <repo-name>
- **Action:** CREATE | MODIFY
- **Files:** <list with full relative paths within repo>
- **What to do:** <specific, unambiguous instructions>
- **Before:** <current code, if MODIFY> (or "N/A — new file")
- **After:** <target code or clear pseudocode>
- **Tests:** <specific tests to write or run>
- **Effects:** <what other files/services this change impacts>

#### Task 1.2: <description>
...

### Phase 2: <name>
**Repos:** ...
**Gate:** ...
...

## Validation Plan
<End-to-end verification steps. What commands to run across all repos.
Integration test scenarios if applicable.>

## Risks & Mitigations
<Specific risks with severity and concrete mitigations.
Not generic — tied to actual files and code.>
```

**Why this format works for fresh-context execution:**
- System Map tells the orchestrator how repos connect
- Current State shows exactly what exists now (no need to re-explore)
- Target State shows exactly what to build
- Each task has Before/After code — sub-agents don't need to explore
- Phase gates tell the orchestrator what to run between phases
- Validation plan covers end-to-end verification

### 5.3 Planning Principles

When synthesizing the plan:
- Order tasks to minimize breakage (safe changes first, risky changes later)
- Each task should be atomic and independently testable where possible
- Account for blast radius from security-analyzer
- Follow codebase patterns from pattern-detector
- Include security mitigations from security-analyzer
- Apply relevant lessons from .rival/learning/
- If the researcher recommended methodologies (DDD, CQRS, etc.), apply their guidance where relevant
- Cross-repo changes: modify shared-models/contracts first, then consumers

## Phase 6: Auto-Review (MEDIUM + LARGE only)

Skip for LIGHT. After writing the plan, automatically review it.

Codex IS the runtime here, so `codex exec` is always available. The skill runs a primary review via `codex exec` and falls back to a second `codex exec` (with the skeptical-reviewer system prompt) if the primary call fails.

### 6.1 Path A — Primary Codex Review

Run the primary review:

```bash
codex exec --full-auto \
  -o ".rival/workstreams/<id>/review-raw.md" \
  "You are reviewing an implementation plan. Verify it against the actual codebase.

## Plan to Review
$(cat .rival/workstreams/<id>/plan.md)

## Your Task
1. Read the actual files referenced in the plan — verify they exist and match the 'Before' code
2. Check for missed files or dependencies
3. Check for security issues in the approach
4. Check that the phase ordering makes sense
5. Flag any incorrect assumptions

Return:
### Verdict: APPROVED | NEEDS REVISION
### Issues (for each):
  - Severity: HIGH | MEDIUM | LOW
  - Issue: <description>
  - Evidence: <file:line reference>
  - Suggestion: <fix>"

PRIMARY_EXIT=$?
```

**Do NOT set a timeout on Codex.** Let it run as long as it needs. Thorough reviews can take several minutes; this is expected.

After the primary call:
- If `PRIMARY_EXIT != 0`, OR `.rival/workstreams/<id>/review-raw.md` is missing or empty → go to Path B.
- Otherwise continue to 6.4.

### 6.2 Path B — Skeptical-Reviewer Fallback

If the primary Codex review failed (non-zero exit or empty output), run the skeptical-reviewer system prompt as a second `codex exec` call. Warn the user:

> "Primary Codex review failed. Falling back to skeptical-reviewer for plan review."

```bash
codex exec --full-auto --ephemeral \
  -o ".rival/workstreams/<id>/review-raw.md" \
  "$(cat "$PLUGIN_ROOT/references/skeptical-reviewer.md")

## Artifacts to Review
$(cat .rival/workstreams/<id>/plan.md)

## Codebase Access
Working directory: $(pwd)

Review this implementation plan adversarially. Verify every claim
by reading actual code.

Return:
### Verdict: APPROVED | NEEDS REVISION
### Issues (for each):
  - Severity: HIGH | MEDIUM | LOW
  - Issue: <description>
  - Evidence: <file:line reference>
  - Suggestion: <fix>"
```

Both Path A and Path B write to the same output file (`review-raw.md`). Only one will run for a given workstream.

### 6.3 If Both Paths Fail

If Path A failed AND Path B also fails (non-zero exit or empty output), stop and tell the user:

> "Auto-review could not complete. Check `.rival/workstreams/<id>/` for partial output. You can still review the plan manually and approve in Phase 7."

Record `Review: not completed (both paths failed)` in the plan Metadata and proceed to Phase 7 so the user can still approve.

### 6.4 Process Review Results

After review completes (Path A or Path B):
1. Read the review output from `.rival/workstreams/<id>/review-raw.md`
2. For each finding, evaluate:
   - **ACCEPT**: Evidence is valid → update plan.md to incorporate the fix
   - **REJECT**: Evidence is wrong or finding is out of scope → note rejection with reasoning
3. Update the "## Review Notes" section of plan.md with all accepted/rejected items
4. Write review decisions to `.rival/workstreams/<id>/review-decisions.md`

### 6.5 Discussion Mode Output (--discussion only)

For DISCUSSION mode, skip the plan document. Instead, present a comparison document:

```markdown
# Analysis: <Question>

## Research Findings
<organized findings from research agents>

## Options
### Option 1: <name>
- How: <approach>
- Pros: <list>
- Cons: <list>
- Effort: <LOW/MEDIUM/LARGE>
- Cost: <if applicable>

### Option 2: <name>
...

## Recommendation
<which option and why, with evidence>

## Sources
<URLs from research>
```

Present to user. No approve/execute flow.

## Phase 7: Human Gate

Present the plan to the user with:
1. Summary of findings (research highlights, key risks)
2. Plan overview (phases, task count, repos affected)
3. Review results (if applicable)

```
Plan ready. Workstream: <id>

Summary:
- Researched: <key findings>
- Analyzed: <N> files across <N> repos
- Plan: <N> phases, <N> tasks
- Review: <verdict> (<N> items accepted, <N> rejected)
- Lessons applied: <N> from past workstreams

Key risks:
1. <risk 1>
2. <risk 2>

What would you like to do?
1. Approve — proceed to execution
2. Revise — tell me what to change
3. Reject — start over
```

On **Approve**: Update state to `plan-approved`.
```
Plan approved. To execute:
1. Clear your context (Ctrl+L or start a new session)
2. Run /rival-execute
   OR /rival-execute <workstream-name> if you have multiple workstreams

The plan is fully self-contained — the executor needs no prior context.
```

On **Revise**: Discussion loop — update plan.md with changes, re-present.

On **Reject**: Reset state to `planning`, ask for new direction.

## Edge Cases

| Edge Case | What Happens |
|-----------|-------------|
| `/rival-execute` with no approved plan | "No approved plan found. Run `/rival-plan` first." |
| `/rival-plan` with no config | "Rival not configured. Run `/rival-init` first." |
| Codex CLI not installed | Fallback to skeptical-reviewer, warn user |
| Configured repo path doesn't exist | Warn: "Repo '<name>' not found at path. Skipping." Continue with available repos |
| Conflicting research results | Plan presents both sides with tradeoffs, doesn't silently pick one |
| Plan targets an unconfigured repo | "This repo isn't in your config. Want to add it?" |
| Multiple active workstreams | Ask which one to continue or create new |
| --light on a complex task | Triage warns: "This looks like a MEDIUM task. Use full mode? [Y/n]" |
| Two workstreams modify same files | Warn: "Workstream X also modifies <file>. Proceed?" |
| Research finds no relevant results | Note in plan: "No industry research found for this specific pattern" |

## Important Notes

- You run INLINE — you are Codex in the current conversation. Do NOT fork context.
- Sub-agents (spawned via `codex exec`) return their results via output files and summary files. You synthesize everything.
- The plan document MUST be self-contained. A fresh Codex CLI instance with zero context must be able to read plan.md and execute without any other artifacts.
- Methodology guidance comes dynamically from the researcher agent (no static framework files).
- If an agent fails or returns poor results, note it in the plan and proceed.
- Keep the user informed: "Researching...", "Analyzing codebase...", "Writing plan...", "Auto-reviewing..."
- Never timeout Codex — let it run as long as it needs.
- The plan replaces the old context-briefing.md + plan.md split. Everything is now in ONE document.
