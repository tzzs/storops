#requires -Version 5.1
<#
.SYNOPSIS
    Read-only: scan a drive or directory and report its largest immediate
    consumers, identified where possible. The usual first step when
    answering "why is this drive full?" (docs/DESIGN.md §6.1, §21 scenario 1).

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
    [string]$Path = 'C:\',

    # How many of the largest immediate entries to report.
    [int]$Top = 15,

    # Also list top-level loose files directly under Path, not just folders.
    [switch]$IncludeFiles,

    # Ask WizTree to read the NTFS MFT directly (faster/more complete, but
    # self-elevates via UAC).
    [switch]$Admin,

    # Emit structured JSON instead of a formatted table.
    [switch]$Json
)

$ErrorActionPreference = 'Stop'
$libRoot = Join-Path $PSScriptRoot 'lib'
Import-Module (Join-Path $libRoot 'Common.psm1') -Force
Import-Module (Join-Path $libRoot 'WizTree.psm1') -Force
Import-Module (Join-Path $libRoot 'Identify.psm1') -Force
Import-Module (Join-Path $libRoot 'Risk.psm1') -Force

$normalized = Resolve-StorOpsPath -Path $Path

$capacity = $null
try { $capacity = Get-StorOpsFreeSpaceInfo -DriveLetter $normalized.Substring(0, 1) }
catch { Write-Warning "Could not read drive capacity for '$normalized': $_" }

Write-Verbose "StorOps: scanning '$normalized' (top $Top, depth 1, includeFiles=$($IncludeFiles.IsPresent))"
$entries = Get-StorOpsTopEntries -Path $normalized -Top $Top -MaxDepth 1 -Admin:$Admin -IncludeFiles:$IncludeFiles

$rows = foreach ($entry in $entries) {
    $identity = Get-StorOpsPathIdentity -Path $entry.FullName
    [PSCustomObject]@{
        Path        = $entry.FullName
        IsFolder    = $entry.IsFolder
        SizeBytes   = $entry.SizeBytes
        Application = $identity.Application
        Category    = $identity.Category
        Confidence  = $identity.Confidence
        CleanupRisk = $identity.CleanupRisk
    }
}

if ($Json) {
    [PSCustomObject]@{
        ScannedPath = $normalized
        Drive       = $capacity
        Entries     = $rows
    } | ConvertTo-Json -Depth 6
    return
}

if ($capacity) {
    Write-Host ("{0}  Total: {1}  Used: {2}  Free: {3}" -f `
        $capacity.Drive, (Format-StorOpsSize $capacity.TotalBytes), `
        (Format-StorOpsSize $capacity.UsedBytes), (Format-StorOpsSize $capacity.FreeBytes)) -ForegroundColor Cyan
    Write-Host ''
}

Write-Host "Top $($rows.Count) consumers under ${normalized}:"
foreach ($row in $rows) {
    $label = if ($row.Application) { $row.Application } else { '(unidentified)' }
    $riskTag = if ($row.CleanupRisk -in @('high', 'critical')) { " [$($row.CleanupRisk)]" } else { '' }
    "{0,10}  {1,-22}{2,-12}{3}" -f (Format-StorOpsSize $row.SizeBytes), $label, $riskTag, $row.Path | Write-Host
}

if (-not $IncludeFiles) {
    Write-Host ''
    Write-Host "(Folders only. Pass -IncludeFiles to also list top-level loose files, or run inspect.ps1 on a specific large folder to drill down.)" -ForegroundColor DarkGray
}
