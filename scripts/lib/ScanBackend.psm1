#requires -Version 5.1
<#
    StorOps' scan-backend dispatcher (docs/DESIGN.md §4a): picks the
    platform-appropriate storage-discovery backend and re-exports its
    Invoke-StorOpsScan / Get-StorOpsTopEntries / Get-StorOpsPathSize under
    one stable name. Every other script/module imports ONLY this module --
    never a specific backend directly -- so adding or swapping a platform's
    backend never requires touching an entry script.

    Selection order:
      1. Windows            -> backends/WizTree.psm1
      2. Linux/macOS + gdu   -> backends/Gdu.psm1  (parallel walk, closest
                                cross-platform equivalent to WizTree's speed)
      3. Linux/macOS, no gdu -> backends/Du.psm1    (always available,
                                single-threaded, slower on large trees --
                                a one-time warning is printed)
#>

Set-StrictMode -Version Latest

Import-Module (Join-Path $PSScriptRoot 'Common.psm1') -Force -Global

function Get-StorOpsScanBackendName {
    <#
        'WizTree' | 'Gdu' | 'Du' -- which backend ScanBackend.psm1 selected
        (or would select) for the current platform/environment.
    #>
    [CmdletBinding()]
    param()

    if ((Get-StorOpsPlatform) -eq 'Windows') { return 'WizTree' }
    if ($env:STOROPS_GDU_PATH -or (Get-Command -Name 'gdu' -ErrorAction SilentlyContinue)) { return 'Gdu' }
    return 'Du'
}

$script:StorOpsDuFallbackAdvice = "Install gdu for noticeably faster scans on large directory trees: https://github.com/dundee/gdu#installation"

function Get-StorOpsScanBackendAdvice {
    <#
        $null when the active backend is the recommended one for this
        platform; otherwise a short, human-readable suggestion (currently
        only fires for the Du fallback). Entry scripts fold this into their
        -Json output as `BackendAdvice` so an agent can act on it reliably --
        unlike the Write-Warning below, which only reaches a human running
        the script directly in a terminal and depends on the caller
        capturing the warning stream at all.
    #>
    [CmdletBinding()]
    param()

    if ((Get-StorOpsScanBackendName) -eq 'Du') { return $script:StorOpsDuFallbackAdvice }
    return $null
}

$script:StorOpsBackendName = Get-StorOpsScanBackendName
if ($script:StorOpsBackendName -eq 'Du') {
    Write-Warning "StorOps: 'gdu' not found on PATH -- falling back to 'du', which is noticeably slower on large directory trees (single-threaded, no MFT-like shortcut). $script:StorOpsDuFallbackAdvice"
}

$backendsDir = Join-Path $PSScriptRoot 'backends'
$backendFile = Join-Path $backendsDir "$script:StorOpsBackendName.psm1"
Import-Module $backendFile -Force

Export-ModuleMember -Function @(
    'Get-StorOpsScanBackendName',
    'Get-StorOpsScanBackendAdvice',
    'Invoke-StorOpsScan',
    'Get-StorOpsTopEntries',
    'Get-StorOpsPathSize'
)
