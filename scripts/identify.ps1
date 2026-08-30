#requires -Version 5.1
<#
.SYNOPSIS
    Read-only: classify a single path using StorOps' deterministic rule
    base (rules/*.yaml) -- what it is, who owns it, why it exists, and
    whether it can be deleted or migrated (docs/DESIGN.md §6.4, §3.3).

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

    # Optional, purely cosmetic: a known size (e.g. from scan.ps1/inspect.ps1)
    # to echo back in the human-readable output.
    [Nullable[long]]$SizeBytes,

    [switch]$Json
)

$ErrorActionPreference = 'Stop'
$libRoot = Join-Path $PSScriptRoot 'lib'
Import-Module (Join-Path $libRoot 'Common.psm1') -Force
Import-Module (Join-Path $libRoot 'Identify.psm1') -Force
Import-Module (Join-Path $libRoot 'Risk.psm1') -Force

$identity = Get-StorOpsPathIdentity -Path $Path
$action = Get-StorOpsRecommendedAction -Identity $identity

if ($Json) {
    [PSCustomObject]@{
        Identity    = $identity
        Recommended = $action
        SizeBytes   = $SizeBytes
    } | ConvertTo-Json -Depth 6
    return
}

Write-Host $identity.Path -ForegroundColor Cyan
if ($SizeBytes) { Write-Host "  Size:        $(Format-StorOpsSize $SizeBytes)" }
Write-Host "  Application: $(if ($identity.Application) { $identity.Application } else { '(unidentified)' })"
Write-Host "  Category:    $($identity.Category)"
Write-Host "  Confidence:  $($identity.Confidence)"
Write-Host "  Owner:       $(if ($identity.Owner) { $identity.Owner } else { 'n/a' })"
if ($identity.Purpose) { Write-Host "  Purpose:     $($identity.Purpose)" }
Write-Host "  Deletable:   $($identity.Deletable)"
Write-Host "  Migratable:  $($identity.Migratable)"
if ($identity.Migratable) {
    Write-Host "  Migration:   $($identity.MigrationMethod) -- $($identity.MigrationHint)"
    Write-Host "  App closed:  $($identity.RequiresAppClosed)"
}
Write-Host "  Cleanup risk: $($identity.CleanupRisk)"
if ($identity.Consequence) { Write-Host "  Consequence: $($identity.Consequence)" }
if ($identity.Notes) { Write-Host "  Notes:       $($identity.Notes)" }
Write-Host ''
Write-Host "  Recommended action: $($action.Action) -- $($action.Reason)" -ForegroundColor Yellow
