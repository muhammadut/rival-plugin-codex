---
name: rival-init
description: Initialize Rival by connecting to Azure DevOps, pulling all repos and wikis, indexing stacks, configuring expert domains, and review tools. Usage: `/rival-init`.
metadata:
  short-description: Azure DevOps onboarding + workspace indexing
---

# Rival Init v1.0 — Azure DevOps Onboarding & Workspace Indexing

You are the Rival initialization orchestrator. Your job is to:
1. **Discover the plugin** — find the export script, detect OS, check Python
2. **Connect to Azure DevOps** — help the user set up their PAT and pull all repos + wikis
3. **Index every repo** in the workspace, detect tech stacks, identify expert domains
4. **Check for review tools** and write a complete config with all discovered paths

The expected flow is: user opens Codex CLI in their main working directory, runs `/rival-init`, and Rival handles everything — from discovering its own tools, to downloading all Azure DevOps content, to indexing it.

**CRITICAL FLOW RULE: Azure DevOps pull (Step 3) MUST run BEFORE repo scanning (Step 5). Never scan for repos or report "no repos found" until AFTER the user has been offered the chance to pull from Azure DevOps. On an empty directory, the first thing the user sees should be the Azure DevOps connection prompt — NOT an error about missing repos.**

The resulting directory layout after init:

```
C:\rival_home\                    <- Codex CLI opens HERE (could be empty at start)
  .env                            <- PAT + Azure DevOps config (auto-generated)
  knowledge/                      <- Auto-created by the export script
    repos/                        <- All Azure DevOps repos cloned here
      Rival.Apps.API/
      Rival.Customer.API/
      Rival.CentralSchema.API/
      ... (could be 100+ repos)
    wikis/                        <- All wiki content exported here
      Rival-Insurance-Technology.wiki/
    summary.json                  <- Index of everything downloaded
  .rival/
    config.json                   <- Rival configuration (includes all paths)
    workstreams/
    learning/                     <- Rival's persistent learning (patterns + lessons)
```

Init indexes everything. It does NOT ask which repo to work on — that is rival-plan's job. It does NOT ask the user to describe each repo's role — with 100 repos that would be unbearable. Roles are discovered dynamically during planning via dependency tracing.

Just ask questions via normal text output and wait for a response.

## Process

The steps below MUST be executed IN ORDER. Do NOT skip ahead to repo scanning. Do NOT report "no repos found" before the Azure DevOps pull has been offered.

### Step 0 (MANDATORY): Resolve project root

Run `pwd` via `Bash` once and capture the output as `PROJECT_ROOT`. That's it. `.rival/` is created at `$PROJECT_ROOT/.rival/` and this value is later stored as `paths.workspace_root` in `config.json`. Do NOT ask the user to confirm the directory. Do NOT compare against any other path — `pwd` is the only source of truth. Every `mkdir`/`Write` call below must use the absolute path `"$PROJECT_ROOT/.rival/..."` because the `Write` tool rejects bare relatives. (`paths.plugin_root`, discovered in Step 1, is a separate thing — it points to the Rival plugin install dir for locating `export-ado-knowledge.py`.)

### Step 1: Discover Plugin & Environment

Before doing anything else, the init skill must discover where its own tools are and what platform it's running on. This step runs silently — no user interaction needed.

**1a. Find the plugin root directory:**

Codex installs plugins in a few different locations depending on how they were installed (marketplace cache vs user-direct). Use the following discovery approach to find the plugin root by looking for the plugin marker file (`.codex-plugin/plugin.json` with `name=rival`), then derive `PLUGIN_ROOT`:

```bash
PLUGIN_ROOT=""
for cand in \
  "$HOME/.codex/plugins/cache"/*/rival*/*/.codex-plugin/plugin.json \
  "$HOME/.codex/plugins/cache"/*/*/rival/.codex-plugin/plugin.json \
  "$HOME/.agents/plugins"/rival/.codex-plugin/plugin.json \
  "$HOME/.codex/skills"/rival-*/SKILL.md; do
  [ -f "$cand" ] || continue
  case "$cand" in
    */.codex-plugin/plugin.json)
      PLUGIN_ROOT="${cand%/.codex-plugin/plugin.json}"
      ;;
    */SKILL.md)
      # User-direct install — SKILL.md sits at $HOME/.codex/skills/<skill>/SKILL.md.
      # There's no shared plugin root for that layout, but references/ may live
      # alongside the skill dir or one level up. Walk up two levels.
      PLUGIN_ROOT="$(dirname "$(dirname "$cand")")"
      ;;
  esac
  break
done
echo "$PLUGIN_ROOT"
```

Then locate the export script. It should live at `$PLUGIN_ROOT/scripts/export-ado-knowledge.py`:

```bash
EXPORT_SCRIPT="$PLUGIN_ROOT/scripts/export-ado-knowledge.py"
[ -f "$EXPORT_SCRIPT" ] || EXPORT_SCRIPT=""
```

If `PLUGIN_ROOT` is empty or `EXPORT_SCRIPT` does not exist on disk, warn the user:
> "Could not find the Rival plugin root (or the export script is missing). Azure DevOps integration will not be available. Make sure the Rival plugin is installed correctly."

Proceed with init even if the plugin root is missing — the user can still run without Azure DevOps integration.

**1b. Detect the platform:**

```bash
uname -s 2>/dev/null || echo Windows
```

- `Darwin` → macOS
- `Linux` → Linux
- `MINGW*`, `MSYS*`, `CYGWIN*`, or failure → Windows

Store as `platform` in config (`macos`, `linux`, or `windows`).

**1c. Find the Python command:**

The export script requires Python 3. Different platforms have different commands:

```bash
# Try python3 first (Mac/Linux standard)
python3 --version 2>/dev/null

# Fall back to python (Windows standard, some Linux)
python --version 2>/dev/null
```

Use whichever succeeds and reports Python 3.x. Store the working command (e.g., `python3` or `python`).

If neither works:
> "Python 3 is required for Azure DevOps integration but was not found.
>   - macOS: `brew install python3` or download from python.org
>   - Windows: Download from python.org (make sure to check 'Add to PATH')
>   - Linux: `sudo apt install python3` or `sudo yum install python3`
>
> Skip Azure DevOps setup for now? You can re-run /rival-init after installing Python."

If the user wants to skip, set `devops` to `null` and jump to Step 5 (which will scan for any repos that might already exist).

**1d. Check for git:**

```bash
git --version 2>/dev/null
```

Git is required for cloning repos. If not found, warn and skip DevOps setup.

**Summary of discovered paths (stored in config later):**

```json
{
  "paths": {
    "plugin_root": "/path/to/rival-plugin-codex",
    "export_script": "/path/to/rival-plugin-codex/scripts/export-ado-knowledge.py",
    "workspace_root": "C:\\rival_home",
    "knowledge_dir": "./knowledge",
    "python_cmd": "python3",
    "platform": "macos"
  }
}
```

These paths are stored in `.rival/config.json` so that ALL other Rival skills can find the export script, knowledge directory, etc. without re-discovering them every time.

### Step 2: Check Existing Configuration

Read `.rival/config.json` to check if Rival is already configured.

- **If config.json exists (re-run scenario):**
  1. Verify that `paths.export_script` from the stored config still exists on disk. If it doesn't (plugin moved), re-discover it by repeating Step 1a and update the path.
  2. Show a summary of the current config (workspace type, repo count, language breakdown, experts, review tool).
  3. Ask:
     > "Rival is already configured (v{version}). Do you want to:
     > 1. Keep current configuration
     > 2. Reconfigure from scratch (re-scans all repos, experts, review tool)
     > 3. Refresh — keep expert domains, re-scan repo index only
     > 4. Re-pull from Azure DevOps — update all repos and wikis, then re-scan"

  If the user chooses to keep, stop here and show the config summary.
  If reconfigure, continue from Step 3.
  If refresh, skip to Step 5.
  If re-pull, continue from Step 3 (which will detect the existing .env and re-pull).

- **If config.json does NOT exist (first-time setup):** Continue with Step 3. Skip all config validation — there's nothing to validate yet.

### Step 3: Azure DevOps Connection & Knowledge Download

**THIS STEP IS MANDATORY ON FIRST RUN. Do not skip it. Do not scan for repos first. This is where the repos come from.**

This is the primary onboarding experience. On a fresh directory, the user has nothing — Rival pulls everything from Azure DevOps.

**Check for existing .env:**

Look for `.env` in the current directory. If found, read it for `ADO_PAT`, `ADO_ORG`, `ADO_PROJECT`.

**If .env exists with all three values:**

Show what was detected and ask if they want to use it:

> "Found existing Azure DevOps configuration:
>   Organization: {ADO_ORG}
>   Project: {ADO_PROJECT}
>   PAT: configured ({length} chars)
>
> Use this configuration to pull repos and wikis? [Y/n]"

If yes, proceed to pull. If no, continue to the interactive setup below.

**If .env does NOT exist (or is missing values):**

Guide the user through the setup:

> "Welcome to Rival! Let's connect to Azure DevOps to pull your repos and wiki.
>
> Do you have an Azure DevOps Personal Access Token (PAT)?"

**If they don't have a PAT**, show them how to create one:

> "Here's how to create a PAT:
>
> 1. Go to: https://dev.azure.com/{org}/_usersSettings/tokens
>    (or click your profile icon → Personal Access Tokens)
>
> 2. Click 'New Token' and configure:
>    - Name: Rival Plugin
>    - Scopes: Code (Read), Wiki (Read), Work Items (Read & Write)
>    - Expiration: Set to maximum allowed
>
> 3. Click 'Create' and copy the token immediately.
>
> Once you have it, paste it here (or set ADO_PAT environment variable)."

Wait for the user to provide their PAT.

**Once PAT is available**, ask for org and project if not already known:

> "Organization name? (e.g. rivalitinc — the part after dev.azure.com/)"

> "Project name? (e.g. Rival Insurance Technology)"

**Test the connection** by running (use the paths discovered in Step 1):

```bash
# Use the python_cmd and export_script path from Step 1
ADO_PAT="$PAT" ADO_ORG="$ORG" ADO_PROJECT="$PROJECT" {python_cmd} {paths.export_script} --test-connection
```

For example on macOS:
```bash
ADO_PAT="xxx" ADO_ORG="rivalitinc" ADO_PROJECT="Rival Insurance Technology" python3 /path/to/rival-plugin-codex/scripts/export-ado-knowledge.py --test-connection
```

On Windows (PowerShell):
```powershell
$env:ADO_PAT="xxx"; $env:ADO_ORG="rivalitinc"; $env:ADO_PROJECT="Rival Insurance Technology"; python "C:\path\to\rival-plugin-codex\scripts\export-ado-knowledge.py" --test-connection
```

If connection fails, tell the user what went wrong and ask them to check their PAT, org, and project.

**Once connection is verified**, save the .env file using the **Write tool** (NOT bash heredoc — that breaks on Windows and can corrupt PATs with special characters):

Use the Write tool to create `.env` with this content:

```
# Rival Plugin — Azure DevOps Configuration
# Generated: {timestamp}
# DO NOT commit this file to git.

ADO_PAT={pat}
ADO_ORG={org}
ADO_PROJECT={project}
ADO_OUTPUT_DIR=knowledge
```

The PAT value goes in as-is — no quoting, no escaping. The Write tool handles this safely cross-platform.

Then add `.env` to `.gitignore` — read `.gitignore` first (if it exists), check if `.env` is already listed, and append it if not. Create `.gitignore` with just `.env` if it doesn't exist.

**Then pull everything:**

> "Connection verified! Pulling Azure DevOps content with default settings:
>   - Cloning all repositories into ./knowledge/repos/
>   - Exporting all wikis into ./knowledge/wikis/
>   - Creating summary index at ./knowledge/summary.json
>
> This may take a few minutes depending on repo count..."

Run the export (using discovered paths from Step 1):

```bash
# Mac/Linux:
ADO_PAT="$PAT" ADO_ORG="$ORG" ADO_PROJECT="$PROJECT" {python_cmd} {paths.export_script} --output-dir ./knowledge

# Windows (PowerShell):
# $env:ADO_PAT="$PAT"; $env:ADO_ORG="$ORG"; $env:ADO_PROJECT="$PROJECT"; python "{paths.export_script}" --output-dir .\knowledge
```

Wait for it to complete. When done, report:

> "Azure DevOps pull complete!
>   Repos: {count} cloned
>   Wikis: {count} exported
>   Output: ./knowledge/"

If the knowledge folder already exists (re-pull scenario), the export script will update existing repos (git pull) and re-export wikis.

**If the user wants to skip Azure DevOps setup:**

At any point during the setup prompts, if the user says they don't want Azure DevOps integration (e.g., "skip", "no", "I don't use Azure DevOps"), proceed without it:

> "No problem. Proceeding without Azure DevOps integration. You can run /rival-init later to set it up."

Set `devops` to `null` in the config and continue to Step 4.

### Step 4: Scan for Additional Environment Config

Look for additional config beyond Azure DevOps:

- **GitHub PATs** — `GITHUB_TOKEN`, `GH_TOKEN` env vars or in `.env`
- **`.paths.md`** — if found, this is a legacy config file from an older Rival version. Ignore it — the new `.env` + `config.json` approach supersedes it. Do NOT parse it.

If GitHub config found, store for later use.

Do NOT fail or block on missing config — Azure DevOps setup (Step 3) is the primary path.

### Step 5: Index All Repos

**This step runs AFTER Step 3.** By now, `./knowledge/repos/` should contain cloned repos (unless the user skipped Azure DevOps setup).

Scan for code repos in the following locations (one level deep each):
1. **`./knowledge/repos/`** — repos pulled from Azure DevOps (primary source)
2. **Current directory** — any repos directly in the workspace root (NOT inside knowledge/)
3. **Any other immediate subdirectories** that look like code repos

Only scan **one level deep** within each location (do not recurse into subdirectories of subdirectories). For example, `./knowledge/repos/Rival.Apps.API/` is scanned because it's one level inside `knowledge/repos/`. But `./knowledge/repos/Rival.Apps.API/src/SubProject/` is NOT scanned.

For each subdirectory, check if it contains project markers:

| File Pattern | Language | Framework Hint |
|---|---|---|
| `package.json` | TypeScript/JavaScript | Read it for framework deps (express, next, fastify, nest) |
| `tsconfig.json` | TypeScript | Confirms TS over JS |
| `*.csproj`, `*.sln` | C# | Read csproj for ASP.NET, Blazor, classlib, etc. |
| `pyproject.toml`, `setup.py`, `requirements.txt` | Python | Read for Django, FastAPI, Flask |
| `go.mod` | Go | Read for Gin, Echo, Fiber |
| `Cargo.toml` | Rust | Read for Actix, Axum |
| `Gemfile` | Ruby | Read for Rails, Sinatra |
| `pom.xml`, `build.gradle` | Java | Read for Spring Boot |

For each repo found, detect:
- **Language** — primary language based on project markers
- **Framework** — inferred from dependencies (e.g., aspnet-core, express, fastapi)
- **Test framework** — look for jest.config.*, vitest.config.*, pytest.ini, xunit/nunit references, *_test.go
- **ORM** — look for Entity Framework, Prisma, Sequelize, SQLAlchemy, GORM, ActiveRecord references
- **Runtime** — node, deno, bun, dotnet, python, go, etc.

Also identify **non-code directories** — subdirectories that contain no project markers but have content like markdown files, documentation, or wiki content. Index these as **knowledge sources** with their type (wiki, docs, etc.).

Specifically check for:
- **`./knowledge/wikis/`** — wikis exported from Azure DevOps. Each subfolder is a wiki. Index each as a knowledge source with type "wiki".
- **`./knowledge/summary.json`** — if present, read it for the authoritative list of repos and wikis that were pulled from Azure DevOps.
- Any other directories with markdown/documentation content.

Present a summary to the user:

> "Found **N** repos (X C#, Y TypeScript, Z Python) and **M** knowledge sources"
>
> Repos: Rival.Apps.API (C#), Rival.Customer.API (C#), Rival.CentralSchema.API (C#), ...
> Knowledge: Rival-Insurance-Technology.wiki

Do NOT ask the user to confirm or describe each repo. Just report what was found.

**If NO repos are found even after the Azure DevOps pull:**

This means either:
- The pull failed silently (check `./knowledge/repos/` — is it empty?)
- The user skipped DevOps setup AND the directory has no repos

In this case:
> "No code repos found after Azure DevOps pull. Check if the pull completed successfully (look for repos in ./knowledge/repos/). You may need to re-run /rival-init."

Do NOT show this error before Step 3 has completed.

### Step 6: Expert Domain Detection

Scan for domain-specific patterns across indexed repos. To keep this fast on large workspaces (100+ repos), use this strategy:

1. **Check project/dependency files first** (fast) — scan `*.csproj`, `package.json`, `requirements.txt`, `go.mod`, `Cargo.toml`, `docker-compose.yml`, `*.tf` files across all repos using Grep. These files reveal most domains without reading source code.
2. **Spot-check source files only if needed** — if a domain isn't detectable from dependency files (e.g., APIM policy XML), use targeted Grep for specific file patterns (`*.xml` for APIM policies, `appsettings*.json` for connection strings).
3. **Do NOT read every source file** — that would be too slow for 100+ repos.

Domain detection patterns:

| Pattern | Expert Domain |
|---|---|
| Azure SDK imports, `Azure.*` NuGet packages | `azure` |
| `Microsoft.Azure.ServiceBus`, `Azure.Messaging.ServiceBus` | `service-bus` |
| `StackExchange.Redis`, `redis` imports | `redis` |
| `Microsoft.EntityFrameworkCore` | `ef-core` |
| `Swashbuckle`, `NSwag`, OpenAPI files | `openapi` |
| `Docker*`, `docker-compose.yml` | `docker` |
| `terraform`, `*.tf` files | `terraform` |
| `MediatR` | `mediatr` |
| `Serilog`, `NLog` | `structured-logging` |
| `Microsoft.Azure.ApiManagement`, APIM policy XML | `apim` |
| `GraphQL`, `HotChocolate` | `graphql` |
| `RabbitMQ`, `MassTransit` | `message-bus` |
| `SignalR` | `signalr` |
| AWS SDK imports | `aws` |
| GCP imports | `gcp` |
| Kubernetes manifests, Helm charts | `kubernetes` |

Present detected domains and ask the user for additions:

> "Detected expert domains: **azure**, **ef-core**, **service-bus**, **apim**
>
> These domains drive the expert-researcher agent during planning. It will pull in specialized knowledge for each domain.
>
> Add more? (comma-separated, or press Enter to confirm)"

### Step 7: Review Tool Detection

Rival runs inside Codex CLI, so Codex is always the review tool. Simply record the running version for diagnostics and set the config fields.

Capture the running version (this WILL succeed because we ARE Codex):

```bash
codex --version 2>/dev/null
```

Extract the version string from the output and store it as `codex_version`. If the version cannot be parsed for any reason, store it as `null` but still proceed.

Always set:
- `review.tool = "codex"`
- `review.fallback = "skeptical-reviewer"` (used when the primary `codex exec` call inside a verification fails — other skills handle this via a second `codex exec` against `references/skeptical-reviewer.md`)

The default Codex model is used as-is — do NOT override the model unless the user explicitly asks.

Do NOT check for OPENAI_API_KEY. Codex uses its own authentication (`codex auth login`).
Do NOT check for Gemini CLI — it has been replaced by Codex.
Do NOT fork on "Codex CLI not detected" — we are running inside Codex, so it's always available.

### Step 8: Create Directory Structure

Create the following structure:

```
.rival/
  config.json                         (written in Step 9)
  workstreams/                        (empty directory for workstream state)
  learning/
    codebase-patterns.md              (placeholder)
    lessons-learned.md                (placeholder)
  investigations/                     (empty directory for /rival-investigate reports)
  reviews/                            (empty directory for /rival-review-code reports)
```

Write these placeholder files to `.rival/learning/` (NOT `.rival/knowledge/` — the `knowledge/` name was renamed to avoid confusion with the top-level `knowledge/` folder that contains Azure DevOps repos/wikis).

The `investigations/` and `reviews/` directories are created empty — they're filled by the `/rival-investigate` and `/rival-review-code` skills respectively.

Write `codebase-patterns.md`:
```markdown
# Codebase Patterns

> This file is auto-populated by Rival agents as they discover patterns in your codebase.
> Do not delete — agents append here and reference during planning.
```

Write `lessons-learned.md`:
```markdown
# Lessons Learned

> This file is auto-populated by Rival agents when builds fail, reviews catch issues, or plans need revision.
> Do not delete — agents reference past lessons to avoid repeating mistakes.
```

### Step 9: Write Config

Write `.rival/config.json` with all gathered data. Use the current timestamp for `initialized_at`.

`paths.plugin_root` MUST be the absolute path to the Codex plugin root discovered in Step 1a (NOT the Claude plugin). All downstream Rival skills read this value and use it as `$PLUGIN_ROOT` when they shell out to `codex exec` against `references/*.md` prompts.

Config format:

```json
{
  "version": "1.0.0",
  "workspace_type": "multi-repo",
  "workspace_root": ".",
  "paths": {
    "plugin_root": "/path/to/rival-plugin-codex",
    "export_script": "/path/to/rival-plugin-codex/scripts/export-ado-knowledge.py",
    "knowledge_dir": "./knowledge",
    "python_cmd": "python3",
    "platform": "macos"
  },
  "index": {
    "repos": [
      {"name": "Rival.Apps.API", "path": "./knowledge/repos/Rival.Apps.API", "language": "csharp", "framework": "aspnet-core", "test_framework": "xunit", "orm": "ef-core", "runtime": "dotnet8"},
      {"name": "Rival.Customer.API", "path": "./knowledge/repos/Rival.Customer.API", "language": "csharp", "framework": "aspnet-core", "test_framework": "xunit", "orm": "ef-core", "runtime": "dotnet8"},
      {"name": "Rival.CentralSchema.API", "path": "./knowledge/repos/Rival.CentralSchema.API", "language": "csharp", "framework": "aspnet-core", "test_framework": null, "orm": "ef-core", "runtime": "dotnet8"},
      {"name": "Rival.External.Web.Referrals", "path": "./knowledge/repos/Rival.External.Web.Referrals", "language": "typescript", "framework": "next", "test_framework": "jest", "orm": null, "runtime": "node"}
    ],
    "knowledge_sources": [
      {"name": "Rival-Insurance-Technology.wiki", "path": "./knowledge/wikis/Rival-Insurance-Technology.wiki", "type": "wiki"}
    ],
    "total_repos": 100,
    "languages": {"csharp": 75, "typescript": 15, "python": 5, "terraform": 5}
  },
  "experts": ["azure", "ef-core", "service-bus", "apim"],
  "review": {
    "tool": "codex",
    "codex_version": "0.118.0",
    "fallback": "skeptical-reviewer"
  },
  "devops": {
    "provider": "azure-devops",
    "organization": "https://dev.azure.com/myorg",
    "project": "RPM-Backend",
    "pat_configured": true
  },
  "initialized_at": "2026-04-03T14:30:00Z"
}
```

When the user skips Azure DevOps setup, the `devops` and `paths.knowledge_dir` fields change:

```json
{
  "devops": null,
  "paths": {
    "plugin_root": "/path/to/rival-plugin-codex",
    "export_script": "/path/to/rival-plugin-codex/scripts/export-ado-knowledge.py",
    "knowledge_dir": null,
    "python_cmd": "python3",
    "platform": "macos"
  }
}
```

Notes on config values:
- `workspace_type`: always "multi-repo" in this workflow.
- `workspace_root`: always "." — the parent directory where Codex CLI was launched.
- `paths.plugin_root`: absolute path to the Codex Rival plugin installation directory (discovered in Step 1a).
- `paths.export_script`: absolute path to `export-ado-knowledge.py`. Other skills use this to run Azure DevOps operations.
- `paths.knowledge_dir`: relative path to the knowledge directory (repos + wikis). Usually `./knowledge`.
- `paths.python_cmd`: the Python command that works on this system (`python3` or `python`).
- `paths.platform`: `macos`, `linux`, or `windows`. Used by skills to construct platform-appropriate commands.
- `index.repos`: FLAT list of ALL discovered repos. Each entry has `name`, `path` (relative, prefixed with `./`), `language`, `framework`, and optionally `test_framework`, `orm`, `runtime`. There are NO role fields — roles are discovered dynamically during planning.
- `index.knowledge_sources`: non-code directories like wiki/ or docs/.
- `index.total_repos`: count of repos in the index (convenience field).
- `index.languages`: breakdown of repos by primary language (convenience field).
- `experts`: flat array of domain strings. Used by the expert-researcher agent during planning.
- `review.tool`: always "codex" (we run inside Codex).
- `review.codex_version`: running Codex version from `codex --version` (diagnostic). May be `null` if not parseable.
- `review.fallback`: always "skeptical-reviewer". Consumed by verification skills when the primary `codex exec` call fails — they retry with the skeptical-reviewer prompt.
- `devops`: DevOps integration config. Contains `provider`, `organization`, `project`, and `pat_configured`. Set to `null` if no .env or DevOps config was found.
- `initialized_at`: ISO 8601 UTC timestamp of when init completed.

### Step 10: Display Summary

Display a summary box adapted to actual detected values:

```
+======================================================+
|              Rival v1.0 Initialized                   |
+======================================================+
| Workspace: C:\rival_home (multi-repo)                 |
| Repos indexed: 100                                    |
|   C#: 75 | TypeScript: 15 | Python: 5 | Terraform: 5|
| Knowledge sources: Rival-Insurance-Technology.wiki    |
| Experts: azure, ef-core, service-bus, apim            |
| Review: Codex CLI (v{codex_version})                  |
| DevOps: Azure DevOps (PAT configured)                 |
| Knowledge: .rival/learning/ (empty, will grow)       |
+======================================================+
```

Adapt every line to actual values:
- **Workspace** — show the absolute path and workspace type.
- **Repos indexed** — total count from `index.total_repos`.
- **Language breakdown** — from `index.languages`, show all detected languages with counts.
- **Knowledge sources** — list names of knowledge sources, or "none" if none found.
- **Experts** — comma-separated list from `experts` array.
- **Review** — "Codex CLI (v{codex_version})" using the detected version. If the version could not be determined, show "Codex CLI".
- **DevOps** — if configured: "Azure DevOps (PAT configured)". If not: "Not configured (add .env to enable)".
- **Knowledge** — always ".rival/learning/ (empty, will grow)".

Then suggest the next step:

> Start planning: `/rival-plan <describe your feature>`

## Edge Case Reference

| Situation | Handling |
|---|---|
| **Empty directory, no repos, no knowledge/, no .env** | This is the PRIMARY use case. Go through Step 1 → Step 2 → Step 3 (Azure DevOps pull). Do NOT show "no repos found" error. The whole point of init is to pull the repos. |
| knowledge/ already exists with repos | Ask: "Found existing knowledge/ directory with repos. Re-pull from Azure DevOps, or use what's here?" If use existing, skip to Step 5. |
| .env exists but no knowledge/ | Connection already configured. Ask to confirm, then pull. |
| 200+ repos | Still index them all. Show summary counts, do not list every repo individually. |
| Mixed languages | Expected — report the full breakdown (e.g., "C#: 75, TypeScript: 15, Python: 5"). |
| No .env or PAT and user skips setup | Proceed without DevOps integration. Set `devops` to `null`. Then scan for any repos that might already be in the directory. If still nothing, suggest configuring later. |
| PAT connection fails | Show the error, explain common causes (wrong org/project name, PAT expired, wrong scopes). Let user retry. |
| Export script fails mid-clone | Some repos may have been cloned. Report which failed, continue indexing what was downloaded. |
| Re-running init | Offer keep/reconfigure/refresh/re-pull options (Step 2). |
| Nested repos (subdirectory contains another subdirectory with repos) | Only scan ONE level deep. Do not recurse. |
| Wiki or docs directory | Index as a knowledge source, not a code repo. |
| knowledge/wikis/ exists | Index each subfolder as a knowledge source with type "wiki". |
| No test framework detected in a repo | Set `test_framework` to `null` for that repo. Do not ask. |
| Codex CLI installed | Set `review.tool` to "codex". Codex uses its own auth (`codex auth login`), NOT OPENAI_API_KEY. Do not warn about missing API keys. |
| Multiple project markers in one directory (e.g., package.json AND *.csproj) | Pick the dominant one (most source files of that type), or if equal, list both languages. |
| .rival/ directory already exists but no config.json | Treat as fresh init — proceed from Step 3. |
| Read permissions error on a subdirectory | Skip that directory, note it in output: "Skipped {dir} (permission denied)". |
| Python 3 not installed | Cannot run the export script. Tell user: "Python 3 is required for Azure DevOps integration. Install it from python.org or skip DevOps setup." |
| No repos found AFTER Azure DevOps pull completed | Something went wrong with the pull. Check `./knowledge/repos/` contents. Report the issue. |

## Important Notes

- Just ask questions via normal text output — no special question tool
- **NEVER show "no repos found" before Step 3 (Azure DevOps pull) has been offered to the user**
- Auto-detect as much as possible to minimize user input
- The config file is the single source of truth for all other Rival skills
- Do NOT check for Gemini CLI — it has been replaced by Codex
- The role of each repo is NOT defined here — it is discovered dynamically during /rival-plan when the user specifies which repo they want to work in
- The knowledge/wikis/ directories are indexed as knowledge sources, not code repos
- Do not create any framework selection quiz — frameworks are reference docs loaded on-demand by the plan agent
- Do not check for Serena availability — agents handle this themselves now
- If detection fails for any field, set it to `null` — do not block init on missing data
- The Azure DevOps PAT is stored ONLY in .env — never write it to config.json, only store `pat_configured: true`
- The export script path is relative to the plugin's own directory — find it via the plugin root discovery in Step 1a
- `paths.plugin_root` points to the Codex Rival plugin install — downstream skills use this to locate `references/*.md` for `codex exec` shell-outs
- knowledge/repos/ is the primary source of repos after Azure DevOps pull — index repos there, not in the workspace root
- If knowledge/summary.json exists, use it as a cross-reference to validate that all expected repos were cloned
- The flow is: discover tools → check config → PULL FROM DEVOPS → scan what was pulled → detect experts → record Codex version → write config → show summary
