#requires -Version 5.1
<#
.SYNOPSIS
    Re-check the current state of a completed migration against the result
    file migrate-execute.ps1 wrote. Compatibility wrapper around
    `python -m storops verify` -- same parameters as before, business logic
    now lives in src/storops/ (docs/plans/storops-v2-cross-platform-
    refactor.md §2.10). Requires Python 3.11+ on PATH.

.EXAMPLE
    pwsh scripts/verify.ps1 -ResultFile C:\...\storops-migrate-result.json

.NOTES
    Read-only: re-checks state, never modifies anything or retries the
    migration. Can be re-run at any later time. Exits non-zero if
    verification fails overall (a new, deliberate improvement over the
    previous PowerShell-only implementation, which never had a consistent
    exit-code contract -- docs/plans/storops-v2-cross-platform-refactor.md
    §2.7).
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$ResultFile,
    [switch]$Json
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Import-Module (Join-Path $PSScriptRoot 'lib/PythonBridge.psm1') -Force

$cliArgs = @('verify', '--result-file', $ResultFile)
if ($Json) { $cliArgs += '--json' }

Invoke-StorOpsCli -RepoRoot $repoRoot -CliArgs $cliArgs
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
