#requires -Version 5.1
<#
    Dependency-free smoke test for the rule reader (scripts/lib/Identify.psm1)
    and risk engine (scripts/lib/Risk.psm1). No Pester dependency on purpose,
    to keep StorOps installable with nothing beyond PowerShell + WizTree.

    Run on Windows PowerShell / PowerShell 7:
        pwsh tests/smoke.ps1

    This repo was authored without access to a Windows machine, so this is
    the first thing to run after cloning on real Windows before trusting any
    other script here -- it specifically exercises the hand-rolled YAML-subset
    reader in Identify.psm1 against the real rules/*.yaml files.
#>

param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
Import-Module (Join-Path $root 'scripts\lib\Common.psm1') -Force
Import-Module (Join-Path $root 'scripts\lib\Identify.psm1') -Force
Import-Module (Join-Path $root 'scripts\lib\Risk.psm1') -Force

$script:failures = 0
$script:passed = 0

function Assert-True {
    param(
        [Parameter(Mandatory)][bool]$Condition,
        [Parameter(Mandatory)][string]$Message
    )
    if ($Condition) {
        $script:passed++
        Write-Host "  PASS  $Message" -ForegroundColor Green
    } else {
        $script:failures++
        Write-Host "  FAIL  $Message" -ForegroundColor Red
    }
}

function Assert-Equal {
    param(
        [Parameter(Mandatory)]$Expected,
        [Parameter(Mandatory)]$Actual,
        [Parameter(Mandatory)][string]$Message
    )
    Assert-True -Condition ($Expected -eq $Actual) -Message "$Message (expected '$Expected', got '$Actual')"
}

Write-Host "StorOps smoke test" -ForegroundColor Cyan
Write-Host "===================" -ForegroundColor Cyan

# --- Rule loading ---------------------------------------------------------
Write-Host "`nRule loading"
$rules = Get-StorOpsRules -Force
Assert-True -Condition ($rules.Count -ge 20) -Message "loaded at least 20 rules across all files (got $($rules.Count))"

$lmstudio = $rules | Where-Object { $_.id -eq 'lmstudio-models' } | Select-Object -First 1
Assert-True -Condition ($null -ne $lmstudio) -Message "found rule 'lmstudio-models'"
if ($lmstudio) {
    Assert-Equal -Expected 'LM Studio' -Actual $lmstudio.application -Message "lmstudio-models.application"
    Assert-Equal -Expected 'ai-model-weights' -Actual $lmstudio.category -Message "lmstudio-models.category"
    Assert-Equal -Expected 'high' -Actual $lmstudio.cleanup_risk -Message "lmstudio-models.cleanup_risk"
    Assert-True -Condition ($lmstudio.path_patterns -is [array] -and $lmstudio.path_patterns.Count -eq 2) -Message "lmstudio-models.path_patterns parsed as a 2-item list"
    Assert-True -Condition ($lmstudio.purpose -match 'GGUF') -Message "lmstudio-models.purpose folded block scalar parsed ('$($lmstudio.purpose)')"
}

# --- Path identification ---------------------------------------------------
Write-Host "`nPath identification"

$lmstudioPath = Join-Path $env:USERPROFILE '.lmstudio\models\publisher\repo\model.gguf'
$id1 = Get-StorOpsPathIdentity -Path $lmstudioPath
Assert-Equal -Expected 'LM Studio' -Actual $id1.Application -Message "identifies LM Studio model path"
Assert-Equal -Expected $true -Actual $id1.Migratable -Message "LM Studio models are migratable"
Assert-Equal -Expected $false -Actual $id1.Deletable -Message "LM Studio models are not casually deletable"

$winPath = Join-Path $env:SystemRoot 'System32\drivers\etc\hosts'
$id2 = Get-StorOpsPathIdentity -Path $winPath
Assert-Equal -Expected 'os-system' -Actual $id2.Category -Message "identifies a Windows system path as os-system"
Assert-Equal -Expected 'critical' -Actual $id2.CleanupRisk -Message "Windows system path is critical risk"

$unknownPath = 'C:\StorOpsTestOnly\definitely-not-a-real-known-app\data'
$id3 = Get-StorOpsPathIdentity -Path $unknownPath
Assert-Equal -Expected 'unknown' -Actual $id3.Category -Message "unmatched path classified as unknown, not guessed"
Assert-Equal -Expected 'critical' -Actual $id3.CleanupRisk -Message "unknown path defaults to critical risk (never assumed safe)"

# --- Risk engine ------------------------------------------------------------
Write-Host "`nRisk engine"
Assert-True -Condition ((Get-StorOpsRiskRank 'low') -lt (Get-StorOpsRiskRank 'critical')) -Message "risk ranks order low < critical"
Assert-True -Condition (Test-StorOpsRiskWithinLimit -Risk 'low' -MaxRisk 'medium') -Message "low is within a medium limit"
Assert-True -Condition (-not (Test-StorOpsRiskWithinLimit -Risk 'high' -MaxRisk 'medium')) -Message "high is not within a medium limit"

$threw = $false
try { Assert-StorOpsNotCritical -Identity $id2 } catch { $threw = $true }
Assert-True -Condition $threw -Message "Assert-StorOpsNotCritical throws for a critical-risk identity"

$rec1 = Get-StorOpsRecommendedAction -Identity $id1
Assert-Equal -Expected 'MOVE' -Actual $rec1.Action -Message "LM Studio models recommended as MOVE"

$rec2 = Get-StorOpsRecommendedAction -Identity $id2
Assert-Equal -Expected 'KEEP' -Actual $rec2.Action -Message "Windows system path recommended as KEEP"

$rec3 = Get-StorOpsRecommendedAction -Identity $id3
Assert-Equal -Expected 'CHECK' -Actual $rec3.Action -Message "unknown path recommended as CHECK"

# --- Formatting --------------------------------------------------------------
Write-Host "`nFormatting"
Assert-Equal -Expected '87.20 GB' -Actual (Format-StorOpsSize -Bytes 93627028848) -Message "Format-StorOpsSize renders GB with 2 decimals"

Write-Host "`n===================" -ForegroundColor Cyan
Write-Host "$($script:passed) passed, $($script:failures) failed" -ForegroundColor $(if ($script:failures -eq 0) { 'Green' } else { 'Red' })

if ($script:failures -gt 0) { exit 1 }
exit 0
