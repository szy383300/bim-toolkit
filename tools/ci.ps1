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
#   1. interpreter / dependency contract    (tools/check_env.py)
#   2. extension self-test, strict mode     (selftest.py --strict, 8 stages)
#   3. DWG revamp classification fidelity   (tools/plan_eval.py --gate)
#   4. release artifact equals source       (tools/build_release.py --check-only)
#   5. repo-side unit tests                 (mcp/tests/test_pure.py)
#   6. IronPython 2.7 syntax + semantics    (tools/ipy_selfcheck.py, real ipy)
#   7. 4D player contract                   (mcp/play_4d.py --dry on a sample)
#
# NOTE: ASCII only. cmd.exe parses .bat in the OEM codepage (936 on CN
#       systems); UTF-8 Chinese in a .bat turns into garbage and swallows
#       the following quotes. PowerShell .ps1 on this box needs a UTF-8 BOM
#       to survive Chinese comments -- keeping this file ASCII sidesteps both.

$ErrorActionPreference = 'Continue'

$Repo = Split-Path -Parent $PSScriptRoot
$Py   = 'E:\AI-pyenvs\bim-dev\Scripts\python.exe'

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

if (-not (Test-Path $Py)) {
    Write-Host ("[CI] FATAL: python not found: " + $Py)
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

Step 'DWG revamp classification fidelity (tools/plan_eval.py --gate)' {
    # Synthetic ground truth: we generate a DXF whose correct answer we know,
    # run DWG->DXF->JSON->plan, and join on entity handle to get real
    # precision/recall. The old "coverage %" proxy was not gateable (most
    # unmatched entities are plants/title blocks that must NOT be built), and
    # hand-modelled baselines were deleted, so nothing measured this chain.
    # Gate shape: control scenarios must be perfect; the KNOWN_DEFECTS ledger
    # must still reproduce (so a real fix cannot leave a stale entry behind).
    & $Py (Join-Path $Repo 'tools\plan_eval.py') --gate
}

Step 'release artifact equals source (tools/build_release.py --check-only)' {
    # Guards the failure mode we actually hit: bimtoolkit-release was a manual
    # snapshot that silently lost 6 lib modules plus the whole "build-config"
    # button, and shipped bridge 7.10.4 (no COMMAND_TIERS = no policy tiers)
    # with allow_execute_code=true. This gate makes that state non-green.
    & $Py (Join-Path $Repo 'tools\build_release.py') --check-only
}

Step 'repo-side unit tests (mcp/tests/test_pure.py)' {
    # 31 real assertions on spec/bridge/engine -- previously written but not
    # wired into ANY gate, so they proved nothing.
    Push-Location (Join-Path $Repo 'mcp')
    try { & $Py -m unittest tests.test_pure } finally { Pop-Location }
}

# --- IronPython 2.7 gate -------------------------------------------------
# The repo's "stage 4: IronPython 2.7 real run" actually execs scripts on
# CPython 3.13, so it cannot see Py2-incompatible syntax OR Py2 integer-
# division semantics. This gate runs the same checks under a real ipy.exe.
# Missing interpreter is a FAIL, not a skip: a silently-skipped gate is how
# the false-green problem reproduces itself.
$Ipy = $null
foreach ($cand in @(
    "${env:ProgramFiles(x86)}\IronPython 2.7\ipy.exe",
    "$env:ProgramFiles\IronPython 2.7\ipy.exe")) {
    if (Test-Path -LiteralPath $cand) { $Ipy = $cand; break }
}
if ($env:BIMTOOLKIT_IPY -and (Test-Path -LiteralPath $env:BIMTOOLKIT_IPY)) {
    $Ipy = $env:BIMTOOLKIT_IPY
}

if (-not $Ipy) {
    Write-Host ''
    Write-Host '[CI] FAIL -> ironpython 2.7 syntax/semantics gate'
    Write-Host '[CI]   ipy.exe not found. Install IronPython 2.7 or set'
    Write-Host '[CI]   $env:BIMTOOLKIT_IPY to its path. Not skipping:'
    Write-Host '[CI]   a skipped gate proves nothing.'
    $script:Failed++
} else {
    Step 'ironpython 2.7 syntax + division semantics (tools/ipy_selfcheck.py)' {
        & $Ipy (Join-Path $Repo 'tools\ipy_selfcheck.py')
    }
    # Compile-clean does NOT mean semantics-identical: lookbehind, named groups,
    # unicode char classes and str.isdigit() are .NET-regex territory. This step
    # runs the floor-number parser itself on ipy and compares against the
    # CPython result (both are BAD=0 today; the CPython side lives in
    # tools/plan_eval.py --gate, level cases).
    Step 'ironpython 2.7 floor-number parser semantics (tools/ipy_floor_parser.py)' {
        & $Ipy (Join-Path $Repo 'tools\ipy_floor_parser.py')
    }
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
