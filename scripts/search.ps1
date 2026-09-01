#requires -Version 5.1
<#
.SYNOPSIS
    Read-only: targeted search under a path -- by name pattern, minimum
    size, and/or age. Compatibility wrapper around `python -m storops
    search` -- same parameters as before, business logic now lives in
    src/storops/ (docs/plans/storops-v2-cross-platform-refactor.md §2.10).
    Requires Python 3.11+ on PATH.

.EXAMPLE
    pwsh scripts/search.ps1 -Path C:\ -NamePattern '*.gguf'
    pwsh scripts/search.ps1 -Path C:\Users\me -MinSizeGB 5
    pwsh scripts/search.ps1 -Path C:\ -NamePattern '*cache*' -Folders

.NOTES
    Read-tier capability: safe to run without confirmation.
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$Path = $(if ($env:OS -eq 'Windows_NT') { 'C:\' } else { '/' }),
    [string]$NamePattern,
    [double]$MinSizeGB,
    [int]$OlderThanDays,
    [switch]$Folders,
    [int]$Top = 50,
    [int]$MaxDepth = 0,
    [switch]$Admin,
    [switch]$Json
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Import-Module (Join-Path $PSScriptRoot 'lib/PythonBridge.psm1') -Force

$cliArgs = @('search', $Path, '--top', "$Top", '--max-depth', "$MaxDepth")
if ($NamePattern) { $cliArgs += @('--name-pattern', $NamePattern) }
if ($MinSizeGB) { $cliArgs += @('--min-size-gb', "$MinSizeGB") }
if ($OlderThanDays) { $cliArgs += @('--older-than-days', "$OlderThanDays") }
if ($Folders) { $cliArgs += '--folders' }
if ($Admin) { $cliArgs += '--admin' }
if ($Json) { $cliArgs += '--json' }

Invoke-StorOpsCli -RepoRoot $repoRoot -CliArgs $cliArgs
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
