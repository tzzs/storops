#requires -Version 7.0
<#
    StorOps' preferred Linux/macOS scan backend: gdu
    (https://github.com/dundee/gdu), a Go disk-usage analyzer that walks
    directories with a goroutine pool instead of one file at a time -- the
    closest cross-platform equivalent to WizTree's speed advantage that is
    achievable without reading filesystem metadata directly (no ext4/APFS
    has a public, stable MFT-like read path -- see docs/DESIGN.md §4a/§4b).
    Selected automatically by ScanBackend.psm1 when `gdu` is on PATH (or
    $env:STOROPS_GDU_PATH is set); falls back to backends/Du.psm1 otherwise.

    NOTE: authored without a live gdu install to test against (this repo's
    dev environment has neither pwsh nor gdu available). The JSON export
    shape parsed below follows gdu's own documented dump format -- a
    [schemaVersion, flags, rootNode] array where each node carries
    name/asize/dsize/isDir/files -- but this is the one thing here that
    should be double-checked against `gdu --help` and a real
    `gdu -n -o out.json <path>` run before relying on it in production, the
    same caveat WizTree.psm1 carries for its own CLI assumptions.

    gdu always builds the *whole* tree before exporting (no native
    depth-limit flag the way WizTree's /exportmaxdepth or du's --max-depth
    provide) -- MaxDepth is applied by StorOps after the fact, by simply not
    recursing/emitting past it. This is usually still fast because the walk
    itself is parallel, but for a "just the top level" query on a very large
    tree, backends/Du.psm1 (native depth-limiting, single-threaded) can
    occasionally win despite the lack of parallelism. There is no universal
    answer here; gdu remains the recommended default.
#>

Set-StrictMode -Version Latest

function Get-StorOpsGduPath {
    <#
        Resolve the gdu executable, in priority order:
          1. $env:STOROPS_GDU_PATH (full path to the binary)
          2. `gdu` on PATH
        Mirrors the WizTree backend's $env:STOROPS_WIZTREE_PATH convention.
    #>
    [CmdletBinding()]
    param()

    if ($env:STOROPS_GDU_PATH) {
        if (Test-Path -LiteralPath $env:STOROPS_GDU_PATH -PathType Leaf) {
            return (Resolve-Path -LiteralPath $env:STOROPS_GDU_PATH).Path
        }
        throw "StorOps: `$env:STOROPS_GDU_PATH is set to '$env:STOROPS_GDU_PATH' but that file was not found."
    }

    $cmd = Get-Command -Name 'gdu' -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($cmd) { return $cmd.Source }

    throw @'
StorOps could not locate gdu. Install it (e.g. `brew install gdu`,
`apt install gdu`, or see https://github.com/dundee/gdu#installation), or
point StorOps at an existing binary via:
    $env:STOROPS_GDU_PATH = '/path/to/gdu'
StorOps will fall back to the slower system `du` if gdu is unavailable.
'@
}

function ConvertFrom-GduNode {
    <#
        Flatten one gdu tree node's children into the StorOps standard scan
        result shape, recursing only as far as -MaxDepth allows (0 =
        unlimited). $Depth = 1 means "immediate child of the scanned root",
        matching WizTree's /exportmaxdepth "relative to scanned target"
        semantics (WizTree.psm1's own header comment).
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][object]$Node,
        [Parameter(Mandatory)][string]$ParentPath,
        [Parameter(Mandatory)][int]$Depth,
        [int]$MaxDepth = 0,
        [bool]$ExportFolders = $true,
        [bool]$ExportFiles = $false
    )

    $results = New-Object System.Collections.Generic.List[object]
    $fullPath = Join-Path $ParentPath $Node.name
    $isDir = [bool]$Node.isDir

    if ($MaxDepth -eq 0 -or $Depth -le $MaxDepth) {
        if (($isDir -and $ExportFolders) -or ((-not $isDir) -and $ExportFiles)) {
            $fileCount = $null
            $folderCount = $null
            if ($isDir -and $Node.files) {
                $fileCount = @($Node.files | Where-Object { -not $_.isDir }).Count
                $folderCount = @($Node.files | Where-Object { $_.isDir }).Count
            }
            $results.Add([PSCustomObject]@{
                FullName       = $fullPath
                IsFolder       = $isDir
                SizeBytes      = [int64]$Node.asize
                AllocatedBytes = if ($null -ne $Node.dsize) { [int64]$Node.dsize } else { [int64]$Node.asize }
                Modified       = $null  # gdu's JSON export does not carry mtimes
                FileCount      = $fileCount
                FolderCount    = $folderCount
            })
        }
    }

    if ($isDir -and $Node.files -and ($MaxDepth -eq 0 -or $Depth -lt $MaxDepth)) {
        foreach ($child in $Node.files) {
            $results.AddRange((ConvertFrom-GduNode -Node $child -ParentPath $fullPath `
                -Depth ($Depth + 1) -MaxDepth $MaxDepth -ExportFolders $ExportFolders -ExportFiles $ExportFiles))
        }
    }

    return $results
}

function Invoke-StorOpsScan {
    <#
        StorOps' standardized scan entry point (docs/DESIGN.md §4a). -Admin
        is accepted for signature parity with the Windows backend but is a
        no-op here: StorOps never silently re-execs itself under sudo. If a
        scan hits Permission Denied subtrees, re-run the whole command under
        sudo yourself.
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

    $exe = Get-StorOpsGduPath
    $target = Resolve-StorOpsPath -Path $Path
    if (-not (Test-Path -LiteralPath $target)) {
        throw "StorOps: '$target' does not exist."
    }
    $outFile = New-StorOpsTempFile -Extension '.json'
    if (Test-Path -LiteralPath $outFile) {
        Remove-Item -LiteralPath $outFile -Force -ErrorAction SilentlyContinue
    }

    # -n: no ANSI color codes in any incidental output. -o: write the JSON
    # export and exit instead of opening the interactive TUI.
    $psi = [System.Diagnostics.ProcessStartInfo]::new()
    $psi.FileName = $exe
    $psi.ArgumentList.Add('-n')
    $psi.ArgumentList.Add('-o')
    $psi.ArgumentList.Add($outFile)
    $psi.ArgumentList.Add($target)
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true

    Write-Verbose "StorOps: running gdu on '$target' -> '$outFile'"
    $proc = [System.Diagnostics.Process]::Start($psi)
    if (-not $proc.WaitForExit($TimeoutSeconds * 1000)) {
        try { $proc.Kill() } catch { }
        throw "StorOps: gdu did not finish scanning '$target' within $TimeoutSeconds seconds."
    }
    if (-not (Test-Path -LiteralPath $outFile)) {
        throw "StorOps: gdu exited (code $($proc.ExitCode)) without producing an export at '$outFile'. Confirm the path exists and is readable (re-run the whole command under sudo for permission-denied subtrees -- StorOps never self-elevates)."
    }

    try {
        $raw = Get-Content -LiteralPath $outFile -Raw | ConvertFrom-Json
        $rootNode = $raw[2]

        $entries = New-Object System.Collections.Generic.List[object]
        if ($rootNode.files) {
            foreach ($child in $rootNode.files) {
                $entries.AddRange((ConvertFrom-GduNode -Node $child -ParentPath $target `
                    -Depth 1 -MaxDepth $MaxDepth -ExportFolders $ExportFolders -ExportFiles $ExportFiles))
            }
        }

        # gdu has no native per-file name filter (only directory-exclude via
        # -i, not exposed here); apply Filter/FilterExclude client-side
        # against each entry's leaf name to match WizTree's /filter behavior
        # closely enough for search.ps1's purposes.
        if ($Filter) {
            $entries = @($entries | Where-Object { (Split-Path -Leaf $_.FullName) -like $Filter })
        }
        if ($FilterExclude) {
            $entries = @($entries | Where-Object { (Split-Path -Leaf $_.FullName) -notlike $FilterExclude })
        }

        # NOTE: deliberately NOT `return @($entries)` -- see the matching
        # comment in backends/Du.psm1 for the PowerShell 7.4.x engine bug
        # this works around (`@()` on a List[object] throws "Argument types
        # do not match", independent of content).
        return $entries
    }
    finally {
        Remove-Item -LiteralPath $outFile -Force -ErrorAction SilentlyContinue
    }
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
    'Get-StorOpsGduPath',
    'Invoke-StorOpsScan',
    'Get-StorOpsTopEntries',
    'Get-StorOpsPathSize'
)
