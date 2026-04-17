---
name: rival-retro
description: Extract lessons learned and codebase patterns from completed workstreams. Builds persistent knowledge for future planning. Usage: `/rival-retro [workstream-name]`.
metadata:
  short-description: Post-workstream knowledge extraction and archival
---

# Rival Retro

After a workstream completes, analyze all artifacts and extract persistent knowledge for future planning.

## Step 0: Check Configuration

Read `.rival/config.json`. If missing:
> "Rival isn't configured for this project. Run `/rival-init` first."

## Step 1: Resolve Workstream

Use the standard workstream resolution priority to identify the target workstream:

1. If a workstream name was passed as an argument, use it directly.
2. If no argument, scan `.rival/workstreams/*/state.json` for workstreams in phase `verified` or `build-complete`. These are the candidates for retro. Auto-select if only one exists.
3. If multiple candidates exist, ask the user to choose. Prefer the most recently updated workstream as the suggested default.
4. If still nothing found, also accept workstreams in phase `archived` whose `retro_completed_at` field is missing — this allows running retro after archiving by hand.

Read `.rival/workstreams/<id>/state.json` to load workstream metadata (name, current phase, dates, tasks, build summary).

## Step 2: Read All Artifacts

Gather every artifact produced during the workstream:

- `.rival/workstreams/<id>/plan.md` — the implementation plan
- `.rival/workstreams/<id>/build-log.md` — build output and task completion notes
- `.rival/workstreams/<id>/verification.md` — adversarial review findings
- Run `git diff` for the workstream's branch or commit range to see actual code changes

Also read any existing knowledge files so you can detect duplicates:

- `.rival/learning/codebase-patterns.md`
- `.rival/learning/lessons-learned.md`

If knowledge files do not exist yet, that is fine — they will be created in Step 4.

## Step 3: Extract Lessons

Analyze all artifacts and extract knowledge into three categories:

### Codebase Patterns

Conventions, architectural decisions, and structural patterns discovered during this workstream that agents should know for future work. Examples:

- "All API routes use zod validation middleware"
- "Database migrations live in `db/migrations/` and use timestamps"
- "Error handling follows the Result pattern in `src/lib/result.ts`"

### Lessons Learned

Mistakes made, surprises encountered, things caught in review, and approaches to do differently next time. Examples:

- "The ORM doesn't auto-cascade deletes — must handle manually"
- "Verification caught a missing null check that would have caused a runtime crash"
- "Plan underestimated complexity of auth integration — took 3x longer than expected"

### Agent Performance

How well agents performed during this workstream. This is informational and does NOT get written to knowledge files:

- Budget usage (tokens, API calls, time)
- Useful findings from sub-agents
- Items agents missed that were caught in review
- Suggestions for improving agent workflows

## Step 4: Update Knowledge Files

Create the `.rival/learning/` directory if it does not exist:

```
mkdir -p .rival/learning
```

### Update `codebase-patterns.md`

Append new codebase patterns to `.rival/learning/codebase-patterns.md`.

If the file does not exist, create it with this header:

```markdown
# Codebase Patterns

Conventions and patterns discovered during workstreams. Referenced by agents during planning and building.
```

Then append entries in this format:

```markdown
## <Category>
Last updated: <date>
Updated by workstream: <id>

- <pattern description>
- <pattern description>
```

### Update `lessons-learned.md`

Append new lessons to `.rival/learning/lessons-learned.md`.

If the file does not exist, create it with this header:

```markdown
# Lessons Learned

Mistakes, surprises, and review catches from past workstreams. Referenced by agents to avoid repeating errors.
```

Then append entries in this format:

```markdown
## <date>: <workstream-name>

- <lesson>
- <lesson>
```

### Rules for updating knowledge files

- **Append only** — never replace or remove existing entries.
- **Detect duplicates** — before appending, compare new entries against existing content. Skip any entry that is substantively the same as an existing one (not just exact string match — check semantic similarity).
- **Consolidate when large** — if a knowledge file exceeds 200 lines, consolidate and summarize older entries to keep the file manageable. Group related patterns, merge redundant lessons, and compress verbose entries. Preserve all unique information.
- **Create with headers** — if a file does not exist, create it with the appropriate header before appending.

## Step 5: Present to User

Display the extracted knowledge to the user in a clear summary:

1. Show codebase patterns that will be added (with category grouping).
2. Show lessons learned that will be added.
3. Show agent performance notes (informational only).
4. Show any entries that were detected as duplicates and will be skipped.

Ask the user if they want to edit anything before committing the changes. Wait for confirmation before writing to knowledge files.

## Step 6: Update State

After the user confirms and knowledge files are written:

- Set the workstream status to `archived` in `.rival/workstreams/<id>/state.json`.
- Record the retro completion timestamp.

## Edge Cases

- **User runs retro before verify** — this is allowed. Read whatever artifacts exist. If `verification.md` is missing, note that verification was not performed and extract lessons only from plan, build log, and git diff.
- **Knowledge files don't exist** — create them with the appropriate headers (see Step 4) before appending entries.
- **Duplicate lesson from previous workstream** — detect substantively similar entries in existing knowledge files and skip them. Report skipped duplicates to the user.
- **User rejects changes in Step 5** — do not write to knowledge files. Changes remain on disk only if the user explicitly asks to save partial results. Do not update workstream state to `archived`.
</content>
</invoke>