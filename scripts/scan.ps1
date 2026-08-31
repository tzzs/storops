#requires -Version 5.1
<#
.SYNOPSIS
    Read-only: scan a drive or directory and report its largest immediate
    consumers, identified where possible. Compatibility wrapper around
    `python -m storops scan` -- same parameters as before, business logic
    now lives in src/storops/ (docs/plans/storops-v2-cross-platform-
    refactor.md §2.10). Requires Python 3.11+ on PATH.

.EXAMPLE
    pwsh scripts/scan.ps1 -Path C:\
    pwsh scripts/scan.ps1 -Path C:\Users\me\AppData\Local -Top 10 -IncludeFiles
    pwsh scripts/scan.ps1 -Path C:\ -Json

.NOTES
    Read-tier capability: safe to run without confirmation. Never modifies
    anything on disk.
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$Path = $(if ($env:OS -eq 'Windows_NT') { 'C:\' } else { '/' }),
    [int]$Top = 15,
    [switch]$IncludeFiles,
    [switch]$Admin,
    [switch]$Json
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Import-Module (Join-Path $PSScriptRoot 'lib/PythonBridge.psm1') -Force

$cliArgs = @('scan', $Path, '--top', "$Top")
if ($IncludeFiles) { $cliArgs += '--include-files' }
if ($Admin) { $cliArgs += '--admin' }
if ($Json) { $cliArgs += '--json' }

Invoke-StorOpsCli -RepoRoot $repoRoot -CliArgs $cliArgs
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
