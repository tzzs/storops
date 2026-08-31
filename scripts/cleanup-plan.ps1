#requires -Version 5.1
<#
.SYNOPSIS
    Plan-tier: build an itemized, risk-classified cleanup plan JSON for a
    human to review before anything is deleted. Compatibility wrapper
    around `python -m storops cleanup plan` -- same parameters as before,
    business logic now lives in src/storops/ (docs/plans/storops-v2-cross-
    platform-refactor.md §2.10). Requires Python 3.11+ on PATH.

.EXAMPLE
    pwsh scripts/cleanup-plan.ps1
    pwsh scripts/cleanup-plan.ps1 -MaxRisk medium -OutFile C:\temp\plan.json

.NOTES
    Plan-tier capability: read-only, produces a plan file but never deletes
    anything. cleanup-execute.ps1 is the only script allowed to act on the
    plan this produces, and only on items marked Approved.
#>
[CmdletBinding()]
param(
    [ValidateSet('low', 'medium', 'high')]
    [string]$MaxRisk = 'low',
    [string]$OutFile,
    [switch]$Admin,
    [switch]$Json
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Import-Module (Join-Path $PSScriptRoot 'lib/PythonBridge.psm1') -Force

$cliArgs = @('cleanup', 'plan', '--max-risk', $MaxRisk)
if ($OutFile) { $cliArgs += @('--out-file', $OutFile) }
if ($Admin) { $cliArgs += '--admin' }
if ($Json) { $cliArgs += '--json' }

Invoke-StorOpsCli -RepoRoot $repoRoot -CliArgs $cliArgs
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
