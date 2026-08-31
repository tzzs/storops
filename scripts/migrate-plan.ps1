#requires -Version 5.1
<#
.SYNOPSIS
    Plan-tier: work out how to move one identified, migratable directory to
    a new location -- method, whether the owning app must be closed, and
    the ordered steps -- without moving anything (docs/DESIGN.md §8, §9).

.EXAMPLE
    pwsh scripts/migrate-plan.ps1 -Path C:\Users\me\.lmstudio\models -Destination E:\AI\lmstudio-models

.NOTES
    Plan-tier capability: read-only, writes a plan JSON but touches
    nothing. migrate-execute.ps1 is the only script that acts on it.

    Method comes straight from the matched rule's migration_method:
      - "app-config" / "manual": the app has its own way to relocate this
        data (an env var, a settings command, a GUI option) -- follow
        MigrationHint. StorOps does not auto-edit application config files
        for the MVP (formats vary too much to do safely/deterministically);
        it prints the hint for the user/agent to apply by hand.
      - anything else (including no migration_method at all): falls back
        to a Junction at the old path pointing at the new location, since
        the app has no known config-based relocation option
        (docs/DESIGN.md §9: Windows prefers Junction over symbolic link).
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory, Position = 0)]
    [string]$Path,

    [Parameter(Mandatory, Position = 1)]
    [string]$Destination,

    [switch]$Admin,
    [switch]$Json
)

$ErrorActionPreference = 'Stop'
$libRoot = Join-Path $PSScriptRoot 'lib'
Import-Module (Join-Path $libRoot 'Common.psm1') -Force
Import-Module (Join-Path $libRoot 'ScanBackend.psm1') -Force
Import-Module (Join-Path $libRoot 'Identify.psm1') -Force
Import-Module (Join-Path $libRoot 'Risk.psm1') -Force

$source = Resolve-StorOpsPath -Path $Path
if (-not (Test-Path -LiteralPath $source -PathType Container)) {
    throw "StorOps: '$source' does not exist or is not a directory. migrate-plan.ps1 only plans directory migrations."
}
$destination = Resolve-StorOpsPath -Path $Destination
if ($destination -eq $source) {
    throw "StorOps: destination is the same as the source ('$source')."
}
if (Test-Path -LiteralPath $destination) {
    $existing = @(Get-ChildItem -LiteralPath $destination -Force -ErrorAction SilentlyContinue)
    if ($existing.Count -gt 0) {
        throw "StorOps: destination '$destination' already exists and is not empty. Pick an empty or new destination."
    }
}

$identity = Get-StorOpsPathIdentity -Path $source
Assert-StorOpsNotCritical -Identity $identity

if (-not $identity.Migratable) {
    throw "StorOps: '$source' (identified as $(if ($identity.Application) { $identity.Application } else { 'unknown' })'s $($identity.Category)) is not marked migratable. Run identify.ps1 for the full classification; StorOps will not invent a migration path for an unsupported category."
}

$sized = Get-StorOpsPathSize -Path $source -Admin:$Admin
$sizeBytes = if ($sized) { $sized.SizeBytes } else { 0L }

if ($identity.MigrationMethod -eq 'none') {
    throw "StorOps: '$source' is explicitly marked migration_method 'none' -- this data is not meant to be relocated."
}
$useJunction = $identity.MigrationMethod -notin @('app-config', 'manual')

# Best-effort, non-authoritative: process detection cannot be deterministic
# from an "application" display name alone. Treated only as a hint --
# migrate-execute.ps1 still requires an explicit -AppClosed acknowledgement
# whenever RequiresAppClosed is true, regardless of what this finds.
$processGuess = 'unknown'
if ($identity.Application) {
    $candidateNames = @($identity.Application, ($identity.Application -replace '\s', ''))
    $running = foreach ($name in $candidateNames) { Get-Process -Name $name -ErrorAction SilentlyContinue }
    if (@($running).Count -gt 0) { $processGuess = 'likely running' }
    else { $processGuess = 'not detected' }
}

$steps = New-Object System.Collections.Generic.List[string]
if ($identity.RequiresAppClosed) {
    $steps.Add("Close $(if ($identity.Application) { $identity.Application } else { 'the owning application' }) completely (process check: $processGuess -- verify yourself, this is a best-effort guess).")
}
$steps.Add("Copy '$source' to '$destination' (verified copy, original left in place until verification passes).")
$steps.Add("Verify the copy: file count and total size at destination must match the source.")
if ($useJunction) {
    $steps.Add("Remove the now-redundant original at '$source'.")
    $steps.Add("Create an NTFS junction at '$source' pointing to '$destination'.")
    $steps.Add('Verify the junction resolves and lists the same top-level contents as the destination.')
    $steps.Add("Start $(if ($identity.Application) { $identity.Application } else { 'the application' }) and confirm it works normally through the junction.")
}
else {
    $steps.Add("Apply the app's own relocation setting: $($identity.MigrationHint)")
    $steps.Add("Start $(if ($identity.Application) { $identity.Application } else { 'the application' }) and confirm it now reads/writes at '$destination'.")
    $steps.Add("Once confirmed, remove the now-redundant original at '$source'.")
}
$steps.Add('Run scripts/verify.ps1 against the migration result file to record the outcome.')

$plan = [PSCustomObject]@{
    GeneratedAt       = (Get-Date).ToString('o')
    Source            = $source
    Destination       = $destination
    Application       = $identity.Application
    Category          = $identity.Category
    SizeBytes         = [int64]$sizeBytes
    Risk              = $identity.CleanupRisk
    RequiresAppClosed = $identity.RequiresAppClosed
    ProcessGuess      = $processGuess
    Method            = if ($useJunction) { 'junction' } else { $identity.MigrationMethod }
    MigrationHint     = $identity.MigrationHint
    Steps             = $steps
    Backend           = Get-StorOpsScanBackendName
    BackendAdvice     = Get-StorOpsScanBackendAdvice
}

$outFile = Join-Path (Get-StorOpsWorkDir) 'storops-migrate-plan.json'
$plan | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $outFile -Encoding UTF8

if ($Json) {
    $plan | ConvertTo-Json -Depth 6
    return
}

Write-Host "StorOps migration plan" -ForegroundColor Cyan
Write-Host "  Application: $(if ($identity.Application) { $identity.Application } else { '(unidentified)' })"
Write-Host "  Source:      $source"
Write-Host "  Destination: $destination"
Write-Host "  Size:        $(Format-StorOpsSize $sizeBytes)"
Write-Host "  Risk:        $($identity.CleanupRisk)"
Write-Host "  Method:      $($plan.Method)"
if ($plan.RequiresAppClosed) {
    Write-Host "  App must be closed before executing (process check: $processGuess)" -ForegroundColor Yellow
}
Write-Host ''
Write-Host 'Steps:'
for ($i = 0; $i -lt $steps.Count; $i++) { Write-Host ("  {0}. {1}" -f ($i + 1), $steps[$i]) }
Write-Host ''
Write-Host "Plan saved to: $outFile"
Write-Host "Review the plan, then run: pwsh scripts/migrate-execute.ps1 -PlanFile '$outFile' -Confirm$(if ($plan.RequiresAppClosed) { ' -AppClosed' })" -ForegroundColor Green
