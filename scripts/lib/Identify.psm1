#requires -Version 5.1
<#
    StorOps' rule engine: loads the flat YAML rule files under rules/ and
    matches a filesystem path against them, so the agent never has to guess
    what a path is from its name (docs/DESIGN.md §3.3).

    Read-StorOpsRuleFile / ConvertTo-StorOpsRuleObject / ConvertTo-StorOpsScalar
    below are a small reader for the specific YAML subset documented in
    rules/README.md -- NOT a general-purpose YAML parser. Do not extend the
    rule files beyond that subset without extending this reader to match.
#>

Set-StrictMode -Version Latest

$script:StorOpsRuleCache = $null
$script:StorOpsRuleCacheDir = $null

function ConvertTo-StorOpsScalar {
    [CmdletBinding()]
    param([string]$Text)

    $t = $Text.Trim()
    if ($t.Length -eq 0) { return $null }

    if ($t.Length -ge 2 -and $t[0] -eq '"' -and $t[$t.Length - 1] -eq '"') {
        return $t.Substring(1, $t.Length - 2).Replace('\\', '\')
    }
    if ($t.Length -ge 2 -and $t[0] -eq "'" -and $t[$t.Length - 1] -eq "'") {
        return $t.Substring(1, $t.Length - 2).Replace("''", "'")
    }

    switch ($t.ToLowerInvariant()) {
        'true' { return $true }
        'false' { return $false }
        'null' { return $null }
        '~' { return $null }
        default {
            $num = 0.0
            if ([double]::TryParse($t, [System.Globalization.NumberStyles]::Float, [System.Globalization.CultureInfo]::InvariantCulture, [ref]$num)) {
                return $num
            }
            return $t
        }
    }
}

function ConvertTo-StorOpsRuleObject {
    <#
        Parse one item's already-extracted lines (each re-based so the
        field name starts at column 2, e.g. "  id: foo") into an object.
        Supports: scalar fields, one nested block sequence per field
        ("field:" then "    - value" lines), and folded block scalars
        ("field: >" then "    wrapped text" lines).
    #>
    [CmdletBinding()]
    param([string[]]$Lines)

    $obj = [ordered]@{}
    $i = 0
    while ($i -lt $Lines.Count) {
        $line = $Lines[$i]
        if ($line -notmatch '^  (\S[^:]*):\s?(.*)$') {
            throw "StorOps rule parser: could not parse field line: '$line'"
        }
        $key = $Matches[1].Trim()
        $rest = $Matches[2]
        $i++

        if ($rest -match '^>[-+]?$') {
            $textLines = New-Object System.Collections.Generic.List[string]
            while ($i -lt $Lines.Count -and $Lines[$i] -match '^    \S') {
                $textLines.Add($Lines[$i].Substring(4).TrimEnd())
                $i++
            }
            $obj[$key] = ($textLines -join ' ').Trim()
        }
        elseif ($rest -eq '') {
            $listItems = New-Object System.Collections.Generic.List[object]
            while ($i -lt $Lines.Count -and $Lines[$i] -match '^\s+-\s?(.*)$') {
                $listItems.Add((ConvertTo-StorOpsScalar $Matches[1]))
                $i++
            }
            $obj[$key] = $listItems.ToArray()
        }
        else {
            $obj[$key] = ConvertTo-StorOpsScalar $rest
        }
    }

    [PSCustomObject]$obj
}

function Read-StorOpsRuleFile {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$RulesPath
    )

    $lines = @(Get-Content -LiteralPath $RulesPath | Where-Object {
        $_ -notmatch '^\s*#' -and $_.Trim() -ne ''
    })

    $rules = New-Object System.Collections.Generic.List[object]
    $i = 0
    while ($i -lt $lines.Count) {
        if ($lines[$i] -notmatch '^-\s?(.*)$') {
            throw "StorOps rule parser ($RulesPath): expected a top-level '- ' item, got: '$($lines[$i])'"
        }
        $itemLines = New-Object System.Collections.Generic.List[string]
        $itemLines.Add('  ' + $Matches[1])
        $i++
        while ($i -lt $lines.Count -and $lines[$i] -match '^\s') {
            $itemLines.Add($lines[$i])
            $i++
        }
        $rules.Add((ConvertTo-StorOpsRuleObject -Lines $itemLines))
    }

    return $rules
}

function Get-StorOpsRules {
    <#
        Load and cache all rule files, in match precedence order: windows
        (critical short-circuit) -> ai-models -> applications -> caches.
    #>
    [CmdletBinding()]
    param(
        [string]$RulesDirectory,
        [switch]$Force
    )

    if (-not $RulesDirectory) {
        $RulesDirectory = Join-Path $PSScriptRoot '..\..\rules'
    }
    $RulesDirectory = (Resolve-Path -LiteralPath $RulesDirectory).Path

    if (-not $Force -and $script:StorOpsRuleCache -and $script:StorOpsRuleCacheDir -eq $RulesDirectory) {
        return $script:StorOpsRuleCache
    }

    # windows/linux/macos.yaml are always all loaded regardless of the
    # current platform: only the current platform's %TOKEN% set actually
    # expands (Expand-StorOpsPatternTokens), so the other two files' rules
    # simply never match anything real -- see docs/DESIGN.md §4c.
    $orderedFiles = 'windows.yaml', 'linux.yaml', 'macos.yaml', 'ai-models.yaml', 'applications.yaml', 'caches.yaml'
    $all = New-Object System.Collections.Generic.List[object]
    foreach ($file in $orderedFiles) {
        $path = Join-Path $RulesDirectory $file
        if (Test-Path -LiteralPath $path) {
            foreach ($rule in (Read-StorOpsRuleFile -RulesPath $path)) {
                $all.Add($rule)
            }
        }
    }

    $script:StorOpsRuleCache = $all
    $script:StorOpsRuleCacheDir = $RulesDirectory
    return $all
}

function Expand-StorOpsPatternTokens {
    <#
        Expand the %VAR% tokens documented in rules/README.md into their
        current environment values. Tokens are matched case-sensitively as
        written (always upper-case) -- author new rules accordingly. The
        token set is platform-dependent (docs/DESIGN.md §4c): a token from
        another platform's set is left un-expanded and simply never matches
        a real path, which is why Get-StorOpsRules loads windows/linux/
        macos.yaml unconditionally regardless of the current platform.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Pattern)

    switch (Get-StorOpsPlatform) {
        'Windows' {
            $tokens = [ordered]@{
                '%USERPROFILE%'       = $env:USERPROFILE
                '%LOCALAPPDATA%'      = $env:LOCALAPPDATA
                '%APPDATA%'           = $env:APPDATA
                '%PROGRAMDATA%'       = $env:ProgramData
                '%PROGRAMFILES%'      = $env:ProgramFiles
                '%PROGRAMFILES(X86)%' = ${env:ProgramFiles(x86)}
                '%TEMP%'              = $env:TEMP
                '%SYSTEMROOT%'        = $env:SystemRoot
            }
        }
        'MacOS' {
            $xdgCache = if ($env:XDG_CACHE_HOME) { $env:XDG_CACHE_HOME } else { Join-Path $HOME '.cache' }
            $tmpDir = if ($env:TMPDIR) { $env:TMPDIR } else { '/tmp' }
            $tokens = [ordered]@{
                '%HOME%'           = $HOME
                '%CACHES%'         = (Join-Path $HOME 'Library/Caches')
                '%APP_SUPPORT%'    = (Join-Path $HOME 'Library/Application Support')
                '%XDG_CACHE_HOME%' = $xdgCache
                '%TMPDIR%'         = $tmpDir
            }
        }
        default {
            $xdgCache = if ($env:XDG_CACHE_HOME) { $env:XDG_CACHE_HOME } else { Join-Path $HOME '.cache' }
            $xdgConfig = if ($env:XDG_CONFIG_HOME) { $env:XDG_CONFIG_HOME } else { Join-Path $HOME '.config' }
            $xdgData = if ($env:XDG_DATA_HOME) { $env:XDG_DATA_HOME } else { Join-Path $HOME '.local/share' }
            $tmpDir = if ($env:TMPDIR) { $env:TMPDIR } else { '/tmp' }
            $tokens = [ordered]@{
                '%HOME%'            = $HOME
                '%XDG_CACHE_HOME%'  = $xdgCache
                '%XDG_CONFIG_HOME%' = $xdgConfig
                '%XDG_DATA_HOME%'   = $xdgData
                '%TMPDIR%'          = $tmpDir
            }
        }
    }

    $result = $Pattern
    foreach ($token in $tokens.Keys) {
        $value = $tokens[$token]
        if ($value) { $result = $result.Replace($token, $value) }
    }
    return $result
}

function New-StorOpsIdentity {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][object]$Rule,
        [Parameter(Mandatory)][string]$Path,
        [string]$MatchedPattern
    )

    $names = $Rule.PSObject.Properties.Name
    $confidence = if ($names -contains 'confidence' -and $null -ne $Rule.confidence) { [double]$Rule.confidence } else { 0.5 }
    $deletable = if ($names -contains 'deletable') { [bool]$Rule.deletable } else { $false }
    $migratable = if ($names -contains 'migratable') { [bool]$Rule.migratable } else { $false }
    $owner = if ($names -contains 'owner' -and $Rule.owner) { $Rule.owner } else { 'user' }

    [PSCustomObject]@{
        Path              = $Path
        Application       = $Rule.application
        Category          = $Rule.category
        Confidence        = $confidence
        Owner             = $owner
        Purpose           = if ($names -contains 'purpose') { $Rule.purpose } else { $null }
        Deletable         = $deletable
        Migratable        = $migratable
        MigrationMethod   = if ($names -contains 'migration_method') { $Rule.migration_method } else { $null }
        MigrationHint     = if ($names -contains 'migration_config_hint') { $Rule.migration_config_hint } else { $null }
        RequiresAppClosed = if ($names -contains 'migration_requires_app_closed') { [bool]$Rule.migration_requires_app_closed } else { $false }
        CleanupRisk       = $Rule.cleanup_risk
        Consequence       = if ($names -contains 'cleanup_consequence') { $Rule.cleanup_consequence } else { $null }
        Notes             = if ($names -contains 'notes') { $Rule.notes } else { $null }
        MatchedRuleId     = $Rule.id
        MatchedPattern    = $MatchedPattern
    }
}

function Get-StorOpsPathIdentity {
    <#
        The core "identify" capability: classify a single path against the
        rule base. Returns an object with Application/Category/Confidence/
        Owner/Purpose/Deletable/Migratable/MigrationMethod/MigrationHint/
        CleanupRisk/Consequence -- never null-guesses a classification for
        an unmatched path; it comes back as Category "unknown" with
        CleanupRisk "critical" (StorOps never assumes an unknown path is
        safe to touch).
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$Path,

        [string]$RulesDirectory
    )

    $normalized = Resolve-StorOpsPath -Path $Path
    $rules = if ($RulesDirectory) { Get-StorOpsRules -RulesDirectory $RulesDirectory } else { Get-StorOpsRules }

    foreach ($rule in $rules) {
        foreach ($pattern in @($rule.path_patterns)) {
            $expanded = Expand-StorOpsPatternTokens -Pattern $pattern
            if ($normalized -like $expanded) {
                return New-StorOpsIdentity -Rule $rule -Path $normalized -MatchedPattern $pattern
            }
        }
    }

    [PSCustomObject]@{
        Path              = $normalized
        Application       = $null
        Category          = 'unknown'
        Confidence        = 0.0
        Owner             = $null
        Purpose           = $null
        Deletable         = $false
        Migratable        = $false
        MigrationMethod   = $null
        MigrationHint     = $null
        RequiresAppClosed = $false
        CleanupRisk       = 'critical'
        Consequence       = 'Unrecognized path: StorOps has no rule for it and treats it as not-safe-to-touch by default.'
        Notes             = $null
        MatchedRuleId     = $null
        MatchedPattern    = $null
    }
}

Export-ModuleMember -Function @(
    'Get-StorOpsRules',
    'Get-StorOpsPathIdentity',
    'Expand-StorOpsPatternTokens',
    'Read-StorOpsRuleFile'
)
