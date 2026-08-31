# StorOps

**English** | [简体中文](README.zh-CN.md)

**Storage Operations for AI Agents.**

> See where your storage goes. Understand why. Move what matters. Clean what doesn't.

StorOps is an agent skill (`storops`) that lets AI coding agents — Claude Code,
Codex, OpenCode, etc. — safely understand and manage local storage on Windows.

It is **not** another disk analyzer and **not** another disk cleaner. WizTree
already answers "what is taking up space." StorOps answers the questions after
that: *what is this, why is it here, can it be deleted, should it be moved,
where to, how to do it safely, and how to verify it worked.*

```text
Discover → Understand → Diagnose → Recommend → Plan → Execute → Verify
 WizTree     Identify     Analyze
```

See [`docs/DESIGN.md`](docs/DESIGN.md) for the full product design and
[`SKILL.md`](SKILL.md) for the agent behavior contract.

## Status

MVP, Windows-only. Built on top of [WizTree](https://diskanalyzer.com/) as the
storage-discovery backend — StorOps never re-implements disk/MFT scanning, and
never drives the WizTree GUI (no automation, screenshots, or OCR): it only
calls `WizTree64.exe` from the command line and parses its CSV export.

## Requirements

- Windows with NTFS volumes.
- [WizTree](https://diskanalyzer.com/) installed (`WizTree64.exe` on `PATH`,
  in a standard install location, or pointed to via `$env:STOROPS_WIZTREE_PATH`).
- PowerShell 5.1+ (Windows PowerShell) or PowerShell 7+.
- Admin privileges are optional but recommended: WizTree's `/admin=1` mode
  reads the NTFS MFT directly and is dramatically faster/more complete than a
  standard scan.

## Installation

StorOps is a plain agent skill: a directory with a `SKILL.md` at its root,
discovered by name and description rather than invoked as a slash command. No
build step and no dependencies to install — the agent reads `SKILL.md` to
decide when to use the skill, then invokes the PowerShell scripts under
`scripts/` directly. The only runtime requirement is WizTree, see
[Requirements](#requirements) above.

### Ask your agent to install it (recommended)

Paste this into any AI coding agent chat (Claude Code, Codex, Cursor, etc.)
and let it figure out the right method for your setup:

```text
Install the "storops" agent skill from https://github.com/tzzs/storops
using whichever method fits the agent I'm running in, then confirm it
loaded.
```

### Any skill-aware agent — `npx skills add`

[`skills`](https://www.npmjs.com/package/skills) is a community CLI that
installs a `SKILL.md` from any public GitHub repo into an agent's skills
directory (`.claude/skills/`, `.agents/skills/`, etc.):

```bash
npx skills add tzzs/storops
# or, to install for every project on this machine:
npx skills add tzzs/storops -g
```

### Claude Code — plugin marketplace

The repo carries its own `.claude-plugin/marketplace.json`, so it can be added
as a marketplace and installed directly from inside Claude Code:

```text
/plugin marketplace add tzzs/storops
/plugin install storops@storops
```

### Codex — skill installer

Codex ships an official `skill-installer` skill that installs any skill from a
GitHub URL. From inside Codex:

```text
$skill-installer install https://github.com/tzzs/storops
```

### Manual

Clone it directly into a skills directory the agent scans:

```bash
# Project-level (this checkout only)
git clone https://github.com/tzzs/storops.git .claude/skills/storops

# Personal (all projects)
git clone https://github.com/tzzs/storops.git ~/.claude/skills/storops
```

## Layout

```text
SKILL.md            agent behavior contract (when/how to use this skill)
docs/DESIGN.md       full design brief (source of truth for intent/scope)
rules/               declarative identification + risk rules (YAML)
scripts/             PowerShell entry points, one per capability
scripts/lib/         shared PowerShell modules (WizTree invocation, rule
                     matching, risk classification, plan/verification helpers)
tests/               smoke tests for the rule engine and CSV parsing
```

## Safety model

Every capability sits in exactly one of three tiers:

| Tier | Capabilities | Confirmation |
|---|---|---|
| **Read** | scan, inspect, search, identify, analyze | none — safe to run freely |
| **Plan** | cleanup-plan, migrate-plan | none — produces a plan, touches nothing |
| **Write** | migrate-execute, cleanup-execute, junction creation | **required**, always |

Nothing that deletes, moves, renames, or reconfigures anything ever runs
without an explicit, itemized plan being shown to the user first, and nothing
in the `CRITICAL` risk tier (Windows, `Program Files`, unknown system paths,
user documents, etc.) is ever offered for automatic deletion.

## Quick start (agent-driven)

An agent following `SKILL.md` will typically:

```powershell
# 1. Read-only: see the big picture
pwsh scripts/scan.ps1 -Path C:\

# 2. Read-only: drill into a large, unidentified consumer
pwsh scripts/inspect.ps1 -Path C:\Users\me\AppData\Local

# 3. Read-only: attach meaning to what was found
pwsh scripts/identify.ps1 -Path C:\Users\me\.lmstudio\models

# 4. Plan-only: build an itemized, risk-classified cleanup plan
pwsh scripts/cleanup-plan.ps1 -MaxRisk low

# 5. Write, only after the user approves the plan from step 4
pwsh scripts/cleanup-execute.ps1 -PlanFile .\storops-cleanup-plan.json -Confirm

# 6. Plan-only: build a migration plan for a big, movable directory
pwsh scripts/migrate-plan.ps1 -Path C:\Users\me\.lmstudio\models -Destination E:\AI\LMStudio\models

# 7. Write, only after the user approves the plan from step 6 — always verified
pwsh scripts/migrate-execute.ps1 -PlanFile .\storops-migrate-plan.json -Confirm

# 8. Read-only: re-check the migration's result at any later time
pwsh scripts/verify.ps1 -ResultFile .\storops-migrate-result.json
```

## License

MIT — see [`LICENSE`](LICENSE).
