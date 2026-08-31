#requires -Version 5.1
<#
.SYNOPSIS
    Write-tier: delete the approved items from a cleanup plan produced by
    cleanup-plan.ps1 (docs/DESIGN.md §10, §11). The only script in StorOps
    allowed to delete anything, and only paths a plan already marked
    Approved -- it never re-derives approval from scratch.

.EXAMPLE
    pwsh scripts/cleanup-execute.ps1 -PlanFile C:\...\storops-cleanup-plan.json -Confirm

.NOTES
    Write-tier capability: requires -Confirm. Without it, this only prints
    what it would do and exits -- no filesystem changes.

    Defense in depth, independent of what the plan file says:
      - every item is re-identified against the live rule base right
        before deletion, and Assert-StorOpsNotCritical is called on the
        fresh identity -- a stale or hand-edited plan can never be used to
        delete a path that reclassifies as critical since the plan was
        generated.
      - a path whose fresh recommended action is no longer DELETE is
        skipped, not forced through.
      - a locked/in-use file is skipped with a warning, never forced
        (matches rules/caches.yaml's guidance for things like
        windows-temp: leave in-use files alone rather than fighting them).
      - verification after each delete is a plain existence check; a
        failed delete is reported as failed, never retried destructively.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$PlanFile,

    # Without this switch, the script only previews what it would delete.
    [switch]$Confirm,

    [switch]$Json
)

$ErrorActionPreference = 'Stop'
$libRoot = Join-Path $PSScriptRoot 'lib'
Import-Module (Join-Path $libRoot 'Common.psm1') -Force
Import-Module (Join-Path $libRoot 'Identify.psm1') -Force
Import-Module (Join-Path $libRoot 'Risk.psm1') -Force

if (-not (Test-Path -LiteralPath $PlanFile)) {
    throw "StorOps: plan file '$PlanFile' does not exist. Run cleanup-plan.ps1 first."
}

$plan = Get-Content -LiteralPath $PlanFile -Raw | ConvertFrom-Json
$approvedItems = @($plan.Items | Where-Object { $_.Approved })

if ($approvedItems.Count -eq 0) {
    Write-Host 'StorOps: no approved items in this plan -- nothing to do.' -ForegroundColor DarkGray
    return
}

if (-not $Confirm) {
    Write-Host "DRY RUN -- pass -Confirm to actually delete. Approved items in '$PlanFile':" -ForegroundColor Yellow
    foreach ($item in $approvedItems) {
        "  {0,10}  {1,-18}{2}" -f (Format-StorOpsSize $item.SizeBytes), $item.Application, $item.Path | Write-Host
    }
    return
}

$results = New-Object System.Collections.Generic.List[object]

foreach ($item in $approvedItems) {
    $record = [PSCustomObject]@{
        Path      = $item.Path
        SizeBytes = $item.SizeBytes
        Status    = $null
        Detail    = $null
    }

    if (-not (Test-Path -LiteralPath $item.Path)) {
        $record.Status = 'skipped'
        $record.Detail = 'path no longer exists'
        $results.Add($record)
        continue
    }

    $identity = Get-StorOpsPathIdentity -Path $item.Path
    try {
        Assert-StorOpsNotCritical -Identity $identity
    }
    catch {
        $record.Status = 'skipped'
        $record.Detail = "refused: $_"
        $results.Add($record)
        Write-Warning $record.Detail
        continue
    }

    $action = Get-StorOpsRecommendedAction -Identity $identity
    if ($action.Action -ne 'DELETE') {
        $record.Status = 'skipped'
        $record.Detail = "no longer recommended for deletion (now: $($action.Action)) -- path may have changed since the plan was generated"
        $results.Add($record)
        Write-Warning "StorOps: skipping '$($item.Path)': $($record.Detail)"
        continue
    }

    try {
        Remove-Item -LiteralPath $item.Path -Recurse -Force -ErrorAction Stop
    }
    catch {
        $record.Status = 'failed'
        $record.Detail = "delete failed (possibly in use): $_"
        $results.Add($record)
        Write-Warning "StorOps: could not delete '$($item.Path)': $_"
        continue
    }

    if (Test-Path -LiteralPath $item.Path) {
        $record.Status = 'failed'
        $record.Detail = 'path still present after Remove-Item -- treating as unverified/failed'
    }
    else {
        $record.Status = 'deleted'
    }
    $results.Add($record)
}

$reclaimed = ($results | Where-Object { $_.Status -eq 'deleted' } | Measure-Object -Property SizeBytes -Sum).Sum
if (-not $reclaimed) { $reclaimed = 0L }

$resultFile = Join-Path (Get-StorOpsWorkDir) 'storops-cleanup-result.json'
[PSCustomObject]@{
    PlanFile        = $PlanFile
    ExecutedAt      = (Get-Date).ToString('o')
    Results         = $results
    ReclaimedBytes  = [int64]$reclaimed
} | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $resultFile -Encoding UTF8

if ($Json) {
    [PSCustomObject]@{ Results = $results; ReclaimedBytes = [int64]$reclaimed; ResultFile = $resultFile } | ConvertTo-Json -Depth 6
    return
}

Write-Host ''
foreach ($record in $results) {
    $color = switch ($record.Status) { 'deleted' { 'Green' }; 'failed' { 'Red' }; default { 'DarkGray' } }
    "{0,-8} {1,10}  {2}" -f $record.Status, (Format-StorOpsSize $record.SizeBytes), $record.Path | Write-Host -ForegroundColor $color
    if ($record.Detail) { Write-Host "         -> $($record.Detail)" -ForegroundColor DarkGray }
}
Write-Host ''
Write-Host "Reclaimed: $(Format-StorOpsSize $reclaimed)"
Write-Host "Result log: $resultFile"
