#requires -Version 5.1
<#
    StorOps shared helpers: platform detection, path normalization, byte
    formatting, the per-user working directory (temp exports, generated
    plans), and small environment probes used by every other module/script.
#>

Set-StrictMode -Version Latest

function Get-StorOpsPlatform {
    <#
        'Windows' | 'Linux' | 'MacOS'. $IsWindows/$IsLinux/$IsMacOS are only
        defined as automatic variables on PowerShell 6+ -- Windows
        PowerShell 5.1 (which this module still supports, per #requires)
        never runs anywhere but Windows, so their absence itself means
        Windows. Reading them via Get-Variable -ErrorAction SilentlyContinue
        avoids a Set-StrictMode failure on 5.1, where referencing an
        undefined variable directly ($IsLinux) throws.
    #>
    [CmdletBinding()]
    param()

    $isLinuxVar = Get-Variable -Name IsLinux -Scope Global -ErrorAction SilentlyContinue
    if ($isLinuxVar -and $isLinuxVar.Value) { return 'Linux' }
    $isMacVar = Get-Variable -Name IsMacOS -Scope Global -ErrorAction SilentlyContinue
    if ($isMacVar -and $isMacVar.Value) { return 'MacOS' }
    return 'Windows'
}

function Get-StorOpsWorkDir {
    <#
        Per-user scratch directory for temporary scan exports and generated
        plan files. Never used to store anything StorOps itself needs to
        survive across runs except plan files the user explicitly keeps
        around to hand to *-execute.ps1.
    #>
    [CmdletBinding()]
    param()

    switch (Get-StorOpsPlatform) {
        'Windows' { $dir = Join-Path $env:LOCALAPPDATA 'StorOps' }
        'MacOS'   { $dir = Join-Path $HOME 'Library/Application Support/StorOps' }
        default {
            $xdgData = if ($env:XDG_DATA_HOME) { $env:XDG_DATA_HOME } else { Join-Path $HOME '.local/share' }
            $dir = Join-Path $xdgData 'storops'
        }
    }

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
        separator trimmed (except for a bare volume root -- "C:\" on
        Windows, "/" on Linux/macOS), without requiring the path to exist.
        Used so rule matching and display are consistent regardless of how
        the caller typed a path, on any platform.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$Path
    )

    $full = [System.IO.Path]::GetFullPath($Path)
    $sep = [System.IO.Path]::DirectorySeparatorChar
    $root = [System.IO.Path]::GetPathRoot($full)
    if ($full.Length -gt $root.Length -and $full.EndsWith($sep)) {
        $full = $full.TrimEnd($sep)
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
    <#
        Windows: local Administrator check (unchanged). Linux/macOS: root
        check via `id -u` -- root is enough to bypass most Permission Denied
        noise during a scan, but StorOps never requires it and never
        self-elevates (unlike WizTree's /admin=1, gdu/du need no elevation
        to be useful for the vast majority of a user's own home directory).
    #>
    [CmdletBinding()]
    param()

    if ((Get-StorOpsPlatform) -ne 'Windows') {
        $uid = & id -u 2>$null
        return ($uid -eq '0')
    }

    $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object System.Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-StorOpsFreeSpaceInfo {
    <#
        Total/used/free capacity for the volume/filesystem containing $Path.
        On Windows this takes a drive letter (e.g. "C" or "C:\", the -Path
        parameter keeps the old -DriveLetter name as an alias) and uses CIM,
        matching the original behavior. On Linux/macOS it takes any path and
        shells out to `df` for the filesystem that path lives on -- StorOps
        does not depend on a scan backend for basic capacity info.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [Alias('DriveLetter')]
        [string]$Path
    )

    if ((Get-StorOpsPlatform) -eq 'Windows') {
        $letter = $Path.Substring(0, 1)
        $disk = Get-CimInstance -ClassName Win32_LogicalDisk -Filter "DeviceID='$letter`:'" -ErrorAction Stop
        if (-not $disk) {
            throw "StorOps: drive '$Path' was not found."
        }
        return [PSCustomObject]@{
            Drive      = "$letter`:"
            TotalBytes = [int64]$disk.Size
            FreeBytes  = [int64]$disk.FreeSpace
            UsedBytes  = [int64]($disk.Size - $disk.FreeSpace)
            VolumeName = $disk.VolumeName
            FileSystem = $disk.FileSystem
        }
    }

    # `df -Pk -- <path>`: -P forces single-line POSIX output (GNU df can
    # otherwise wrap a long device/source column across two lines), -k
    # reports 1024-byte blocks so parsing is stable across GNU and BSD/macOS
    # df without needing to detect which flavor is installed.
    $lines = @(& df -Pk -- $Path 2>$null)
    if ($LASTEXITCODE -ne 0 -or $lines.Count -lt 2) {
        throw "StorOps: could not read filesystem capacity for '$Path' via df."
    }
    $fields = @($lines[1] -split '\s+')
    return [PSCustomObject]@{
        Drive      = $fields[0]
        TotalBytes = [int64]$fields[1] * 1024
        FreeBytes  = [int64]$fields[3] * 1024
        UsedBytes  = [int64]$fields[2] * 1024
        VolumeName = $fields[0]
        FileSystem = $null
    }
}

Export-ModuleMember -Function @(
    'Get-StorOpsPlatform',
    'Get-StorOpsWorkDir',
    'New-StorOpsTempFile',
    'Resolve-StorOpsPath',
    'Format-StorOpsSize',
    'Test-StorOpsIsAdmin',
    'Get-StorOpsFreeSpaceInfo'
)
