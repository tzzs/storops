#requires -Version 5.1
<#
    StorOps shared helpers: path normalization, byte formatting, the
    per-user working directory (temp CSVs, generated plans), and small
    environment probes used by every other module/script.
#>

Set-StrictMode -Version Latest

function Get-StorOpsWorkDir {
    <#
        Per-user scratch directory for temporary WizTree CSV exports and
        generated plan files. Never used to store anything StorOps itself
        needs to survive across runs except plan files the user explicitly
        keeps around to hand to *-execute.ps1.
    #>
    [CmdletBinding()]
    param()

    $dir = Join-Path $env:LOCALAPPDATA 'StorOps'
    if (-not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
    return $dir
}

function New-StorOpsTempFile {
    [CmdletBinding()]
    param(
        [string]$Extension = '.csv',
        [string]$Prefix = 'storops-'
    )

    $dir = Join-Path (Get-StorOpsWorkDir) 'tmp'
    if (-not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
    $name = "{0}{1}{2}" -f $Prefix, [Guid]::NewGuid().ToString('N'), $Extension
    return Join-Path $dir $name
}

function Resolve-StorOpsPath {
    <#
        Normalize a user-supplied path to a full path with a trailing
        backslash trimmed (except for a bare drive root, e.g. "C:\"),
        without requiring the path to exist. Used so rule matching and
        display are consistent regardless of how the caller typed a path.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$Path
    )

    $full = [System.IO.Path]::GetFullPath($Path)
    if ($full.Length -gt 3 -and $full.EndsWith('\')) {
        $full = $full.TrimEnd('\')
    }
    return $full
}

function Format-StorOpsSize {
    <#
        Human-readable byte size, e.g. 93763223245 -> "87.32 GB".
        Deliberately simple (binary/1024-based) since that is what WizTree
        itself reports.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [double]$Bytes
    )

    $units = 'B', 'KB', 'MB', 'GB', 'TB', 'PB'
    $value = [double]$Bytes
    $unitIndex = 0
    while ([Math]::Abs($value) -ge 1024 -and $unitIndex -lt $units.Length - 1) {
        $value = $value / 1024
        $unitIndex++
    }
    if ($unitIndex -eq 0) {
        return "{0} {1}" -f [math]::Round($value, 0), $units[$unitIndex]
    }
    return "{0:N2} {1}" -f $value, $units[$unitIndex]
}

function Test-StorOpsIsAdmin {
    [CmdletBinding()]
    param()

    if ($IsLinux -or $IsMacOS) { return $false }
    $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object System.Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-StorOpsFreeSpaceInfo {
    <#
        Total/used/free capacity for a drive letter, via CIM rather than
        WizTree — StorOps does not depend on WizTree for basic drive
        capacity, only for the directory/file breakdown.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$DriveLetter
    )

    $letter = $DriveLetter.TrimEnd(':', '\')
    $disk = Get-CimInstance -ClassName Win32_LogicalDisk -Filter "DeviceID='$letter`:'" -ErrorAction Stop
    if (-not $disk) {
        throw "StorOps: drive '$DriveLetter' was not found."
    }

    [PSCustomObject]@{
        Drive        = "$letter`:"
        TotalBytes   = [int64]$disk.Size
        FreeBytes    = [int64]$disk.FreeSpace
        UsedBytes    = [int64]($disk.Size - $disk.FreeSpace)
        VolumeName   = $disk.VolumeName
        FileSystem   = $disk.FileSystem
    }
}

Export-ModuleMember -Function @(
    'Get-StorOpsWorkDir',
    'New-StorOpsTempFile',
    'Resolve-StorOpsPath',
    'Format-StorOpsSize',
    'Test-StorOpsIsAdmin',
    'Get-StorOpsFreeSpaceInfo'
)
