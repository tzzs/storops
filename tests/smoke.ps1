#requires -Version 5.1
<#
    Compatibility-layer smoke test (docs/plans/storops-v2-cross-platform-
    refactor.md §2.10/§2.12). Rule loading, path identification, and risk
    classification now live in the Python core (src/storops/core/) and are
    covered by tests/unit/test_rules.py and tests/unit/test_risk.py -- this
    file no longer duplicates that coverage against scripts/lib/Identify.psm1
    or scripts/lib/Risk.psm1, both of which have been removed (they carried
    no logic independent of the Python core after the scripts/*.ps1 wrapper
    rewrite; see git history for the pre-rewrite version if needed).

    What this file checks instead: that the scripts/*.ps1 compatibility
    wrappers still work end-to-end -- that they resolve a Python
    interpreter, translate their parameters into `storops` CLI flags
    correctly, and that the JSON on stdout in -Json mode parses and has the
    expected top-level keys. This is deliberately shallow (plumbing, not
    business logic) -- see src/storops/cli.py and tests/integration/ for the
    CLI's own, more thorough test coverage.

    Requires Python 3.11+ on PATH (same requirement as the wrappers
    themselves).

    Run on Windows PowerShell / PowerShell 7:
        pwsh tests/smoke.ps1
#>

param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$scriptsDir = Join-Path $root 'scripts'

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

Write-Host "StorOps compatibility-wrapper smoke test" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

$pythonCmd = Get-Command -Name 'python3' -ErrorAction SilentlyContinue
if (-not $pythonCmd) { $pythonCmd = Get-Command -Name 'python' -ErrorAction SilentlyContinue }
if (-not $pythonCmd) {
    Write-Host "  SKIP  no Python 3.11+ interpreter on PATH -- cannot exercise the wrappers, which now delegate to it." -ForegroundColor Yellow
    exit 0
}

# --- Build a small, disposable directory tree to scan/identify against ------
$tmpRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("storops-smoke-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tmpRoot -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $tmpRoot 'subdir') -Force | Out-Null
Set-Content -LiteralPath (Join-Path $tmpRoot 'subdir/file.txt') -Value ('x' * 1024)

try {
    # --- scan.ps1 -Json: parses, has the expected top-level keys -----------
    Write-Host "`nscan.ps1 -Json"
    $scanOut = & (Join-Path $scriptsDir 'scan.ps1') -Path $tmpRoot -Json
    Assert-True -Condition ($LASTEXITCODE -eq 0) -Message "scan.ps1 exits 0"
    $scanJson = $null
    try { $scanJson = $scanOut | ConvertFrom-Json } catch { }
    Assert-True -Condition ($null -ne $scanJson) -Message "scan.ps1 -Json stdout is valid JSON"
    if ($scanJson) {
        foreach ($key in 'ScannedPath', 'Entries', 'Backend') {
            Assert-True -Condition ([bool]($scanJson.PSObject.Properties.Name -contains $key)) -Message "scan.ps1 JSON has top-level key '$key'"
        }
    }

    # --- identify.ps1 -Json: parses, has the expected top-level keys -------
    Write-Host "`nidentify.ps1 -Json"
    $identifyOut = & (Join-Path $scriptsDir 'identify.ps1') -Path $tmpRoot -Json
    Assert-True -Condition ($LASTEXITCODE -eq 0) -Message "identify.ps1 exits 0"
    $identifyJson = $null
    try { $identifyJson = $identifyOut | ConvertFrom-Json } catch { }
    Assert-True -Condition ($null -ne $identifyJson) -Message "identify.ps1 -Json stdout is valid JSON"
    if ($identifyJson) {
        foreach ($key in 'Identity', 'Recommended') {
            Assert-True -Condition ([bool]($identifyJson.PSObject.Properties.Name -contains $key)) -Message "identify.ps1 JSON has top-level key '$key'"
        }
    }

    # --- cleanup-plan.ps1 -Json: parses, has the expected top-level keys ---
    Write-Host "`ncleanup-plan.ps1 -Json"
    $planFile = Join-Path $tmpRoot 'plan.json'
    $planOut = & (Join-Path $scriptsDir 'cleanup-plan.ps1') -OutFile $planFile -Json
    Assert-True -Condition ($LASTEXITCODE -eq 0) -Message "cleanup-plan.ps1 exits 0"
    $planJson = $null
    try { $planJson = $planOut | ConvertFrom-Json } catch { }
    Assert-True -Condition ($null -ne $planJson) -Message "cleanup-plan.ps1 -Json stdout is valid JSON"
    if ($planJson) {
        foreach ($key in 'Items', 'TotalReclaimableBytes', 'Backend') {
            Assert-True -Condition ([bool]($planJson.PSObject.Properties.Name -contains $key)) -Message "cleanup-plan.ps1 JSON has top-level key '$key'"
        }
    }
}
finally {
    Remove-Item -LiteralPath $tmpRoot -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host "`n==========================================" -ForegroundColor Cyan
Write-Host "$($script:passed) passed, $($script:failures) failed" -ForegroundColor $(if ($script:failures -eq 0) { 'Green' } else { 'Red' })

if ($script:failures -gt 0) { exit 1 }
exit 0
