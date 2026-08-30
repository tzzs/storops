#requires -Version 5.1
<#
    StorOps risk classification and action recommendation. Turns a
    Get-StorOpsPathIdentity result into one of KEEP / DELETE / MOVE / CHECK
    (docs/DESIGN.md §6.4, §10, §11) and provides the guardrails
    cleanup/migration execute scripts must call before touching anything.
#>

Set-StrictMode -Version Latest

$script:StorOpsRiskOrder = @{
    'low'      = 0
    'medium'   = 1
    'high'     = 2
    'critical' = 3
}

function Get-StorOpsRiskRank {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Risk)

    $key = $Risk.ToLowerInvariant()
    if (-not $script:StorOpsRiskOrder.ContainsKey($key)) {
        throw "StorOps: unknown risk level '$Risk' (expected low/medium/high/critical)."
    }
    return $script:StorOpsRiskOrder[$key]
}

function Test-StorOpsRiskWithinLimit {
    <#
        Is $Risk at or below $MaxRisk in severity? Used to filter a cleanup
        plan down to only the risk tiers a caller (or the user) approved.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Risk,
        [Parameter(Mandatory)][string]$MaxRisk
    )

    return (Get-StorOpsRiskRank $Risk) -le (Get-StorOpsRiskRank $MaxRisk)
}

function Assert-StorOpsNotCritical {
    <#
        Last-line-of-defense guardrail: *-execute.ps1 scripts call this
        immediately before any delete/move/junction operation, independent
        of whatever a plan file claims, so a hand-edited or stale plan can
        never be used to touch a critical-risk path.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [object]$Identity
    )

    if ($Identity.CleanupRisk -and (Get-StorOpsRiskRank $Identity.CleanupRisk) -ge (Get-StorOpsRiskRank 'critical')) {
        throw "StorOps: refusing to operate on '$($Identity.Path)' -- classified CRITICAL risk ($($Identity.Category)). This tier is never eligible for automatic delete/move, regardless of what a plan file says."
    }
}

function Get-StorOpsRecommendedAction {
    <#
        Deterministic KEEP / DELETE / MOVE / CHECK recommendation for a
        Get-StorOpsPathIdentity result. This never runs anything -- it only
        labels a plan row; cleanup-plan.ps1 / migrate-plan.ps1 use it to
        decide what to propose, and the *-execute.ps1 scripts only ever act
        on rows the user has already approved.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [object]$Identity
    )

    if ($Identity.Category -eq 'unknown') {
        return [PSCustomObject]@{
            Action = 'CHECK'
            Reason = 'No identification rule matched this path -- StorOps does not guess; review it yourself before doing anything.'
        }
    }

    if ((Get-StorOpsRiskRank $Identity.CleanupRisk) -ge (Get-StorOpsRiskRank 'critical')) {
        return [PSCustomObject]@{
            Action = 'KEEP'
            Reason = "Classified CRITICAL risk ($($Identity.Category)) -- never offered for cleanup or migration."
        }
    }

    if ($Identity.Deletable -and (Get-StorOpsRiskRank $Identity.CleanupRisk) -le (Get-StorOpsRiskRank 'medium')) {
        return [PSCustomObject]@{
            Action = 'DELETE'
            Reason = "Identified as $($Identity.Application)'s $($Identity.Category); safely re-creatable/re-downloadable ($($Identity.CleanupRisk) risk)."
        }
    }

    if ($Identity.Migratable) {
        return [PSCustomObject]@{
            Action = 'MOVE'
            Reason = "Identified as $($Identity.Application)'s $($Identity.Category); large and portable, but not disposable -- prefer relocating over deleting."
        }
    }

    if (-not $Identity.Deletable -and -not $Identity.Migratable) {
        return [PSCustomObject]@{
            Action = 'KEEP'
            Reason = "Identified as $($Identity.Application)'s $($Identity.Category); neither safe to delete nor supported for migration."
        }
    }

    return [PSCustomObject]@{
        Action = 'CHECK'
        Reason = 'Identified, but does not cleanly fit an automatic recommendation -- review manually.'
    }
}

Export-ModuleMember -Function @(
    'Get-StorOpsRiskRank',
    'Test-StorOpsRiskWithinLimit',
    'Assert-StorOpsNotCritical',
    'Get-StorOpsRecommendedAction'
)
