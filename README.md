# StorOps

[![skills.sh](https://skills.sh/b/tzzs/storops)](https://skills.sh/tzzs/storops)

**English** | [简体中文](README.zh-CN.md)

**Storage Operations for AI Agents.**

> See where your storage goes. Understand why. Move what matters. Clean what doesn't.

StorOps is an agent skill (`storops`) that lets AI coding agents — Claude Code,
Codex, OpenCode, etc. — safely understand and manage local storage (Windows
today; Linux/macOS support is newer — see [Status](#status) below).

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

MVP. Windows is the most mature target, built on top of
[WizTree](https://diskanalyzer.com/) as the storage-discovery backend —
StorOps never re-implements disk/MFT scanning, and never drives the WizTree
GUI (no automation, screenshots, or OCR): it only calls `WizTree64.exe` from
the command line and parses its CSV export. Linux/macOS support is newer and
uses [gdu](https://github.com/dundee/gdu) (falling back to the system `du`)
behind the same scan-backend interface — see
[`docs/DESIGN.md`](docs/DESIGN.md) §4a. Identification rules for AI-model/
app/cache paths (`rules/ai-models.yaml`, `applications.yaml`, `caches.yaml`)
are still Windows-token-only; only the critical-system-path rules
(`rules/windows.yaml`/`linux.yaml`/`macos.yaml`) currently have per-platform
coverage.

## Requirements

- **Python 3.11+** — this is the primary implementation now (`src/storops/`);
  `python3`/`python` needs to be on `PATH`. No `pip install` is required for
  the common "cloned into a skills directory" install path (see
  [Installation](#installation) below) — `python -m storops` works straight
  out of the checkout.
- PowerShell 5.1+ (Windows PowerShell) on Windows, or PowerShell 7+ on
  Linux/macOS, **only if you use the `scripts/*.ps1` compatibility wrappers**
  (see [Two ways to call it](#two-ways-to-call-it) below) — the Python CLI
  itself has no PowerShell dependency.
- **Windows**: NTFS volumes, plus [WizTree](https://diskanalyzer.com/)
  installed (`WizTree64.exe` on `PATH`, in a standard install location, or
  pointed to via `$env:STOROPS_WIZTREE_PATH`). Admin privileges are optional
  but recommended: WizTree's `/admin=1` mode reads the NTFS MFT directly and
  is dramatically faster/more complete than a standard scan.
- **Linux/macOS**: [gdu](https://github.com/dundee/gdu) recommended (`brew
  install gdu` / `apt install gdu` / see its install docs) for a parallel,
  much faster scan; StorOps falls back to the system `du` automatically if
  `gdu` isn't found (with a one-time warning — `du` is noticeably slower on
  large trees). Point at a specific binary via `$env:STOROPS_GDU_PATH` if
  it's not on `PATH`. No elevation is ever required or auto-applied.

## Installation

StorOps is a plain agent skill: a directory with a `SKILL.md` at its root,
discovered by name and description rather than invoked as a slash command. No
build step and no `pip install` required — the agent reads `SKILL.md` to
decide when to use the skill, then invokes either `python -m storops <verb>`
or the `scripts/*.ps1` wrappers directly (see
[Two ways to call it](#two-ways-to-call-it) below). The only runtime
requirements are Python 3.11+ and, on Windows, WizTree — see
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

## Two ways to call it

StorOps is now implemented in Python (`src/storops/`), exposed as a single
unified CLI with one subcommand per capability — this is the primary,
recommended calling convention:

```bash
python -m storops scan /home/me --json
# or, if the package has been `pip install`-ed: storops scan /home/me --json
```

The original `scripts/*.ps1` entry points **still work, unchanged** — same
script names, same parameter names, same defaults, same `-Json` output field
names. They are now thin compatibility wrappers that translate their
parameters into `storops` CLI flags and shell out to `python -m storops`
under the hood; nothing about how you call them has changed:

```powershell
pwsh scripts/scan.ps1 -Path C:\ -Json
```

Both forms are fully supported and produce equivalent output — see
[`docs/plans/storops-v2-cross-platform-refactor.md`](docs/plans/storops-v2-cross-platform-refactor.md)
§2.10 for why (100% backward compatibility, no transition window). Use
whichever fits your environment; an agent following `SKILL.md` can use
either.

## Layout

```text
SKILL.md            agent behavior contract (when/how to use this skill)
docs/DESIGN.md       full design brief (source of truth for intent/scope)
docs/plans/          detailed design/audit records, e.g. the v2 Python/
                     cross-platform rewrite
rules/               declarative identification + risk rules (YAML),
                     per-platform critical-path files plus shared app/cache rules
src/storops/         the Python implementation: CLI (cli.py), core business
                     logic (core/), platform abstraction (platform/), and
                     output rendering (output/) — see docs/plans/storops-v2-
                     cross-platform-refactor.md §2.2 for the full tree
scripts/             PowerShell entry points, one per capability — now thin
                     compatibility wrappers around `python -m storops`
scripts/lib/         plumbing shared by the scripts/*.ps1 wrappers only
                     (Python-interpreter resolution + CLI invocation); no
                     business logic lives here anymore
tests/               pytest suite (tests/unit, tests/integration) for the
                     Python implementation, plus tests/smoke.ps1 (a thin
                     smoke test for the .ps1 wrappers themselves)
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

An agent following `SKILL.md` will typically (examples below use Windows
paths; on Linux/macOS pass POSIX paths instead, e.g. `-Path /` and
`-Path ~/.cache` — `scan.ps1`/`search.ps1` default to `/` there automatically).
Shown here as the `scripts/*.ps1` wrappers; the equivalent `python -m storops`
form works identically (see [Two ways to call it](#two-ways-to-call-it)) —
e.g. step 1 is also `python -m storops scan C:\`:

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

See [`CHANGELOG.md`](CHANGELOG.md) for a history of notable changes.

## License

MIT — see [`LICENSE`](LICENSE).
