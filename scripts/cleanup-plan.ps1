#requires -Version 5.1
<#
.SYNOPSIS
    Plan-tier: probe the rule base's deletable candidate paths, size the
    ones that exist, and produce an itemized cleanup plan JSON for a human
    to review before anything is deleted (docs/DESIGN.md §10, §11).

.EXAMPLE
    pwsh scripts/cleanup-plan.ps1
    pwsh scripts/cleanup-plan.ps1 -MaxRisk medium -OutFile C:\temp\plan.json

.NOTES
    Plan-tier capability: read-only, produces a plan file but never deletes
    anything. cleanup-execute.ps1 is the only script allowed to act on the
    plan this produces, and only on items marked Approved.

    Candidate discovery is deliberately narrow for the MVP: only
    deletable:true rules whose path_patterns are of the form
    "%TOKEN%\...\*" are probed (the trailing "\*" is stripped to get a
    concrete directory to test/size). Patterns using a drive-relative
    wildcard (e.g. "?:\...", "*\...") or a filename glob (e.g.
    "thumbcache_*.db") are not auto-probed in v1 -- StorOps does not guess
    at expanding those, it simply skips them here (they're still available
    via identify.ps1/search.ps1 for a targeted, user-directed look).
#>
[CmdletBinding()]
param(
    [ValidateSet('low', 'medium', 'high')]
    [string]$MaxRisk = 'low',

    [string]$OutFile,

    [switch]$Admin,
    [switch]$Json
)

$ErrorActionPreference = 'Stop'
$libRoot = Join-Path $PSScriptRoot 'lib'
Import-Module (Join-Path $libRoot 'Common.psm1') -Force
Import-Module (Join-Path $libRoot 'ScanBackend.psm1') -Force
Import-Module (Join-Path $libRoot 'Identify.psm1') -Force
Import-Module (Join-Path $libRoot 'Risk.psm1') -Force

function Get-StorOpsProbePath {
    <#
        A deletable rule's path_pattern is only usable as a concrete probe
        target when it's an env-token-rooted directory wildcard
        ("%TOKEN%\...\*"). Anything else (drive-relative wildcards,
        filename globs) is skipped -- see script NOTES above.
    #>
    param([Parameter(Mandatory)][string]$Pattern)

    if ($Pattern -notmatch '^%[A-Z0-9_()]+%') { return $null }
    if (-not $Pattern.EndsWith('\*')) { return $null }

    $stripped = $Pattern.Substring(0, $Pattern.Length - 2)
    return Expand-StorOpsPatternTokens -Pattern $stripped
}

Write-Verbose "StorOps: building cleanup plan (maxRisk=$MaxRisk)"
$rules = Get-StorOpsRules | Where-Object { $_.deletable -eq $true }

$probes = [ordered]@{}
foreach ($rule in $rules) {
    foreach ($pattern in @($rule.path_patterns)) {
        $probePath = Get-StorOpsProbePath -Pattern $pattern
        if (-not $probePath) { continue }
        if (-not $probes.Contains($probePath)) {
            $probes[$probePath] = $rule
        }
    }
}

$items = New-Object System.Collections.Generic.List[object]
foreach ($probePath in $probes.Keys) {
    if (-not (Test-Path -LiteralPath $probePath)) { continue }

    $sized = Get-StorOpsPathSize -Path $probePath -Admin:$Admin
    if (-not $sized -or $sized.SizeBytes -le 0) { continue }

    $identity = Get-StorOpsPathIdentity -Path $probePath
    $action = Get-StorOpsRecommendedAction -Identity $identity
    if ($action.Action -ne 'DELETE') { continue }

    $approved = Test-StorOpsRiskWithinLimit -Risk $identity.CleanupRisk -MaxRisk $MaxRisk

    $items.Add([PSCustomObject]@{
        Id          = $identity.MatchedRuleId
        Path        = $identity.Path
        Application = $identity.Application
        Category    = $identity.Category
        SizeBytes   = $sized.SizeBytes
        Risk        = $identity.CleanupRisk
        Consequence = $identity.Consequence
        Action      = 'DELETE'
        Approved    = $approved
    })
}

$approvedItems = @($items | Where-Object { $_.Approved })
$totalReclaimable = ($approvedItems | Measure-Object -Property SizeBytes -Sum).Sum
if (-not $totalReclaimable) { $totalReclaimable = 0L }
$totalCandidate = ($items | Measure-Object -Property SizeBytes -Sum).Sum
if (-not $totalCandidate) { $totalCandidate = 0L }

$plan = [PSCustomObject]@{
    GeneratedAt             = (Get-Date).ToString('o')
    MaxRisk                 = $MaxRisk
    Items                   = $items
    TotalReclaimableBytes   = [int64]$totalReclaimable
    TotalCandidateBytes     = [int64]$totalCandidate
    Backend                 = Get-StorOpsScanBackendName
    BackendAdvice           = Get-StorOpsScanBackendAdvice
}

if (-not $OutFile) {
    $OutFile = Join-Path (Get-StorOpsWorkDir) 'storops-cleanup-plan.json'
}
$plan | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $OutFile -Encoding UTF8

if ($Json) {
    $plan | ConvertTo-Json -Depth 6
    return
}

Write-Host "StorOps cleanup plan (maxRisk=$MaxRisk)" -ForegroundColor Cyan
Write-Host ''

foreach ($tier in 'low', 'medium', 'high') {
    $tierItems = @($items | Where-Object { $_.Risk -eq $tier })
    if ($tierItems.Count -eq 0) { continue }

    Write-Host "[$tier risk]" -ForegroundColor Yellow
    foreach ($item in $tierItems) {
        $mark = if ($item.Approved) { '[x]' } else { '[ ]' }
        $label = if ($item.Application) { $item.Application } else { '(unidentified)' }
        "{0} {1,10}  {2,-18}{3}" -f $mark, (Format-StorOpsSize $item.SizeBytes), $label, $item.Path | Write-Host
        if ($item.Consequence -and $tier -ne 'low') {
            Write-Host "      -> $($item.Consequence)" -ForegroundColor DarkGray
        }
    }
    Write-Host ''
}

if ($items.Count -eq 0) {
    Write-Host '(no deletable candidates found)' -ForegroundColor DarkGray
}

Write-Host "Plan saved to: $OutFile"
Write-Host ("Reclaimable (approved, <= {0} risk): {1} of {2} candidate total" -f `
    $MaxRisk, (Format-StorOpsSize $totalReclaimable), (Format-StorOpsSize $totalCandidate))
Write-Host ''
Write-Host "Review the plan, then run: pwsh scripts/cleanup-execute.ps1 -PlanFile '$OutFile' -Confirm" -ForegroundColor Green
