---
name: rival-status
description: "Show status of all Rival workstreams. Usage: `/rival-status`."
metadata:
  short-description: Workstream dashboard and status reporter
---

# Rival Status — Workstream Dashboard

You are the Rival status reporter. Your job is to show the current state of all workstreams and guide the user on next steps. You run inline in the current conversation.

## Process

### Step 1: Read Configuration

Read `.rival/config.json`. If missing:
> "Rival isn't configured for this project. Run `/rival-init` first."

### Step 2: Scan Workstreams

Use Glob to find all workstream state files:
```
.rival/workstreams/*/state.json
```

If no workstreams found:
> "No workstreams found. Start planning a feature with:
> `/rival-plan <describe your feature>`"

### Step 3: Read All State Files

For each `state.json` found, read it and extract:
- `id` — workstream identifier
- `feature` — feature description
- `phase` — current phase
- `created` — creation timestamp
- Last history entry timestamp (most recent activity)

### Step 4: Display Status

Separate workstreams into active (non-archived) and archived.

#### Active Workstreams

Display as a table:

```
## Active Workstreams

| Workstream | Feature | Phase | Started | Last Activity |
|------------|---------|-------|---------|---------------|
| oauth2-auth-20260214 | Add OAuth2 authentication | plan-approved | 2026-02-14 | 2026-02-14 |
| user-avatars-20260214 | Add user profile avatars | building | 2026-02-14 | 2026-02-14 |
```

For each active workstream, show a progress bar:

```
oauth2-auth-20260214: ████░░░░ plan-approved (4/8)
user-avatars-20260214: ██████░░ building (6/8)
```

Phase progress mapping:
- `planning` → 2/8
- `plan-approved` → 4/8
- `building` → 6/8
- `build-complete` → 7/8
- `verifying` → 7/8
- `archived` → 8/8

#### Archived Workstreams

```
## Archived Workstreams

| Workstream | Feature | Completed |
|------------|---------|-----------|
| api-rate-limiting-20260210 | Add API rate limiting | 2026-02-12 |
```

### Step 5: Project Config Summary

```
## Project Configuration

- **Workspace:** multi-repo (47 repos indexed)
- **Languages:** C# (30) | TypeScript (12) | Python (5)
- **Experts:** azure, ef-core, service-bus
- **Review:** Codex CLI ✓ Available
- **Knowledge sources:** wiki
```

After the project config summary, read `.rival/learning/*.md` files and display a knowledge status line:

```
Knowledge: Last retro: <date> (<N> patterns, <N> lessons)
```

Read `.rival/learning/*.md` to get this info. If no knowledge files exist, show:
```
Knowledge: No retros recorded yet.
```

### Step 6: Suggest Next Steps

For each active workstream, suggest the next command based on its phase:

```
## Next Steps

- **oauth2-auth-20260214** → `/rival-execute` (plan is approved, ready for execution)
- **user-avatars-20260214** → Building in progress
```

Phase-to-next-command mapping:
- `planning` → "`/rival-plan`"
- `plan-approved` → "`/rival-execute`"
- `building` → "Building in progress"
- `build-complete` → "`/rival-verify`"
- `verifying` → "Verification in progress"
- `archived` → "`/rival-retro` (if knowledge not updated yet)"

## Important Notes

- This is a read-only status command — it doesn't modify any state
- Keep the output concise but informative
- If there are no active workstreams, emphasize how to start one
- The progress bar is approximate — some phases take longer than others
