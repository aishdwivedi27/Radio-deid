# Run every local check (CLAUDE.md, Git and checks). Exits non-zero if any check fails.
# Usage (repo root):  .\tools\check_all.ps1 [-Phase 0] [-CI]
#   -Phase N : last phase built; the trace report fails for untested TRs of phases <= N
#   -CI      : skip the trace report (it reads git-ignored docs that CI does not have)
param(
    [int]$Phase = 0,
    [switch]$CI
)

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { $Python = "python" }

$Results = New-Object System.Collections.Generic.List[object]

function Invoke-Check {
    param([string]$Name, [scriptblock]$Block)
    Write-Host ""
    Write-Host "==> $Name" -ForegroundColor Cyan
    & $Block
    $code = $LASTEXITCODE
    $Results.Add([pscustomobject]@{ Check = $Name; Passed = ($code -eq 0) })
}

Invoke-Check "pytest (incl. PHI leak tests)" { & $Python -m pytest }
Invoke-Check "file size" { & $Python tools/check_file_size.py }
Invoke-Check "layer imports" { & $Python tools/check_layers.py }
Invoke-Check "ruff lint" { & $Python -m ruff check . }
Invoke-Check "ruff format" { & $Python -m ruff format --check . }
Invoke-Check "mypy" { & $Python -m mypy }
Invoke-Check "pylint max-module-lines" {
    & $Python -m pylint --disable=all --enable=too-many-lines --max-module-lines=1000 --score=n app tools tests
}
Invoke-Check "secret scan" { & $Python tools/check_secrets.py }
Invoke-Check "synthetic markers" { & $Python tools/check_synthetic_markers.py }
Invoke-Check "web lint" { npm --prefix web run lint }
Invoke-Check "web tests" { npm --prefix web test }
Invoke-Check "web build" { npm --prefix web run build }
if ($CI) {
    Write-Host ""
    Write-Host "==> trace report: skipped in CI (reads git-ignored docs)"
} else {
    Invoke-Check "trace report (phase $Phase)" { & $Python tools/trace_report.py --phase $Phase }
}

Write-Host ""
Write-Host "==== Summary ====" -ForegroundColor Cyan
$failed = 0
foreach ($r in $Results) {
    if ($r.Passed) { Write-Host ("PASS  " + $r.Check) -ForegroundColor Green }
    else { Write-Host ("FAIL  " + $r.Check) -ForegroundColor Red; $failed++ }
}
if ($failed -gt 0) {
    Write-Host "$failed check(s) failed." -ForegroundColor Red
    exit 1
}
Write-Host "All checks passed." -ForegroundColor Green
exit 0
