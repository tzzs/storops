#requires -Version 5.1
<#
.SYNOPSIS
    Read-only: drill into a specific directory and report its largest
    immediate children (files and subfolders), identified where possible.
    The natural next step after scan.ps1 flags a large, unidentified or
    interesting directory (docs/DESIGN.md §6.2).

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

    # Folders only, no loose files -- off by default since inspecting a
    # specific folder usually means "what's actually in here".
    [switch]$FoldersOnly,

    [switch]$Admin,
    [switch]$Json
)

$ErrorActionPreference = 'Stop'
$libRoot = Join-Path $PSScriptRoot 'lib'
Import-Module (Join-Path $libRoot 'Common.psm1') -Force
Import-Module (Join-Path $libRoot 'ScanBackend.psm1') -Force
Import-Module (Join-Path $libRoot 'Identify.psm1') -Force
Import-Module (Join-Path $libRoot 'Risk.psm1') -Force

$normalized = Resolve-StorOpsPath -Path $Path
if (-not (Test-Path -LiteralPath $normalized)) {
    throw "StorOps: '$normalized' does not exist."
}

Write-Verbose "StorOps: inspecting '$normalized' (top $Top, depth 1, foldersOnly=$($FoldersOnly.IsPresent))"
$entries = Get-StorOpsTopEntries -Path $normalized -Top $Top -MaxDepth 1 -Admin:$Admin -IncludeFiles:(-not $FoldersOnly)

$rows = foreach ($entry in $entries) {
    $identity = Get-StorOpsPathIdentity -Path $entry.FullName
    $action = Get-StorOpsRecommendedAction -Identity $identity
    [PSCustomObject]@{
        Path        = $entry.FullName
        IsFolder    = $entry.IsFolder
        SizeBytes   = $entry.SizeBytes
        Application = $identity.Application
        Category    = $identity.Category
        Confidence  = $identity.Confidence
        CleanupRisk = $identity.CleanupRisk
        Recommended = $action.Action
    }
}

if ($Json) {
    [PSCustomObject]@{
        InspectedPath = $normalized
        Entries       = $rows
    } | ConvertTo-Json -Depth 6
    return
}

Write-Host "Contents of ${normalized}:" -ForegroundColor Cyan
foreach ($row in $rows) {
    $label = if ($row.Application) { $row.Application } else { '(unidentified)' }
    "{0,10}  {1,-8} {2,-22}{3,-8}{4}" -f `
        (Format-StorOpsSize $row.SizeBytes), $row.Recommended, $label, $row.CleanupRisk, $row.Path | Write-Host
}

if ($rows.Count -eq 0) {
    Write-Host '(empty, or below the reporting threshold)' -ForegroundColor DarkGray
}
