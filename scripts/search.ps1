#requires -Version 5.1
<#
.SYNOPSIS
    Read-only: targeted search under a path -- by name pattern, minimum
    size, and/or age -- for cases scan.ps1/inspect.ps1's directory-level
    view doesn't answer directly (docs/DESIGN.md §6.3), e.g. "find files
    over 10GB" or "find *.gguf files".

.EXAMPLE
    pwsh scripts/search.ps1 -Path C:\ -NamePattern '*.gguf'
    pwsh scripts/search.ps1 -Path C:\Users\me -MinSizeGB 5
    pwsh scripts/search.ps1 -Path C:\ -NamePattern '*cache*' -Folders

.NOTES
    Read-tier capability: safe to run without confirmation.

    NamePattern is passed straight to WizTree's own /filter, so the CSV
    WizTree exports is already narrowed at the source. -MinSizeGB and
    -OlderThanDays have no WizTree CLI equivalent and are applied after
    the export, so an unscoped, unfiltered whole-drive search (no
    NamePattern) can still mean a large export -- scope -Path narrower
    when you can.
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$Path = 'C:\',

    # WizTree filter spec, e.g. '*.gguf' or '*cache*'. Matched against the
    # file/folder name (see /filterfullpath in WizTree's docs for the
    # full-path variant, not exposed here for the MVP).
    [string]$NamePattern,

    [double]$MinSizeGB,
    [int]$OlderThanDays,

    # Search folder names too (default: files only).
    [switch]$Folders,

    [int]$Top = 50,
    [int]$MaxDepth = 0,
    [switch]$Admin,
    [switch]$Json
)

$ErrorActionPreference = 'Stop'
$libRoot = Join-Path $PSScriptRoot 'lib'
Import-Module (Join-Path $libRoot 'Common.psm1') -Force
Import-Module (Join-Path $libRoot 'WizTree.psm1') -Force
Import-Module (Join-Path $libRoot 'Identify.psm1') -Force

$normalized = Resolve-StorOpsPath -Path $Path
if (-not (Test-Path -LiteralPath $normalized)) {
    throw "StorOps: '$normalized' does not exist."
}

if (-not $NamePattern -and $MaxDepth -eq 0) {
    Write-Warning "No -NamePattern and no -MaxDepth given: this exports every file under '$normalized'. Consider narrowing -Path, adding -NamePattern, or setting -MaxDepth."
}

$exportFiles = $true
$exportFolders = [bool]$Folders

Write-Verbose "StorOps: searching '$normalized' (pattern='$NamePattern', minSizeGB=$MinSizeGB, olderThanDays=$OlderThanDays)"
$csv = Invoke-WizTreeScan -Path $normalized -ExportFolders $exportFolders -ExportFiles $exportFiles `
    -MaxDepth $MaxDepth -SortBy 1 -Filter $NamePattern -Admin:$Admin

try {
    $all = ConvertFrom-WizTreeCsv -CsvPath $csv

    $filtered = $all
    if ($MinSizeGB) {
        $minBytes = $MinSizeGB * 1GB
        $filtered = $filtered | Where-Object { $_.SizeBytes -ge $minBytes }
    }
    if ($OlderThanDays) {
        $cutoff = (Get-Date).AddDays(-$OlderThanDays)
        $filtered = $filtered | Where-Object { $_.Modified -and $_.Modified -lt $cutoff }
    }

    $top = $filtered | Sort-Object -Property SizeBytes -Descending | Select-Object -First $Top

    $rows = foreach ($entry in $top) {
        $identity = Get-StorOpsPathIdentity -Path $entry.FullName
        [PSCustomObject]@{
            Path        = $entry.FullName
            IsFolder    = $entry.IsFolder
            SizeBytes   = $entry.SizeBytes
            Modified    = $entry.Modified
            Application = $identity.Application
            Category    = $identity.Category
        }
    }

    if ($Json) {
        [PSCustomObject]@{
            SearchedPath  = $normalized
            NamePattern   = $NamePattern
            MatchCount    = $filtered.Count
            ReturnedCount = $rows.Count
            Entries       = $rows
        } | ConvertTo-Json -Depth 6
        return
    }

    Write-Host "Found $($filtered.Count) match(es) under ${normalized} (showing top $($rows.Count)):" -ForegroundColor Cyan
    foreach ($row in $rows) {
        $label = if ($row.Application) { $row.Application } else { '(unidentified)' }
        $modLabel = if ($row.Modified) { $row.Modified.ToString('yyyy-MM-dd') } else { '?' }
        "{0,10}  {1,-10} {2,-18}{3}" -f (Format-StorOpsSize $row.SizeBytes), $modLabel, $label, $row.Path | Write-Host
    }
}
finally {
    Remove-Item -LiteralPath $csv -Force -ErrorAction SilentlyContinue
}
