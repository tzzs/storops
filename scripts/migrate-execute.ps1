#requires -Version 5.1
<#
.SYNOPSIS
    Write-tier: execute a migration plan from migrate-plan.ps1 -- copy,
    verify, then (only after verification passes) remove the original and
    relink the old path. Compatibility wrapper around
    `python -m storops migrate execute` -- same parameters as before,
    business logic now lives in src/storops/ (docs/plans/storops-v2-cross-
    platform-refactor.md §2.10). Requires Python 3.11+ on PATH.

.EXAMPLE
    pwsh scripts/migrate-execute.ps1 -PlanFile C:\...\storops-migrate-plan.json -Confirm -AppClosed

.NOTES
    Write-tier capability: requires -Confirm. Without it, this only prints
    the plan's steps and exits -- no filesystem changes. -AppClosed is a
    required, explicit acknowledgement whenever the plan's RequiresAppClosed
    is true; StorOps' own "is it running" check is a best-effort guess and
    is never trusted on its own for this.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$PlanFile,
    [switch]$Confirm,
    [switch]$AppClosed,
    [switch]$Json
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Import-Module (Join-Path $PSScriptRoot 'lib/PythonBridge.psm1') -Force

$cliArgs = @('migrate', 'execute', '--plan-file', $PlanFile)
if ($Confirm) { $cliArgs += '--confirm' }
if ($AppClosed) { $cliArgs += '--app-closed' }
if ($Json) { $cliArgs += '--json' }

Invoke-StorOpsCli -RepoRoot $repoRoot -CliArgs $cliArgs
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
