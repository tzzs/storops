#requires -Version 5.1
<#
    StorOps' only integration point with WizTree: locate the CLI, invoke its
    command-line export, and parse the resulting CSV into PowerShell objects.

    StorOps never drives the WizTree GUI (no automation/screenshots/OCR) and
    never re-implements NTFS/MFT scanning itself -- see docs/DESIGN.md §5.

    NOTE: this module was authored without access to a Windows machine with
    WizTree installed, so the CLI flags below are taken from WizTree's own
    published CLI documentation (diskanalyzer.com/guide) rather than from a
    live test run. The one behavior that should be double-checked on a real
    install is whether `/exportmaxdepth` counts depth relative to the scanned
    target path or relative to the drive root -- this module assumes "relative
    to the scanned target", which is what makes the scan -> inspect drill-down
    workflow in docs/DESIGN.md §5/§6 work as described.
#>

Set-StrictMode -Version Latest

function Get-StorOpsWizTreePath {
    <#
        Resolve the WizTree CLI executable, in priority order:
          1. $env:STOROPS_WIZTREE_PATH (full path to the exe, or its folder)
          2. WizTree64.exe / WizTree.exe on PATH
          3. Well-known install locations under Program Files
    #>
    [CmdletBinding()]
    param()

    if ($env:STOROPS_WIZTREE_PATH) {
        $candidate = $env:STOROPS_WIZTREE_PATH
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
        if (Test-Path -LiteralPath $candidate -PathType Container) {
            foreach ($name in 'WizTree64.exe', 'WizTree.exe') {
                $p = Join-Path $candidate $name
                if (Test-Path -LiteralPath $p) { return (Resolve-Path -LiteralPath $p).Path }
            }
        }
        throw "StorOps: `$env:STOROPS_WIZTREE_PATH is set to '$candidate' but WizTree64.exe/WizTree.exe was not found there."
    }

    $cmd = Get-Command -Name 'WizTree64.exe', 'WizTree.exe' -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($cmd) { return $cmd.Source }

    $roots = @($env:ProgramFiles, ${env:ProgramFiles(x86)}, $env:LOCALAPPDATA) | Where-Object { $_ }
    foreach ($root in $roots) {
        foreach ($name in 'WizTree64.exe', 'WizTree.exe') {
            $p = Join-Path (Join-Path $root 'WizTree') $name
            if (Test-Path -LiteralPath $p) { return $p }
        }
    }

    throw @'
StorOps could not locate WizTree. Install it from https://diskanalyzer.com/,
or point StorOps at an existing install via:
    $env:STOROPS_WIZTREE_PATH = 'C:\Path\To\WizTree64.exe'
StorOps only calls WizTree's command-line export -- it never drives the GUI.
'@
}

function Invoke-WizTreeScan {
    <#
        Run a single WizTree CLI export and return the path to the CSV it
        produced. This is the only function in StorOps that launches an
        external process on WizTree's behalf; every other capability
        consumes its output.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$Path,

        [string]$OutputCsv,

        # /admin=1 makes WizTree read the NTFS MFT directly (faster, more
        # complete) but self-elevates via UAC -- expect an interactive
        # prompt the first time in a given session.
        [switch]$Admin,

        [bool]$ExportFolders = $true,
        [bool]$ExportFiles = $false,

        # 0 = unlimited. Keep this small for drive-level scans to avoid
        # exporting the whole tree (docs/DESIGN.md §5: "control export size").
        [int]$MaxDepth = 0,

        # 0=name, 1=size desc, 2=allocated desc, 3=date desc
        [int]$SortBy = 1,

        [string]$Filter,
        [string]$FilterExclude,

        [int]$TimeoutSeconds = 300
    )

    $exe = Get-StorOpsWizTreePath
    if (-not $OutputCsv) { $OutputCsv = New-StorOpsTempFile -Extension '.csv' }
    $target = Resolve-StorOpsPath -Path $Path

    if (Test-Path -LiteralPath $OutputCsv) {
        Remove-Item -LiteralPath $OutputCsv -Force -ErrorAction SilentlyContinue
    }

    $psi = [System.Diagnostics.ProcessStartInfo]::new()
    $psi.FileName = $exe
    $psi.ArgumentList.Add($target)
    $psi.ArgumentList.Add("/export=$OutputCsv")
    $psi.ArgumentList.Add("/exportfolders=$([int]$ExportFolders)")
    $psi.ArgumentList.Add("/exportfiles=$([int]$ExportFiles)")
    $psi.ArgumentList.Add("/exportmaxdepth=$MaxDepth")
    $psi.ArgumentList.Add("/sortby=$SortBy")
    $psi.ArgumentList.Add("/admin=$([int]$Admin.IsPresent)")
    if ($Filter) { $psi.ArgumentList.Add("/filter=$Filter") }
    if ($FilterExclude) { $psi.ArgumentList.Add("/filterexclude=$FilterExclude") }
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true

    Write-Verbose "StorOps: running WizTree on '$target' -> '$OutputCsv'"
    $proc = [System.Diagnostics.Process]::Start($psi)
    if (-not $proc.WaitForExit($TimeoutSeconds * 1000)) {
        try { $proc.Kill() } catch { }
        throw "StorOps: WizTree did not finish scanning '$target' within $TimeoutSeconds seconds."
    }

    if (-not (Test-Path -LiteralPath $OutputCsv)) {
        throw "StorOps: WizTree exited (code $($proc.ExitCode)) without producing an export at '$OutputCsv'. Confirm the path exists and is readable (try -Admin for a full MFT scan)."
    }

    return $OutputCsv
}

function ConvertFrom-WizTreeCsv {
    <#
        Parse a WizTree CSV export into objects with typed, predictable
        property names, regardless of which optional export columns were
        enabled. Columns: "File Name, Size, Allocated, Modified, Attributes,
        Files, Folders" -- folder rows have a trailing backslash in "File Name".
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$CsvPath
    )

    $lines = Get-Content -LiteralPath $CsvPath
    $headerIndex = -1
    for ($i = 0; $i -lt $lines.Length; $i++) {
        if ($lines[$i] -match '^"?File Name"?\s*,') {
            $headerIndex = $i
            break
        }
    }
    if ($headerIndex -lt 0) {
        throw "StorOps: '$CsvPath' does not look like a WizTree export (no 'File Name' header row found)."
    }

    $rows = $lines[$headerIndex..($lines.Length - 1)] | ConvertFrom-Csv
    foreach ($row in $rows) {
        $props = $row.PSObject.Properties.Name
        $name = $row.'File Name'
        if ([string]::IsNullOrEmpty($name)) { continue }

        $isFolder = $name.EndsWith('\')
        $fullName = if ($isFolder) { $name.TrimEnd('\') } else { $name }

        $sizeBytes = 0L
        [void][int64]::TryParse($row.Size, [ref]$sizeBytes)
        $allocatedBytes = 0L
        [void][int64]::TryParse($row.Allocated, [ref]$allocatedBytes)

        $modified = $null
        if ($props -contains 'Modified' -and $row.Modified) {
            $parsed = [datetime]::MinValue
            if ([datetime]::TryParse($row.Modified, [ref]$parsed)) { $modified = $parsed }
        }

        $fileCount = $null
        if ($props -contains 'Files' -and $row.Files -ne '') { $fileCount = [int64]$row.Files }
        $folderCount = $null
        if ($props -contains 'Folders' -and $row.Folders -ne '') { $folderCount = [int64]$row.Folders }

        [PSCustomObject]@{
            FullName       = $fullName
            IsFolder       = $isFolder
            SizeBytes      = $sizeBytes
            AllocatedBytes = $allocatedBytes
            Modified       = $modified
            FileCount      = $fileCount
            FolderCount    = $folderCount
        }
    }
}

function Get-StorOpsTopEntries {
    <#
        The workhorse behind scan.ps1 and inspect.ps1: scan a target path to
        a shallow depth, return its N largest immediate children. Reused for
        both "top directories on a drive" and "drill into this one big
        folder" -- the only difference is the Path argument.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$Path,

        [int]$Top = 20,
        [int]$MaxDepth = 1,
        [switch]$Admin,
        [switch]$IncludeFiles
    )

    $csv = Invoke-WizTreeScan -Path $Path -ExportFolders $true -ExportFiles:([bool]$IncludeFiles) `
        -MaxDepth $MaxDepth -SortBy 1 -Admin:$Admin
    try {
        ConvertFrom-WizTreeCsv -CsvPath $csv |
            Where-Object { $_.FullName -ne (Resolve-StorOpsPath -Path $Path) } |
            Sort-Object -Property SizeBytes -Descending |
            Select-Object -First $Top
    }
    finally {
        Remove-Item -LiteralPath $csv -Force -ErrorAction SilentlyContinue
    }
}

function Get-StorOpsPathSize {
    <#
        Look up one specific, already-known path's size by scanning its
        parent directory one level deep and picking out the matching row.
        Used by cleanup-plan.ps1/migrate-plan.ps1 to size a handful of
        specific candidate paths (derived from rules/*.yaml) without ever
        exporting a whole drive. Returns $null if the path doesn't exist or
        wasn't found in its parent's listing.
    #>
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

    $csv = Invoke-WizTreeScan -Path $parent -ExportFolders $true -ExportFiles $true -MaxDepth 1 -SortBy 1 -Admin:$Admin
    try {
        $rows = ConvertFrom-WizTreeCsv -CsvPath $csv
        return $rows | Where-Object { $_.FullName -eq $normalized } | Select-Object -First 1
    }
    finally {
        Remove-Item -LiteralPath $csv -Force -ErrorAction SilentlyContinue
    }
}

Export-ModuleMember -Function @(
    'Get-StorOpsWizTreePath',
    'Invoke-WizTreeScan',
    'ConvertFrom-WizTreeCsv',
    'Get-StorOpsTopEntries',
    'Get-StorOpsPathSize'
)
