#requires -Version 5.1
<#
.SYNOPSIS
    Re-check the current state of a completed migration against the result
    file migrate-execute.ps1 wrote: target accessible, size/count still
    matches what was copied, source correctly cleared or (for the junction
    method) still resolving to the new location (docs/DESIGN.md §12).

.EXAMPLE
    pwsh scripts/verify.ps1 -ResultFile C:\...\storops-migrate-result.json

.NOTES
    Read-only: re-checks state, never modifies anything or retries the
    migration. Can be re-run at any later time, independent of the
    migrate-execute.ps1 run that produced the result file.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$ResultFile,

    [switch]$Json
)

$ErrorActionPreference = 'Stop'
$libRoot = Join-Path $PSScriptRoot 'lib'
Import-Module (Join-Path $libRoot 'Common.psm1') -Force

if (-not (Test-Path -LiteralPath $ResultFile)) {
    throw "StorOps: result file '$ResultFile' does not exist."
}
$result = Get-Content -LiteralPath $ResultFile -Raw | ConvertFrom-Json

function Get-StorOpsDirStats {
    param([Parameter(Mandatory)][string]$DirPath)

    $files = @(Get-ChildItem -LiteralPath $DirPath -Recurse -File -Force -ErrorAction SilentlyContinue)
    $sum = ($files | Measure-Object -Property Length -Sum).Sum
    [PSCustomObject]@{
        FileCount = $files.Count
        SizeBytes = [int64](if ($sum) { $sum } else { 0 })
    }
}

$checks = New-Object System.Collections.Generic.List[object]
function Add-StorOpsCheck($name, $pass, $detail) {
    $checks.Add([PSCustomObject]@{ Check = $name; Pass = [bool]$pass; Detail = $detail })
}

$targetAccessible = Test-Path -LiteralPath $result.Destination -PathType Container
Add-StorOpsCheck 'target-accessible' $targetAccessible "Destination '$($result.Destination)'"

if ($targetAccessible) {
    $currentStats = Get-StorOpsDirStats -DirPath $result.Destination
    $countMatches = $currentStats.FileCount -eq $result.PostCopy.FileCount
    $sizeMatches = $currentStats.SizeBytes -eq $result.PostCopy.SizeBytes
    Add-StorOpsCheck 'file-count-matches' $countMatches "expected $($result.PostCopy.FileCount), found $($currentStats.FileCount)"
    Add-StorOpsCheck 'total-size-matches' $sizeMatches "expected $(Format-StorOpsSize $result.PostCopy.SizeBytes), found $(Format-StorOpsSize $currentStats.SizeBytes)"
}
else {
    Add-StorOpsCheck 'file-count-matches' $false 'destination not accessible'
    Add-StorOpsCheck 'total-size-matches' $false 'destination not accessible'
}

if ($result.Method -eq 'junction') {
    $sourceExists = Test-Path -LiteralPath $result.Source -PathType Container
    Add-StorOpsCheck 'source-is-junction' $sourceExists "Source '$($result.Source)' should exist as a junction"

    if ($sourceExists) {
        $item = Get-Item -LiteralPath $result.Source -Force
        $isJunction = $item.LinkType -eq 'Junction'
        $targetMatches = $isJunction -and $item.Target -and
            ((Resolve-StorOpsPath -Path $item.Target[0]) -eq (Resolve-StorOpsPath -Path $result.Destination))
        Add-StorOpsCheck 'junction-works' $targetMatches "LinkType=$($item.LinkType), Target=$($item.Target -join ';')"
    }
    else {
        Add-StorOpsCheck 'junction-works' $false 'source path does not exist -- junction is missing'
    }
}
else {
    $sourceCleared = -not (Test-Path -LiteralPath $result.Source)
    Add-StorOpsCheck 'source-cleared' $sourceCleared (
        if ($sourceCleared) { "Original at '$($result.Source)' is gone, as expected" }
        else { "Original still present at '$($result.Source)' -- remove it once the app is confirmed working from '$($result.Destination)'" }
    )
}

$overallPass = ($checks | Where-Object { -not $_.Pass }).Count -eq 0

$result | Add-Member -MemberType NoteProperty -Name LastVerification -Value ([PSCustomObject]@{
    VerifiedAt = (Get-Date).ToString('o')
    Pass       = $overallPass
    Checks     = $checks
}) -Force
$result | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $ResultFile -Encoding UTF8

if ($Json) {
    [PSCustomObject]@{ Pass = $overallPass; Checks = $checks } | ConvertTo-Json -Depth 6
    return
}

Write-Host "StorOps verification: $($result.Source) -> $($result.Destination)" -ForegroundColor Cyan
foreach ($check in $checks) {
    $mark = if ($check.Pass) { 'PASS' } else { 'FAIL' }
    $color = if ($check.Pass) { 'Green' } else { 'Red' }
    "  [{0}] {1,-22}{2}" -f $mark, $check.Check, $check.Detail | Write-Host -ForegroundColor $color
}
Write-Host ''
if ($overallPass) {
    Write-Host 'Overall: PASS' -ForegroundColor Green
}
else {
    Write-Host 'Overall: FAIL -- do not remove any remaining original data until this is resolved.' -ForegroundColor Red
}
