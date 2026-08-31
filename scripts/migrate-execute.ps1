#requires -Version 5.1
<#
.SYNOPSIS
    Write-tier: execute a migration plan from migrate-plan.ps1 -- copy,
    verify, then (only after verification passes) remove the original and,
    for the junction method, relink the old path (docs/DESIGN.md §8, §9).

.EXAMPLE
    pwsh scripts/migrate-execute.ps1 -PlanFile C:\...\storops-migrate-plan.json -Confirm -AppClosed

.NOTES
    Write-tier capability: requires -Confirm. Without it, this only prints
    the plan's steps and exits -- no filesystem changes.

    Sequence is copy-then-verify-then-remove-original, never a plain move:
    a cross-volume "move" is a copy+delete under the hood anyway, so doing
    the copy and size/count verification explicitly means an interrupted
    or partial copy is caught before the original is ever touched
    (docs/DESIGN.md §12: verification failure must never trigger
    auto-deletion of originals -- here it means the original simply isn't
    deleted at all).

    Does NOT auto-edit application config files for the "app-config"/
    "manual" methods -- config formats vary too much (JSON, YAML, .env,
    registry, GUI-only settings) to do that safely and deterministically
    for the MVP. It prints the rule's migration_config_hint and expects
    the user/agent to apply it, then re-run with the app pointed at the
    new location.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$PlanFile,

    # Without this switch, the script only previews the plan's steps.
    [switch]$Confirm,

    # Required acknowledgement whenever the plan's RequiresAppClosed is
    # true. StorOps' own "is it running" check is a best-effort guess
    # (see migrate-plan.ps1) and is never trusted on its own for this.
    [switch]$AppClosed,

    [switch]$Json
)

$ErrorActionPreference = 'Stop'
$libRoot = Join-Path $PSScriptRoot 'lib'
Import-Module (Join-Path $libRoot 'Common.psm1') -Force
Import-Module (Join-Path $libRoot 'Identify.psm1') -Force
Import-Module (Join-Path $libRoot 'Risk.psm1') -Force

function Get-StorOpsDirStats {
    param([Parameter(Mandatory)][string]$DirPath)

    $files = @(Get-ChildItem -LiteralPath $DirPath -Recurse -File -Force -ErrorAction SilentlyContinue)
    $sum = ($files | Measure-Object -Property Length -Sum).Sum
    [PSCustomObject]@{
        FileCount = $files.Count
        SizeBytes = [int64](if ($sum) { $sum } else { 0 })
    }
}

if (-not (Test-Path -LiteralPath $PlanFile)) {
    throw "StorOps: plan file '$PlanFile' does not exist. Run migrate-plan.ps1 first."
}
$plan = Get-Content -LiteralPath $PlanFile -Raw | ConvertFrom-Json
$source = $plan.Source
$destination = $plan.Destination

if (-not (Test-Path -LiteralPath $source -PathType Container)) {
    throw "StorOps: source '$source' no longer exists or is not a directory -- the plan is stale. Re-run migrate-plan.ps1."
}

$identity = Get-StorOpsPathIdentity -Path $source
Assert-StorOpsNotCritical -Identity $identity
if (-not $identity.Migratable) {
    throw "StorOps: '$source' is no longer classified as migratable -- the plan is stale. Re-run migrate-plan.ps1."
}

if ($plan.RequiresAppClosed -and -not $AppClosed) {
    throw "StorOps: this plan requires $(if ($identity.Application) { $identity.Application } else { 'the owning application' }) to be fully closed before executing. Close it, then re-run with -AppClosed."
}

if (-not $Confirm) {
    Write-Host "DRY RUN -- pass -Confirm to actually migrate. Plan '$PlanFile':" -ForegroundColor Yellow
    Write-Host "  $source  ->  $destination"
    for ($i = 0; $i -lt $plan.Steps.Count; $i++) { Write-Host ("  {0}. {1}" -f ($i + 1), $plan.Steps[$i]) }
    return
}

if (Test-Path -LiteralPath $destination) {
    $existing = @(Get-ChildItem -LiteralPath $destination -Force -ErrorAction SilentlyContinue)
    if ($existing.Count -gt 0) {
        throw "StorOps: destination '$destination' already exists and is not empty -- the plan is stale. Re-run migrate-plan.ps1 with a clean destination."
    }
}
$destParent = Split-Path -Parent $destination
if ($destParent -and -not (Test-Path -LiteralPath $destParent)) {
    New-Item -ItemType Directory -Path $destParent -Force | Out-Null
}

$robocopy = Get-Command -Name 'robocopy.exe' -ErrorAction SilentlyContinue
if (-not $robocopy) {
    throw 'StorOps: robocopy.exe was not found on PATH -- it ships with Windows and is required for the verified-copy step.'
}

Write-Host "Copying '$source' -> '$destination' ..." -ForegroundColor Cyan
$preStats = Get-StorOpsDirStats -DirPath $source

$psi = [System.Diagnostics.ProcessStartInfo]::new()
$psi.FileName = $robocopy.Source
foreach ($arg in @($source, $destination, '/E', '/COPY:DAT', '/R:2', '/W:2', '/MT:8', '/NFL', '/NDL', '/NP', '/NJH', '/NJS')) {
    $psi.ArgumentList.Add($arg)
}
$psi.UseShellExecute = $false
$psi.CreateNoWindow = $true
$proc = [System.Diagnostics.Process]::Start($psi)
$proc.WaitForExit()

# robocopy exit codes: 0-7 are success variants (bit flags for files
# copied / extra files / mismatches), >=8 means at least one failure.
if ($proc.ExitCode -ge 8) {
    throw "StorOps: robocopy failed copying '$source' to '$destination' (exit code $($proc.ExitCode)). Nothing was removed; inspect the destination and retry."
}

$postStats = Get-StorOpsDirStats -DirPath $destination
$verified = ($postStats.FileCount -eq $preStats.FileCount) -and ($postStats.SizeBytes -eq $preStats.SizeBytes)

$result = [PSCustomObject]@{
    PlanFile        = $PlanFile
    ExecutedAt      = (Get-Date).ToString('o')
    Source          = $source
    Destination     = $destination
    Method          = $plan.Method
    PreCopy         = $preStats
    PostCopy        = $postStats
    Verified        = $verified
    SourceRemoved   = $false
    JunctionCreated = $false
    Status          = $null
    Detail          = $null
}

if (-not $verified) {
    $result.Status = 'verification-failed'
    $result.Detail = "File count/size mismatch after copy (source: $($preStats.FileCount) files/$($preStats.SizeBytes) bytes, destination: $($postStats.FileCount) files/$($postStats.SizeBytes) bytes). Original left untouched at '$source'."
    Write-Warning "StorOps: $($result.Detail)"
}
else {
    try {
        Remove-Item -LiteralPath $source -Recurse -Force -ErrorAction Stop
        $result.SourceRemoved = $true
    }
    catch {
        $result.Status = 'copy-ok-source-not-removed'
        $result.Detail = "Copy verified, but the original at '$source' could not be removed ($_). Remove it manually once you've confirmed the migration is working."
        Write-Warning "StorOps: $($result.Detail)"
    }

    if ($result.SourceRemoved -and $plan.Method -eq 'junction') {
        try {
            New-Item -ItemType Junction -Path $source -Target $destination -ErrorAction Stop | Out-Null
            $junctionOk = (Test-Path -LiteralPath $source -PathType Container) -and
                ((Get-Item -LiteralPath $source -Force).LinkType -eq 'Junction')
            $result.JunctionCreated = [bool]$junctionOk
            if (-not $junctionOk) {
                $result.Status = 'junction-verification-failed'
                $result.Detail = "Junction was created at '$source' but did not verify as pointing to '$destination'. Inspect manually."
            }
        }
        catch {
            $result.Status = 'junction-failed'
            $result.Detail = "Data was copied and verified, and the original was removed, but creating the junction at '$source' failed: $_. Create it manually: mklink /J `"$source`" `"$destination`""
            Write-Warning "StorOps: $($result.Detail)"
        }
    }

    if (-not $result.Status) {
        $result.Status = 'succeeded'
        $result.Detail = if ($plan.Method -eq 'junction') {
            "Migrated and relinked via junction. $($identity.Application) can keep using '$source' unchanged."
        }
        else {
            "Migrated. Apply the app's relocation setting now: $($plan.MigrationHint)"
        }
    }
}

$resultFile = Join-Path (Get-StorOpsWorkDir) 'storops-migrate-result.json'
$result | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $resultFile -Encoding UTF8

if ($Json) {
    $result | ConvertTo-Json -Depth 6
    return
}

Write-Host ''
$color = if ($result.Status -eq 'succeeded') { 'Green' } elseif ($result.Status -like '*failed*') { 'Red' } else { 'Yellow' }
Write-Host "Status: $($result.Status)" -ForegroundColor $color
Write-Host $result.Detail
Write-Host ''
Write-Host "Result log: $resultFile"
Write-Host "Verify later with: pwsh scripts/verify.ps1 -ResultFile '$resultFile'" -ForegroundColor Green
