#requires -Version 5.1
<#
.SYNOPSIS
    Write-tier: delete the approved items from a cleanup plan produced by
    cleanup-plan.ps1. Compatibility wrapper around
    `python -m storops cleanup execute` -- same parameters as before,
    business logic now lives in src/storops/ (docs/plans/storops-v2-cross-
    platform-refactor.md §2.10). Requires Python 3.11+ on PATH.

.EXAMPLE
    pwsh scripts/cleanup-execute.ps1 -PlanFile C:\...\storops-cleanup-plan.json -Confirm

.NOTES
    Write-tier capability: requires -Confirm. Without it, this only prints
    what it would do and exits -- no filesystem changes. Every item is
    re-identified against the live rule base right before deletion (defense
    in depth, independent of what the plan file says) -- see
    src/storops/core/cleanup.py.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$PlanFile,
    [switch]$Confirm,
    [switch]$Json
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Import-Module (Join-Path $PSScriptRoot 'lib/PythonBridge.psm1') -Force

$cliArgs = @('cleanup', 'execute', '--plan-file', $PlanFile)
if ($Confirm) { $cliArgs += '--confirm' }
if ($Json) { $cliArgs += '--json' }

Invoke-StorOpsCli -RepoRoot $repoRoot -CliArgs $cliArgs
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
