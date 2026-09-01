---
name: storops
description: 'Storage Operations for AI Agents. Cross-platform (Windows, Linux, macOS): diagnose why a drive or volume is full, identify what specific applications/caches/AI-model files are consuming space, and safely clean up or migrate them -- with mandatory user confirmation before any write and verification after every migration. Use when the user asks things like "why is my C: drive full", "why is my disk full" / "/ is full", "clean up disk space", "move LM Studio / Ollama / Docker / <app> to another drive", or "is it safe to delete <path>".'
slug: storops
displayName: StorOps
version: 0.2.0
summary: '面向 AI Agent 的存储运维能力：跨平台（Windows / Linux / macOS）诊断磁盘空间占用、安全清理与迁移，写入前必须确认，迁移后自动验证。'
license: MIT
---

# StorOps

**See where your storage goes. Understand why. Move what matters. Clean what doesn't.**

StorOps is not a disk scanner and not a disk cleaner. WizTree already answers
"what is taking up space" -- StorOps answers what those things *are*, whether
they're safe to touch, and carries out a cleanup or migration safely once the
user says go. Full rationale in `docs/DESIGN.md`.

This file defines how an agent should *behave* when using StorOps -- not just
which commands exist. Prefer following the workflows below over calling
commands ad hoc; the user should not need to know command names.

## Non-negotiable rules

1. Analysis before action. Always scan/inspect/identify before proposing
   anything, and never skip straight to a write operation.
2. Everything defaults to read-only. `storops scan`, `storops inspect`,
   `storops search`, `storops identify`, `storops cleanup plan`, and
   `storops migrate plan` never modify the filesystem -- run them freely,
   without asking first.
3. Never guess what a path is from its name. Only `storops identify` (backed
   by `rules/*.yaml`) determines category/risk/deletability. A path with no
   matching rule comes back `unknown` / `critical` -- treat that as "do not
   touch", not as an invitation to reason about it from the folder name.
4. Never treat "cache" or "temp" in a name as license to delete. Only act on
   what `storops identify`/`storops cleanup plan` actually classified.
5. Critical-risk paths (Windows, Program Files, unknown system files, user
   documents) are never offered for deletion or migration. This is enforced
   in code (`assert_not_critical` in `src/storops/core/risk.py`), not just
   by convention -- don't try to route around it.
6. Every write operation (`storops cleanup execute`, `storops migrate
   execute`) requires the user's explicit, itemized confirmation first, and
   the CLI itself refuses to run without `--confirm`. Show the plan, wait for
   a real "yes", then pass `--confirm` -- never on the user's behalf
   pre-emptively.
7. If an application may be running, do not move its data. `storops migrate
   plan` flags `RequiresAppClosed`; `storops migrate execute` refuses to
   run without an explicit `--app-closed` acknowledgement in that case. Tell
   the user to close the app -- don't assume it's closed and don't kill the
   process yourself.
8. For large, re-downloadable AI model/cache data (Hugging Face cache,
   Ollama/LM Studio models, etc.), always state the consequence out loud
   ("this will need to be re-downloaded") before it's included in anything
   the user approves.
9. Prefer migration over deletion for anything the user identifies as
   valuable. Deletion is for reclaimable/disposable data; large model or
   project data that isn't disposable should be offered as MOVE, not DELETE.
10. Prefer an application's own config-based relocation over a Junction.
    `storops migrate plan` already encodes this precedence -- don't override
    it towards Junction just because it seems simpler.
11. Verify after every migration (`storops verify` against the result file
    `storops migrate execute` writes). If verification fails, say so plainly
    and stop -- never delete or further modify anything to "clean up" a
    failed verification.
12. Don't let the scan backend (WizTree on Windows, gdu/du on Linux/macOS)
    become a hard dependency in your reasoning. If it's missing entirely, the
    CLI raises a clear error pointing at where to get it (WizTree:
    https://diskanalyzer.com/ or `$env:STOROPS_WIZTREE_PATH`; gdu:
    https://github.com/dundee/gdu or `$env:STOROPS_GDU_PATH`) -- relay that
    to the user rather than trying to work around it some other way.
13. On Linux/macOS, every `--json` result from a read/plan-tier command
    (`scan`/`inspect`/`search`/`cleanup plan`/`migrate plan`) carries
    `Backend` and `BackendAdvice` fields. `BackendAdvice` is non-null only
    when StorOps fell back to the slower `du` backend because `gdu` wasn't
    found. Mention it to the user once per conversation if it's non-null
    (e.g. "by the way, installing gdu would make these scans noticeably
    faster") -- don't repeat it on every single command, and don't mention it
    at all on Windows or when it's `null`.

## Workflow: "why is my drive full?"

1. `storops scan C:\` (or the drive the user mentioned) for top-level
   consumers and free space.
2. For any large and unidentified or ambiguous entry, `storops inspect` into
   it to see what's actually inside.
3. Cross-reference every notable entry with `storops identify` (scan/inspect
   already attach identity + recommended action, but call it directly for a
   single path the user asks about).
4. Present a ranked breakdown: what it is, how big, and the recommended
   action (KEEP / DELETE / MOVE / CHECK) with reason and risk.
5. Never delete or move anything at this stage -- this is purely diagnostic.

## Workflow: "clean up disk space" / "delete X"

1. Make sure the target has already been scanned/identified (run the scan
   workflow above first if not).
2. Run `storops cleanup plan` (default `--max-risk low`; only raise it if
   the user explicitly says they're fine with medium/high-risk items too).
   This produces an itemized JSON plan -- nothing is deleted yet.
3. Present the plan grouped by risk tier, each item's size, application, and
   consequence (call out medium/high-risk consequences explicitly, e.g. "may
   need to be re-downloaded"). Show the total reclaimable size.
4. Ask the user to confirm. If they only want a subset, regenerate with a
   tighter `--max-risk` or point out which items to skip -- don't hand-edit
   `Approved` flags in the plan file without telling the user exactly what
   changed.
5. Only once the user confirms, run `storops cleanup execute --plan-file
   <path> --confirm`.
6. Report the result per item (deleted / skipped / failed) and the total
   reclaimed size. A `failed` item (e.g. file in use) is reported, not
   retried forcefully.

## Workflow: "migrate X to another drive"

1. Identify the source path (`storops identify` if not already known from a
   scan). If it's not `Migratable`, say so and explain why (e.g. critical,
   or no known migration path) instead of improvising one.
2. Run `storops migrate plan <source> <destination>`. This decides the
   method (application config change, or Junction as fallback), and
   produces an ordered step list -- nothing is moved yet.
3. Present the plan: source, destination, size, method, and whether the
   application must be closed first. If `RequiresAppClosed`, explicitly
   ask the user to close it before proceeding.
4. Ask for confirmation on the plan as a whole.
5. Run `storops migrate execute --plan-file <path> --confirm` (add
   `--app-closed` once the user has confirmed the app is closed). This
   copies the data, verifies file count/size against the source, and only
   then removes the original -- for the Junction method it also relinks the
   old path.
6. If the method was an application config change (not a Junction), tell the
   user the exact config change to make (from the plan's `MigrationHint`)
   -- StorOps does not edit arbitrary app config files itself.
7. Run `storops verify --result-file <path from migrate execute output>` and
   report PASS/FAIL per check. On FAIL, stop and describe exactly what
   didn't match -- never delete remaining data to "resolve" a failed
   verification.

## Workflow: "is it safe to delete/move <path>?"

Just run `storops identify <path>` and relay Category, Deletable,
Migratable, CleanupRisk, Consequence, and the recommended action verbatim --
this is the direct, deterministic answer; don't editorialize past what the
rule base actually says, and don't guess for an `unknown` result.

## Command reference

| Tier   | Commands | Confirmation |
|--------|----------|--------------|
| Read   | `storops scan`, `storops inspect`, `storops search`, `storops identify` | none |
| Plan   | `storops cleanup plan`, `storops migrate plan` | none (produces a plan file only) |
| Write  | `storops cleanup execute`, `storops migrate execute` | requires `--confirm` (and `--app-closed` for migrations that need it) |
| Verify | `storops verify` | none (read-only re-check) |

Every command supports `--json` for machine-readable output. Invoke as
`python -m storops <command> ...`, or `storops <command> ...` if the package
has been `pip install`-ed.

See `README.md` for setup/requirements and `rules/README.md` for the rule
schema behind identification.
