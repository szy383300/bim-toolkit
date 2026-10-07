# ci.ps1 -- BIMToolkit local CI gate (ASCII ONLY on purpose).
#
# WHY LOCAL, NOT GITHUB ACTIONS:
#   This repo has NO git remote (`git remote -v` is empty), so a hosted CI
#   cannot run. This script is the honest substitute: one command that runs
#   every gate we actually have, with a single non-zero exit on any failure.
#   When a remote is added, wire this script into the CI job instead of
#   duplicating the gate definitions.
#
# Gates (in order):
#   1. interpreter / dependency contract  (tools/check_env.py)
#   2. extension self-test, strict mode   (selftest.py --strict, 8 stages)
#   3. 4D player contract                 (mcp/play_4d.py --dry on a sample)
#
# NOTE: ASCII only. cmd.exe parses .bat in the OEM codepage (936 on CN
#       systems); UTF-8 Chinese in a .bat turns into garbage and swallows
#       the following quotes. PowerShell .ps1 on this box needs a UTF-8 BOM
#       to survive Chinese comments -- keeping this file ASCII sidesteps both.

$ErrorActionPreference = 'Continue'

$Repo = Split-Path -Parent $PSScriptRoot

# Interpreter: BIMTOOLKIT_PYTHON wins, else python on PATH. This used to be pinned to one
# machine's venv, which made the gate unrunnable anywhere else -- including on this repo's own
# next checkout. Point it at a CPython that has PyYAML + ezdxf.
$Py = $env:BIMTOOLKIT_PYTHON
if (-not $Py) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { $Py = $cmd.Source }
}

$script:Failed = 0

function Step($name, [scriptblock]$body) {
    Write-Host ''
    Write-Host ('=' * 66)
    Write-Host ("[CI] " + $name)
    Write-Host ('=' * 66)
    & $body
    if ($LASTEXITCODE -ne 0) {
        Write-Host ("[CI] FAIL -> " + $name + " (exit " + $LASTEXITCODE + ")")
        $script:Failed++
    } else {
        Write-Host ("[CI] OK   -> " + $name)
    }
}

if (-not $Py -or -not (Test-Path $Py)) {
    Write-Host '[CI] FATAL: no usable python -- set BIMTOOLKIT_PYTHON, or put python on PATH'
    exit 9
}
Write-Host ("[CI] repo: " + $Repo)
Write-Host ("[CI] python: " + $Py)

Step 'interpreter / dependency contract (tools/check_env.py)' {
    & $Py (Join-Path $Repo 'tools\check_env.py')
}

Step 'extension self-test --strict (8 stages)' {
    & $Py (Join-Path $Repo 'pyrevit-bim-panel\selftest.py') --strict
}

Step '4D player contract (mcp/play_4d.py --dry)' {
    # Build a tiny steps tree on the fly, then only list it (no Revit needed).
    $tmp = Join-Path $env:TEMP 'ci_4d_steps.json'
    $json = '{"task":"ci","steps":[{"n":1,"title":"all","show_all":true},' +
            '{"n":2,"title":"f1","levels":["1F"]}]}'
    Set-Content -LiteralPath $tmp -Value $json -Encoding UTF8
    & $Py (Join-Path $Repo 'mcp\play_4d.py') $tmp --dry
    Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
}

Write-Host ''
Write-Host ('=' * 66)
if ($script:Failed -gt 0) {
    Write-Host ("[CI] FAIL -- " + $script:Failed + " gate(s) failed")
} else {
    Write-Host '[CI] PASS -- all gates green'
}
Write-Host ('=' * 66)
if ($script:Failed -gt 0) { exit 1 }
exit 0
