#requires -Version 5.1
<#
.SYNOPSIS
    Plan-tier: work out how to move one identified, migratable directory to
    a new location -- method, whether the owning app must be closed, and
    the ordered steps -- without moving anything. Compatibility wrapper
    around `python -m storops migrate plan` -- same parameters as before,
    business logic now lives in src/storops/ (docs/plans/storops-v2-cross-
    platform-refactor.md §2.10). Requires Python 3.11+ on PATH.

.EXAMPLE
    pwsh scripts/migrate-plan.ps1 -Path C:\Users\me\.lmstudio\models -Destination E:\AI\lmstudio-models

.NOTES
    Plan-tier capability: read-only, writes a plan JSON but touches
    nothing. migrate-execute.ps1 is the only script that acts on it.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory, Position = 0)]
    [string]$Path,
    [Parameter(Mandatory, Position = 1)]
    [string]$Destination,
    [switch]$Admin,
    [switch]$Json
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Import-Module (Join-Path $PSScriptRoot 'lib/PythonBridge.psm1') -Force

$cliArgs = @('migrate', 'plan', $Path, $Destination)
if ($Admin) { $cliArgs += '--admin' }
if ($Json) { $cliArgs += '--json' }

Invoke-StorOpsCli -RepoRoot $repoRoot -CliArgs $cliArgs
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
