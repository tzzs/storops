#requires -Version 7.0
<#
    StorOps' last-resort Linux/macOS scan backend: the `du` that ships with
    every Unix-like system. Single-threaded, one stat() syscall per entry --
    noticeably slower than gdu on large trees, since there is no parallel
    directory walk and no filesystem-metadata shortcut the way WizTree has
    on NTFS (see docs/DESIGN.md §4a/§4b). Selected by ScanBackend.psm1 only
    when `gdu` is not found on PATH.

    GNU coreutils and BSD/macOS du use different flags for the same things
    (--max-depth vs -d, --apparent-size/-b vs no byte-size flag at all) --
    Get-StorOpsDuFlavor probes `du --version` once per session to pick the
    right set. Depth limiting is always passed natively to `du` itself
    (never "scan everything, then truncate in PowerShell"): -a combined with
    --max-depth/-d reports file-level rows only down to that depth, which is
    exactly what avoids turning a "just the top level" query into a full
    recursive scan.
#>

Set-StrictMode -Version Latest

$script:StorOpsDuFlavor = $null

function Get-StorOpsDuFlavor {
    <#
        'gnu' | 'bsd'. Cached per session (the flavor cannot change between
        calls within one run).
    #>
    [CmdletBinding()]
    param()

    if ($script:StorOpsDuFlavor) { return $script:StorOpsDuFlavor }

    $verOut = & du --version 2>$null
    $script:StorOpsDuFlavor = if ($LASTEXITCODE -eq 0 -and ($verOut -match 'GNU coreutils')) { 'gnu' } else { 'bsd' }
    return $script:StorOpsDuFlavor
}

function Invoke-StorOpsScan {
    <#
        StorOps' standardized scan entry point (docs/DESIGN.md §4a). -Admin
        is accepted for signature parity with the Windows backend but is a
        no-op here -- StorOps never silently re-execs itself under sudo.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$Path,

        [bool]$ExportFolders = $true,
        [bool]$ExportFiles = $false,
        [int]$MaxDepth = 0,
        [string]$Filter,
        [string]$FilterExclude,
        [switch]$Admin,
        [int]$TimeoutSeconds = 300
    )

    $target = Resolve-StorOpsPath -Path $Path
    if (-not (Test-Path -LiteralPath $target)) {
        throw "StorOps: '$target' does not exist."
    }

    $flavor = Get-StorOpsDuFlavor
    $duArgs = New-Object System.Collections.Generic.List[string]
    $duArgs.Add('-a')
    if ($flavor -eq 'gnu') {
        # -b = --apparent-size --block-size=1: logical/apparent size in
        # bytes, comparable to WizTree's "Size" column (not its "Allocated").
        $duArgs.Add('-b')
        if ($MaxDepth -gt 0) { $duArgs.Add("--max-depth=$MaxDepth") }
    }
    else {
        # BSD/macOS du has no portable apparent-size-in-bytes flag; report
        # 1024-byte blocks (-k) and scale below. This is disk-usage, not
        # apparent size, on this flavor -- a known, documented approximation.
        $duArgs.Add('-k')
        if ($MaxDepth -gt 0) { $duArgs.Add('-d'); $duArgs.Add("$MaxDepth") }
    }
    $duArgs.Add('--')
    $duArgs.Add($target)

    Write-Verbose "StorOps: running du ($flavor) on '$target' (maxDepth=$MaxDepth)"
    $raw = & du @duArgs 2>$null
    if ($LASTEXITCODE -ne 0 -and -not $raw) {
        throw "StorOps: du exited with code $LASTEXITCODE scanning '$target' (permission denied on a subtree? re-run the whole command under sudo -- StorOps never self-elevates)."
    }

    $rootSegments = @($target -split '[\\/]' | Where-Object { $_ -ne '' }).Count
    $entries = New-Object System.Collections.Generic.List[object]

    foreach ($line in @($raw)) {
        if (-not $line) { continue }
        $parts = $line -split "`t", 2
        if ($parts.Count -lt 2) { continue }

        $size = [int64]$parts[0]
        if ($flavor -ne 'gnu') { $size = $size * 1024 }
        $entryPath = $parts[1]
        if ($entryPath -eq $target) { continue }

        # Belt-and-braces: du was already asked to stop at -MaxDepth, this
        # just guards against any flavor quirk that returns deeper rows.
        $depth = (@($entryPath -split '[\\/]' | Where-Object { $_ -ne '' }).Count) - $rootSegments
        if ($MaxDepth -ne 0 -and $depth -gt $MaxDepth) { continue }

        $isFolder = Test-Path -LiteralPath $entryPath -PathType Container
        if (($isFolder -and -not $ExportFolders) -or ((-not $isFolder) -and -not $ExportFiles)) { continue }

        $name = Split-Path -Leaf $entryPath
        if ($Filter -and $name -notlike $Filter) { continue }
        if ($FilterExclude -and $name -like $FilterExclude) { continue }

        $entries.Add([PSCustomObject]@{
            FullName       = $entryPath
            IsFolder       = $isFolder
            SizeBytes      = $size
            AllocatedBytes = $size
            Modified       = $null
            FileCount      = $null
            FolderCount    = $null
        })
    }

    return @($entries)
}

function Get-StorOpsTopEntries {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$Path,

        [int]$Top = 20,
        [int]$MaxDepth = 1,
        [switch]$Admin,
        [switch]$IncludeFiles
    )

    Invoke-StorOpsScan -Path $Path -ExportFolders $true -ExportFiles:([bool]$IncludeFiles) `
        -MaxDepth $MaxDepth -Admin:$Admin |
        Sort-Object -Property SizeBytes -Descending |
        Select-Object -First $Top
}

function Get-StorOpsPathSize {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$Path,

        [switch]$Admin
    )

    $normalized = Resolve-StorOpsPath -Path $Path
    if (-not (Test-Path -LiteralPath $normalized)) { return $null }
    $parent = Split-Path -Parent $normalized
    if (-not $parent) { return $null }

    Invoke-StorOpsScan -Path $parent -ExportFolders $true -ExportFiles $true -MaxDepth 1 -Admin:$Admin |
        Where-Object { $_.FullName -eq $normalized } | Select-Object -First 1
}

Export-ModuleMember -Function @(
    'Get-StorOpsDuFlavor',
    'Invoke-StorOpsScan',
    'Get-StorOpsTopEntries',
    'Get-StorOpsPathSize'
)
