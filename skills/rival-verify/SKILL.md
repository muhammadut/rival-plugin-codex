---
name: rival-verify
description: Adversarial code verification with Codex CLI (primary) and skeptical-reviewer fallback. Usage: `/rival-verify [workstream-name]`.
metadata:
  short-description: Multi-repo adversarial code verification orchestrator
---

# Rival Verify v1.10 — Multi-Repo Adversarial Code Verification Orchestrator

You are the Rival verification orchestrator. Your job is to get an independent adversarial review of the BUILT CODE via `codex exec`. Both review paths (primary and fallback) run as `codex exec` subprocesses — the primary sends the assembled verify prompt directly, the fallback wraps the same inputs with the skeptical-reviewer system prompt. The plan IS the spec. You run inline in the current conversation.

## Phase 0: Resolve Plugin Root

Every agent spawn below needs the absolute path to the plugin's `references/` directory. Read `paths.plugin_root` from `.rival/config.json`:

```bash
PLUGIN_ROOT=$(jq -r '.paths.plugin_root' .rival/config.json)
if [ -z "$PLUGIN_ROOT" ] || [ "$PLUGIN_ROOT" = "null" ]; then
  echo "ERROR: .rival/config.json is missing paths.plugin_root. Re-run /rival-init."
  exit 1
fi
```

`$PLUGIN_ROOT` is used throughout Phase 4 to locate `references/skeptical-reviewer.md`.

## Phase 1: State Validation

### 1.1 Read Configuration

Read `.rival/config.json`. If missing:
> "Rival isn't configured. Run `/rival-init` first."

### 1.2 Resolve Workstream

Standard resolution priority (same as other skills).

### 1.3 Validate Phase

Read `state.json`. Phase must be `build-complete` or `verified` (re-verify).

- If earlier: Guide to correct next step
- If `archived`: "This workstream is already complete and archived."
- If `build-complete` or `verified`: proceed

Update state to `verifying`.

## Phase 2: Gather Verification Context

Collect everything the verifier needs. For multi-repo workstreams this is per-repo; for single-repo it's just one entry.

1. **Plan:** `.rival/workstreams/<id>/plan.md` — the single source of truth for what was supposed to be built.

2. **Branches and commits per repo:** read `state.json`. The relevant fields:
   - `state.branches` — map of `{repo-path: { name, role, default_branch }}`. Tells you which branch in which repo, and what its base is for the diff.
   - `state.build.commits` — map of `{repo-path: { first, last, count }}`. The first/last commit hashes per repo. **Do NOT grep git log for the workstream id — execute records these in state.json directly.**
   - `state.build.repos_touched` — flat array of repo paths that actually received commits. (Repos in `state.branches` with `count: 0` are pruned.)

3. **Git diff per repo:** for each repo in `state.build.repos_touched`:
```bash
REPO="<repo-path>"
DEFAULT=$(jq -r ".branches[\"$REPO\"].default_branch" .rival/workstreams/<id>/state.json)
BRANCH=$(jq -r ".branches[\"$REPO\"].name" .rival/workstreams/<id>/state.json)

# Full diff for this repo's branch vs its default
git -C "$REPO" diff "origin/$DEFAULT...$BRANCH"

# Or, equivalently, between first commit's parent and last commit:
FIRST=$(jq -r ".build.commits[\"$REPO\"].first" .rival/workstreams/<id>/state.json)
LAST=$(jq  -r ".build.commits[\"$REPO\"].last"  .rival/workstreams/<id>/state.json)
git -C "$REPO" diff "${FIRST}~1..${LAST}"
```

4. **Test results per repo:** run the test commands from the plan's Validation Plan section. In multi-repo, the plan should specify per-repo test commands (or fall back to each repo's `index.repos[*].test_framework`).

5. **Fallback if state is incomplete:** if `state.build.commits` is missing or empty (e.g., the workstream was built by an older version of execute), fall back to:
   - Try grepping recent git log for `(ws: <workstream-id>)` in commit messages — newer execute embeds this defensively in every commit
   - If still nothing, ask the user: "I can't find a recorded commit range. What range should I review? (e.g., `HEAD~5..HEAD` for single-repo, or specify per-repo)"

**Auto-suggestion for LARGE workstreams:** If the git diff exceeds 2000 lines or touches more than 20 files, warn the user:
> "This is a large workstream. Verification will take longer and may benefit from being split. Proceeding anyway."

## Phase 3: Build Verification Prompt

Assemble the verification prompt. The `## Actual Code Changes` and `## Test Results` sections must be built **per repo** by iterating `state.build.repos_touched` — there is no single global git diff in a multi-repo workstream, and trying to run `git diff` from cwd will crash because cwd is not a git repo (it's the parent containing `knowledge/repos/`).

Pseudocode for assembling the prompt:

```bash
PROMPT=""
PROMPT+="You are performing adversarial code verification.\n\n"
PROMPT+="## Implementation Plan (what was supposed to be built):\n"
PROMPT+="$(cat .rival/workstreams/<id>/plan.md)\n\n"

PROMPT+="## Actual Code Changes (per repo):\n\n"
for REPO in $(jq -r '.build.repos_touched[]' .rival/workstreams/<id>/state.json); do
  BRANCH=$(jq -r ".branches[\"$REPO\"].name" .rival/workstreams/<id>/state.json)
  ROLE=$(jq -r ".branches[\"$REPO\"].role" .rival/workstreams/<id>/state.json)
  FIRST=$(jq -r ".build.commits[\"$REPO\"].first" .rival/workstreams/<id>/state.json)
  LAST=$(jq  -r ".build.commits[\"$REPO\"].last"  .rival/workstreams/<id>/state.json)

  PROMPT+="### $REPO (role: $ROLE, branch: $BRANCH)\n"
  PROMPT+='```diff\n'
  PROMPT+="$(git -C "$REPO" diff "${FIRST}~1..${LAST}")\n"
  PROMPT+='```\n\n'
done

PROMPT+="## Test Results (per repo):\n\n"
# For each repo, run the test command. Source: plan.md Validation Plan section
# (which should be structured per-repo for multi-repo workstreams).
# If the plan only specifies one global test command, run it once and label it accordingly.
# Do NOT try to derive a runnable command from index.repos[*].test_framework alone — that's
# a framework name (e.g., "xunit"), not a command. Ask the user if the plan didn't specify.

PROMPT+="## Your Task:
1. **Read the 'Feature Request & Clarifications' section at the top of plan.md carefully** — this is the authoritative user intent. Verify the code actually delivers the clarified scope, not just what's literally in the tasks.
2. Read the actual source files, not just the diff. Each diff is scoped to one repo — read source files via the same repo path.
3. Verify each task was implemented correctly. The 'Repo:' field in each task tells you which repo's diff to look at.
4. Verify NO scope violations — nothing was added that the user marked 'out of scope'.
5. Verify success criteria from Clarifications section are met.
6. Check for security issues not in the plan.
7. Check test quality — are tests meaningful? (per repo)
8. Check for regressions in existing functionality (per repo).
9. **Cross-repo coherence check (multi-repo only):** if the primary repo introduces a new contract (type, endpoint, event), verify the connected repos are using it correctly. Mismatches between primary's exports and connected's usage are the most common multi-repo bug class.

## Output:
### Verdict: PASS | PASS WITH NOTES | NEEDS FIXES | FAIL
### Scope Adherence: (Did the implementation stay within clarified scope? Any violations?)
### Success Criteria Met: (From Clarifications section — yes/no with evidence)
### Task Verification: (for each task: verified or issue)
### Issues Found: (severity, description, repo:file:line, suggestion)
### Cross-repo Coherence: (PASS or list of mismatches)
### Security Check: PASS or CONCERNS with details
"
```

Write the assembled prompt to `.rival/workstreams/<id>/codex-verify-prompt.md`.

For **single-repo workstreams**, the loop runs once with one entry — same template, just shorter output. No special-case code path needed.

## Phase 4: Execute Verification

Both review paths below run as `codex exec` subprocesses. Codex is always on PATH — we're running inside it — so there is no "is Codex available?" check. Path A is primary; Path B is the skeptical-reviewer fallback and is used only if Path A fails.

### Path A: Codex exec with the verify prompt directly

```bash
VERIFY_PROMPT_FILE=".rival/workstreams/<id>/codex-verify-prompt.md"
VERIFICATION_OUT=".rival/workstreams/<id>/verification.md"

codex exec --full-auto --ephemeral \
  -o "$VERIFICATION_OUT" \
  "$(cat "$VERIFY_PROMPT_FILE")"
PATH_A_EXIT=$?
```

Do NOT set a timeout. Let Codex run as long as it needs. Codex will explore the codebase, read source files, and produce its verdict.

After the call completes, validate:

```bash
if [ "$PATH_A_EXIT" -ne 0 ] || [ ! -s "$VERIFICATION_OUT" ]; then
  PATH_A_FAILED=1
else
  PATH_A_FAILED=0
fi
```

If `PATH_A_FAILED=0`, record the reviewer as "Codex CLI (verify prompt)" and proceed to Phase 4.5.

### Path B: Codex exec wrapped with skeptical-reviewer (fallback)

Invoked **only** when Path A fails (non-zero exit or empty/missing output file). Warn the user:

> "Primary verification via codex exec failed (exit code: <PATH_A_EXIT>, output present: <yes/no>). Falling back to skeptical-reviewer prompt."

Then run:

```bash
SKEPTIC_PROMPT="$(cat "$PLUGIN_ROOT/references/skeptical-reviewer.md")"
VERIFY_PROMPT="$(cat "$VERIFY_PROMPT_FILE")"

codex exec --full-auto --ephemeral \
  -o "$VERIFICATION_OUT" \
  "$SKEPTIC_PROMPT

## Verification Task

$VERIFY_PROMPT"
PATH_B_EXIT=$?
```

After the call completes, validate:

```bash
if [ "$PATH_B_EXIT" -ne 0 ] || [ ! -s "$VERIFICATION_OUT" ]; then
  echo "ERROR: Both primary verification and skeptical-reviewer fallback failed. Keeping state at 'verifying' so the user can retry."
  exit 1
fi
```

Record the reviewer as "Codex CLI (skeptical-reviewer fallback)" and proceed to Phase 4.5.

### Error Handling Summary

- **Path A succeeds** → use its output, reviewer = "Codex CLI (verify prompt)".
- **Path A fails** (non-zero exit OR empty/missing `verification.md`) → run Path B, reviewer = "Codex CLI (skeptical-reviewer fallback)", warn the user.
- **Both fail** → surface the error, keep state at `verifying`, don't silently continue.

Per §4.5 of the migration spec: always check `$?` after `codex exec` and verify the expected output file exists before moving on.

## Phase 4.5: Compute Recommended Merge Order (multi-repo only)

**Skip-condition (explicit):** if `len(state.build.repos_touched) <= 1`, this is effectively a single-repo workstream — there's only one PR to merge, and a merge-order recommendation would be vacuous. Skip Phase 4.5 entirely and proceed to Phase 5.

For multi-repo workstreams (`len(state.build.repos_touched) >= 2`), the order in which you merge matters. Merging the primary repo first activates new code paths before connected repos have caught up — that's a runtime breakage window. Merging connected repos first is almost always safer because they're additive (new types, new fields, new endpoints) without anyone calling them yet.

Read the plan's "System Map" section plus `state.connected_repos` to determine the dependency direction:

- **Tier 1 (merge first):** repos that EXPORT contracts/types others import (e.g., shared-models, schemas, protocol definitions). Branch role: `connected`.
- **Tier 2 (merge second):** repos that CONSUME the new contracts but aren't the user-facing entry point. Branch role: `connected`.
- **Tier 3 (merge LAST):** the primary repo — the one whose feature this is. Once this is merged, users can hit the new feature, so all dependencies must already be live. Branch role: `primary`.

Append a section to `.rival/workstreams/<id>/verification.md`:

```markdown
## Recommended Merge Order

1. **Rival.CentralSchema.API** (chore/<ws-id>) — Tier 1, exports new types
   PR: `gh -R <owner>/Rival.CentralSchema.API pr ...`
   Why first: downstream services import the new type. Merging shared schema first means consumers can pick it up cleanly.

2. **Rival.Customer.API** (chore/<ws-id>) — Tier 2, consumer
   PR: `gh -R <owner>/Rival.Customer.API pr ...`
   Why second: depends on Rival.CentralSchema.API@new. Once shared schema is merged and a new package version is published, customer API can update.

3. **Rival.Apps.API** (feature/<ws-id>) — Tier 3, primary
   PR: `gh -R <owner>/Rival.Apps.API pr ...`
   Why LAST: this is what activates the user-facing feature. Merging it before customer API would create a window where the new code path exists but the consumer isn't ready, causing 5xx errors.

## Compatibility Window

Between merging Tier 1 and merging Tier 3, the system is in a "ready but not activated" state. This is safe — no user-facing change has happened yet. The window collapses the moment Tier 3 merges. If you need to roll back, roll back in REVERSE order (Tier 3 → Tier 2 → Tier 1).
```

For single-repo workstreams, skip this phase — there's only one PR to merge.

## Phase 5: Human Gate (Final)

Read `.rival/workstreams/<id>/verification.md` and present results:

> "**Verification complete.**
>
> Reviewer: <Codex CLI (verify prompt) / Codex CLI (skeptical-reviewer fallback)>
> Verdict: **<verdict>**
>
> **Tasks: <N>/<N> verified**
> **Issues found: <N>** <one-line summary of each with severity, if any>
> **Security: <PASS/CONCERNS>**
>
> Full verification: `.rival/workstreams/<id>/verification.md`
>
> **What would you like to do?**
> 1. **Ship it** — mark as verified, run retro to capture lessons
> 2. **Fix issues** — address the findings and re-verify
> 3. **Accept as-is** — acknowledge issues but mark as verified anyway"

On **Ship it** or **Accept as-is**:
- Update state to `verified` (NOT `archived` — retro is the one that archives, so it can still find this workstream)
- Append to history with timestamp
- Print:
> "Workstream **<id>** marked as **verified**. All artifacts preserved in `.rival/workstreams/<id>/`.
> Next: run `/rival-retro` to capture lessons learned. The retro will archive this workstream once it's done."

On **Fix issues**: Keep state at `build-complete`, user fixes and re-runs `/rival-verify`.

## Important Notes

- This reviews CODE, not the plan — focus on what was actually built
- The plan.md is the spec — it defines what "correct" means
- The git diff should capture all changes from the workstream, not just the last commit
- Do NOT set a timeout on Codex — let it run to completion
- If the verifier's output is clearly wrong (hallucinated files, etc.), note it to the user
- Archiving preserves all artifacts — workstreams are a permanent record
- Both Path A and Path B are `codex exec` subprocesses; they differ only in the system prompt wrapping. Path B uses `$PLUGIN_ROOT/references/skeptical-reviewer.md` as a system prompt around the same verify inputs.
