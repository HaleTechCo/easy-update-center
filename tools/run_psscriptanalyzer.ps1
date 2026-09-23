# pre-commit wrapper: lint staged .ps1 files with PSScriptAnalyzer (errors only).
#
# Invoked by the `psscriptanalyzer` hook in .pre-commit-config.yaml; pre-commit
# appends the staged .ps1 paths as trailing arguments. Runs at -Severity Error: only real
# breakage gates a commit. Tighten later with a PSScriptAnalyzerSettings.psd1 if desired.
#
# Requires the module (one-time, not a pip dep):
#   Install-Module -Name PSScriptAnalyzer -Scope CurrentUser
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Files
)

if (-not $Files) { exit 0 }

if (-not (Get-Module -ListAvailable -Name PSScriptAnalyzer)) {
    # Not every environment has the module - skip (exit 0) rather than block the
    # commit. Install it to enable linting: Install-Module -Name PSScriptAnalyzer -Scope CurrentUser
    Write-Warning 'PSScriptAnalyzer not installed; skipping .ps1 lint. Install with: Install-Module -Name PSScriptAnalyzer -Scope CurrentUser'
    exit 0
}

Import-Module PSScriptAnalyzer

# One file per call. Invoke-ScriptAnalyzer's -Path is [string], NOT [string[]]: passing the
# whole array makes PowerShell fail the String[] -> String conversion with a NON-terminating
# error, leaving $results null and the script falling through to `exit 0`. That renders the
# hook permanently green while linting nothing, and pre-commit hides the error text because
# the hook "passed". -ErrorAction Stop turns any remaining analyzer failure into a real one.
#
# ParseError is listed alongside Error on purpose: a file that does not even parse reports
# severity ParseError, which `-Severity Error` alone silently filters out - so a syntax
# error would sail through as clean.
$results = foreach ($file in $Files) {
    Invoke-ScriptAnalyzer -Path $file -Severity Error, ParseError -ErrorAction Stop
}

if ($results) {
    $results | Format-Table -AutoSize | Out-String | Write-Output
    exit 1
}
exit 0
