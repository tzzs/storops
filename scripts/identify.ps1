#requires -Version 5.1
<#
.SYNOPSIS
    Read-only: classify a single path using StorOps' deterministic rule
    base (rules/*.yaml). Compatibility wrapper around
    `python -m storops identify` -- same parameters as before, business
    logic now lives in src/storops/ (docs/plans/storops-v2-cross-platform-
    refactor.md §2.10). Requires Python 3.11+ on PATH.

.EXAMPLE
    pwsh scripts/identify.ps1 -Path C:\Users\me\.lmstudio\models
    pwsh scripts/identify.ps1 -Path C:\Users\me\.cache\huggingface -Json

.NOTES
    Read-tier capability: safe to run without confirmation. Never guesses --
    an unmatched path comes back as Category "unknown", CleanupRisk
    "critical", not as some plausible-sounding invented classification.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory, Position = 0)]
    [string]$Path,
    [Nullable[long]]$SizeBytes,
    [switch]$Json
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Import-Module (Join-Path $PSScriptRoot 'lib/PythonBridge.psm1') -Force

$cliArgs = @('identify', $Path)
if ($null -ne $SizeBytes) { $cliArgs += @('--size-bytes', "$SizeBytes") }
if ($Json) { $cliArgs += '--json' }

Invoke-StorOpsCli -RepoRoot $repoRoot -CliArgs $cliArgs
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
