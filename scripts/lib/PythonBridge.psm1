#requires -Version 5.1
<#
    Compatibility-layer plumbing only (docs/plans/storops-v2-cross-platform-
    refactor.md §2.10). Resolves a Python 3.11+ interpreter and shells out to
    `python -m storops`. Carries NO business logic of its own -- all rule
    matching, risk classification, planning, and execution now lives in
    src/storops/. This module exists purely so the scripts/*.ps1
    compatibility wrappers can stay a few lines each; it is not meant to be
    imported by anything other than those wrappers.
#>

function Get-StorOpsPythonExe {
    <#
        Resolves a Python interpreter the way the wrappers need it: try
        `python3` first, then `python`, and fail loudly (not silently) if
        neither is on PATH.
    #>
    [CmdletBinding()]
    param()

    $cmd = Get-Command -Name 'python3' -ErrorAction SilentlyContinue
    if (-not $cmd) { $cmd = Get-Command -Name 'python' -ErrorAction SilentlyContinue }
    if (-not $cmd) {
        throw 'StorOps: no Python interpreter found on PATH (tried "python3", then "python"). Install Python 3.11+ and make sure it is on PATH -- see README.md "Requirements".'
    }
    return $cmd.Source
}

function Invoke-StorOpsCli {
    <#
        Invokes `python -m storops <CliArgs>` on behalf of a scripts/*.ps1
        wrapper.

        - Sets $env:PYTHONPATH to include "<RepoRoot>/src" for the duration
          of the call (prepended, never clobbering a PYTHONPATH the caller
          already set) so `python -m storops` works even when this repo was
          never `pip install`-ed -- the common "clone straight into a
          skills directory" case (README.md "Manual" install).
        - Returns the Python process's stdout VERBATIM (already valid JSON
          in -Json mode -- callers must not re-wrap/re-parse it).
        - Relays stderr lines as PowerShell warnings (StorOps' own stdout/
          stderr discipline already puts both non-fatal warnings and fatal
          error text on stderr -- see src/storops/cli.py).
        - Leaves $LASTEXITCODE set to the Python process's real exit code;
          callers are expected to check it and `exit $LASTEXITCODE` on
          failure themselves, to propagate the new exit-code contract
          (docs/plans/storops-v2-cross-platform-refactor.md §2.7) faithfully.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$RepoRoot,

        [Parameter(Mandatory)]
        [string[]]$CliArgs
    )

    $python = Get-StorOpsPythonExe
    $srcDir = Join-Path $RepoRoot 'src'
    $sep = [IO.Path]::PathSeparator
    $previousPythonPath = $env:PYTHONPATH
    $env:PYTHONPATH = if ($previousPythonPath) { "$srcDir$sep$previousPythonPath" } else { $srcDir }

    $stderrFile = [System.IO.Path]::GetTempFileName()
    try {
        $stdout = & $python -m storops @CliArgs 2>$stderrFile
    }
    finally {
        $env:PYTHONPATH = $previousPythonPath
    }

    $stderrText = Get-Content -LiteralPath $stderrFile -Raw -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $stderrFile -ErrorAction SilentlyContinue

    if ($stderrText) {
        # Deliberately NOT Write-Warning: verified (2026-09-01, real
        # pwsh 7.4.6, `pwsh -File x.ps1 1>out 2>err`) that Write-Warning's
        # output lands on the process's actual STDOUT file descriptor in
        # this non-interactive host, not stderr -- which would silently
        # break the -Json stdout-purity contract every time a scan
        # backend prints a BackendAdvice warning (i.e. on nearly every
        # `du`-fallback run). [Console]::Error.WriteLine writes to the
        # real OS-level stderr regardless of how the process's streams
        # were redirected -- confirmed with the same test.
        ($stderrText -split "`r?`n") | Where-Object { $_ } | ForEach-Object { [Console]::Error.WriteLine($_) }
    }

    return $stdout
}

Export-ModuleMember -Function Get-StorOpsPythonExe, Invoke-StorOpsCli
