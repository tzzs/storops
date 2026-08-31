#requires -Version 5.1
<#
.SYNOPSIS
    Read-only: drill into a specific directory and report its largest
    immediate children. Compatibility wrapper around
    `python -m storops inspect` -- same parameters as before, business
    logic now lives in src/storops/ (docs/plans/storops-v2-cross-platform-
    refactor.md §2.10). Requires Python 3.11+ on PATH.

.EXAMPLE
    pwsh scripts/inspect.ps1 -Path C:\Users\me\AppData\Local
    pwsh scripts/inspect.ps1 -Path C:\Users\me\AppData\Local\Docker -Top 10

.NOTES
    Read-tier capability: safe to run without confirmation.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory, Position = 0)]
    [string]$Path,
    [int]$Top = 20,
    [switch]$FoldersOnly,
    [switch]$Admin,
    [switch]$Json
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Import-Module (Join-Path $PSScriptRoot 'lib/PythonBridge.psm1') -Force

$cliArgs = @('inspect', $Path, '--top', "$Top")
if ($FoldersOnly) { $cliArgs += '--folders-only' }
if ($Admin) { $cliArgs += '--admin' }
if ($Json) { $cliArgs += '--json' }

Invoke-StorOpsCli -RepoRoot $repoRoot -CliArgs $cliArgs
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
