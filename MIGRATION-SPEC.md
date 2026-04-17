# Rival Plugin: Claude Code → Codex CLI Migration Spec

This document is the **contract** for porting Rival skills from Claude Code to Codex CLI. Every ported skill must follow the rules below so the plugin behaves consistently across the suite.

## 1. Mental Model

Codex skills work almost identically to Claude skills:

- A `SKILL.md` with YAML frontmatter is dropped into `skills/<skill-name>/`.
- The model reads `SKILL.md` as instructions when the skill is invoked.
- The model uses its available tools (Bash, Read, Write, etc.) to execute those instructions.

The one material difference is **sub-agent spawning**. Claude Code has a first-class `Agent` tool with a `subagent_type` parameter. Codex CLI has TOML-based custom agents, but invoking named custom agents from within a session is currently unreliable (GitHub issue openai/codex#15250). **Our workaround:** shell out to `codex exec` with the agent's system prompt inlined.

This pattern works because `codex exec` supports `--ephemeral` (no session persistence) and `-o` (write last message to file) — every agent call becomes a self-contained subprocess.

## 2. Directory Layout

```
rival-plugin-codex/
├── .codex-plugin/plugin.json            # plugin manifest
├── .agents/plugins/marketplace.json     # marketplace manifest
├── skills/                               # ported skills
│   ├── rival-plan/SKILL.md
│   ├── rival-execute/SKILL.md
│   └── ...
├── references/                           # agent system prompts (shared, runtime-neutral)
│   ├── researcher.md
│   ├── expert-researcher.md
│   ├── pattern-detector.md
│   ├── code-explorer.md
│   ├── security-analyzer.md
│   └── skeptical-reviewer.md
└── scripts/
```

Skills read agent prompts from `references/` via an absolute path computed at runtime (see §5).

## 3. Skill Frontmatter

Codex uses the same YAML frontmatter as Claude. Port as-is, with these changes:

- Remove `user-invocable: true` — Codex doesn't use that field.
- Remove `argument-hint: ...` — not part of the Codex skill spec.
- Keep `name:` and `description:` — these drive skill discovery.
- Optionally add a `metadata.short-description` field (Codex convention).

**Example — before (Claude):**
```yaml
---
name: rival-plan
description: Plan a feature. Researches best practices, explores codebase, synthesizes self-contained execution plan with auto-review.
user-invocable: true
argument-hint: <feature-description> [--light] [--discussion] [--no-questions]
---
```

**After (Codex):**
```yaml
---
name: rival-plan
description: Plan a feature. Researches best practices, explores codebase, synthesizes self-contained execution plan with auto-review. Usage: `/rival-plan <feature-description> [--light] [--discussion] [--no-questions]`.
metadata:
  short-description: Research + analysis + planning orchestrator
---
```

The argument hint is folded into `description` so the user can still see it.

## 4. Sub-Agent Spawn Translation

### 4.1 Canonical Mapping

| Claude (source) | Codex (target) |
|---|---|
| `Agent(subagent_type="rival:researcher", prompt="...")` | `codex exec --full-auto --ephemeral -o <summary-path> "$(cat <ref>) + $prompt"` |
| Parallel multi-agent dispatch (single message with multiple Agent calls) | Backgrounded shell processes + `wait` |
| Sequential agent dispatch | Sequential `codex exec` calls |
| `Agent(subagent_type="rival:skeptical-reviewer", ...)` | Same pattern against `references/skeptical-reviewer.md` |

### 4.2 Single Agent Spawn

**Claude pattern:**
```
Agent(
  subagent_type="rival:researcher",
  description="Research: <feature>",
  prompt="
    ## Feature Request (NORTH STAR)
    <enriched feature>
    ## Stack
    <stack>
    ## Output Path
    .rival/workstreams/<id>/agent-outputs/01-researcher.md
    Write findings to Output Path. Return 3-5 line summary.
  "
)
```

**Codex equivalent — in SKILL.md, instruct the model to run:**
```bash
PLUGIN_ROOT="$(find-plugin-root)"   # see §5 for discovery
AGENT_PROMPT="$(cat "$PLUGIN_ROOT/references/researcher.md")"
WS_DIR=".rival/workstreams/<id>"
OUT_ABS="$(pwd)/$WS_DIR/agent-outputs"
mkdir -p "$OUT_ABS"

codex exec --full-auto --ephemeral \
  -o "$OUT_ABS/01-researcher-summary.md" \
  "$AGENT_PROMPT

## Feature Request (NORTH STAR)
<enriched feature>

## Stack
<stack>

## Output Path
$OUT_ABS/01-researcher.md

Write findings to Output Path. Return 3-5 line summary."
```

The model reading SKILL.md will run this via Bash.

### 4.3 Parallel Spawns (Phase 3 research, Phase 4.1 execute waves)

Background each spawn, collect PIDs, wait:

```bash
PIDS=()

codex exec --full-auto --ephemeral \
  -o "$OUT_ABS/01-researcher-summary.md" \
  "$(cat $PLUGIN_ROOT/references/researcher.md)
<inputs for researcher>" &
PIDS+=($!)

codex exec --full-auto --ephemeral \
  -o "$OUT_ABS/02-expert-researcher-azure-summary.md" \
  "$(cat $PLUGIN_ROOT/references/expert-researcher.md)
<inputs for expert-researcher, domain=azure>" &
PIDS+=($!)

# Wait for all
for pid in "${PIDS[@]}"; do wait "$pid"; done
```

Each subprocess writes its full output file (`01-researcher.md`, `02-expert-researcher-azure.md`) to `$OUT_ABS` via its own Write tool during execution. The `-o` flag captures the last message separately as a summary.

### 4.4 Sequential Spawns (Phase 4 pattern-detector → code-explorer → security-analyzer)

Just call them one after another, waiting between:

```bash
codex exec --full-auto --ephemeral \
  -o "$OUT_ABS/03-pattern-detector-summary.md" \
  "$(cat $PLUGIN_ROOT/references/pattern-detector.md)
<inputs>"

codex exec --full-auto --ephemeral \
  -o "$OUT_ABS/04-code-explorer-summary.md" \
  "$(cat $PLUGIN_ROOT/references/code-explorer.md)
<inputs>"

codex exec --full-auto --ephemeral \
  -o "$OUT_ABS/05-security-analyzer-summary.md" \
  "$(cat $PLUGIN_ROOT/references/security-analyzer.md)
<inputs>"
```

### 4.5 Error Handling

- **`codex exec` returns non-zero**: check `$?` after the call. Report to user, don't silently continue.
- **Output file not written**: after `codex exec` completes, verify the expected `<N>-<agent>.md` exists. If missing, the agent didn't follow the write-to-path instruction — surface this to the user.
- **Parallel spawn: one fails**: the others may still be running. Collect all exit codes first, then decide.

## 5. Discovering the Plugin Root

Skills need the absolute path to `references/` to pass to `codex exec`. The plugin root is discoverable via the skill's own install location.

Codex installs plugin skills at:
- `$CODEX_HOME/plugins/cache/<marketplace>/<plugin>/<version>/skills/<skill-name>/SKILL.md`
- OR `~/.codex/skills/<skill-name>/SKILL.md` (user-direct install)

The SKILL.md instructs the model: discover the plugin root by walking up from the SKILL.md's own location two levels (`skills/<name>/SKILL.md` → `../../`). Concrete approach:

```bash
# In Codex, the model can get the current skill's working location via bash pwd chain,
# OR use this cross-install-path-tolerant discovery:
PLUGIN_ROOT="$(
  # Try the install cache layout first
  find "$HOME/.codex/plugins/cache" -type d -name "rival-plugin-codex*" 2>/dev/null |
    head -1
)"
# Fallback — user-direct install
if [ -z "$PLUGIN_ROOT" ]; then
  PLUGIN_ROOT="$(find "$HOME/.codex/skills" -name "SKILL.md" -path "*/rival-plan/*" 2>/dev/null |
    head -1 |
    xargs dirname |
    xargs dirname |
    xargs dirname)"
fi
```

Simpler: each ported SKILL.md tells the model to `find` the plugin's `references/` dir at runtime. We'll factor this into a shared shell helper in `scripts/find-plugin-root.sh`.

## 6. State Files — Unchanged

All file paths and JSON schemas stay identical:
- `.rival/config.json`
- `.rival/workstreams/<id>/state.json`
- `.rival/workstreams/<id>/plan.md`
- `.rival/workstreams/<id>/build-log.md`
- `.rival/workstreams/<id>/verification.md`
- `.rival/workstreams/<id>/agent-outputs/<NN>-<agent>.md`
- `.rival/learning/codebase-patterns.md`
- `.rival/learning/lessons-learned.md`

This is intentional: a user can init with Claude Rival and verify with Codex Rival (or vice versa). The state is runtime-neutral.

**One exception:** `config.json.paths.plugin_root` — on the Codex side this points to the Codex plugin install path, not the Claude plugin. Skills read this from config and use it as `$PLUGIN_ROOT` for the duration of the session.

## 7. Codex Review Path (rival-plan Phase 6, rival-verify Phase 4)

The Claude version has two paths:
- **Path A:** `codex exec` (primary)
- **Path B:** `Agent(subagent_type="rival:skeptical-reviewer")` (fallback when Codex unavailable)

In Codex, Path A stays the same (we ARE Codex — always available). Path B becomes another `codex exec` with the skeptical-reviewer prompt as fallback if the first Codex call fails.

Drop the "check if codex is on PATH" logic — it's always on PATH since we're running inside it.

## 8. What Does NOT Change

Every skill must preserve:
- Phase names, numbers, and ordering
- State transitions (`plan-approved`, `building`, `build-complete`, `verified`, `archived`)
- User-facing prompts and output formats
- All edge-case handling
- Workstream ID format (`<slug>-YYYYMMDD`)

Behavior parity is required. Only the execution mechanism for agent spawning changes.

## 9. Testing Approach

After porting:
1. Install the plugin in a scratch Codex workspace: `codex exec "install rival from github.com/muhammadut/rival-plugin-codex"`.
2. Run `/rival-init` on a small repo — confirms config + directory setup.
3. Run `/rival-plan <small-feature> --light` — confirms basic flow (light skips agents).
4. Run `/rival-plan <medium-feature>` — confirms parallel research spawn + sequential analysis.
5. Compare `.rival/` output to Claude-side output on same feature — files should match schema.

## 10. Sub-Agent Checklist for Porters

When you port a skill:

- [ ] Read the source `skills/<name>/SKILL.md` from `/Users/tariqusama/Documents/plugin_development/rival_plugin/`
- [ ] Write the ported version to `/Users/tariqusama/Documents/plugin_development/rival-plugin-codex/skills/<name>/SKILL.md`
- [ ] Remove `user-invocable` and `argument-hint` from frontmatter
- [ ] Fold argument hints into `description` field as "Usage: ..."
- [ ] Translate every `Agent(subagent_type="rival:<name>", prompt=...)` call to the `codex exec` shell-out pattern (§4)
- [ ] Preserve all phase numbers, state transitions, edge cases, user prompts
- [ ] For references to `rival:<agent-name>`, swap to `references/<agent-name>.md` in `$PLUGIN_ROOT`
- [ ] Remove the "Codex available / unavailable" fork in review paths — Codex is the runtime
- [ ] Do NOT invent new features or refactor beyond the translation
- [ ] Do NOT change state schemas or file paths
