# StorOps

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
pwsh scripts/cleanup-plan.ps1 -Path C:\ -MaxRisk Low

# 5. Write, only after the user approves the plan from step 4
pwsh scripts/cleanup-execute.ps1 -PlanFile .\storops-cleanup-plan.json -Confirm

# 6. Plan-only: build a migration plan for a big, movable directory
pwsh scripts/migrate-plan.ps1 -Path C:\Users\me\.lmstudio\models -Destination E:\AI\LMStudio\models

# 7. Write, only after the user approves the plan from step 6 — always verified
pwsh scripts/migrate-execute.ps1 -PlanFile .\storops-migration-plan.json -Confirm
```

## License

MIT — see [`LICENSE`](LICENSE).
