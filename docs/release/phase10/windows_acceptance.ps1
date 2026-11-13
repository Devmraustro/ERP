# Phase 10 — reproducible Windows desktop acceptance (PowerShell 5.1 / 7+)
#
# NOT EXECUTED in this project's environment: the sandbox is Linux without libGL and
# has no Windows host. This script exists so the acceptance can be run by someone who
# does, in one command, and produce machine-checkable evidence.
#
#   powershell -ExecutionPolicy Bypass -File docs\release\phase10\windows_acceptance.ps1
#
# It is deliberately strict: every step either passes or stops the script. A GUI check
# that cannot be automated is printed as a manual row and re-printed in the summary so
# it cannot be forgotten.

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
$WorkRoot = Join-Path $env:TEMP ("dz-erp-acceptance-" + (Get-Date -Format "yyyyMMddTHHmmss"))
$Venv = Join-Path $WorkRoot "venv"
$Report = Join-Path $WorkRoot "acceptance.json"
$Rows = New-Object System.Collections.ArrayList

function Record([string]$Row, [string]$Status, [string]$Detail) {
    $null = $Rows.Add([ordered]@{ row = $Row; status = $Status; detail = $Detail })
    Write-Host ("[{0,-13}] {1}: {2}" -f $Status, $Row, $Detail)
}

function Fail([string]$Row, [string]$Detail) {
    Record $Row "FAILED" $Detail
    Write-Host ""
    Write-Host "Acceptance STOPPED. Evidence: $Report"
    Save-Report
    exit 1
}

function Save-Report() {
    $summary = [ordered]@{
        generated_at = (Get-Date).ToUniversalTime().ToString("o")
        host_os      = [System.Environment]::OSVersion.VersionString
        python       = (& $Venv\Scripts\python.exe -V 2>&1)
        rows         = $Rows
    }
    $summary | ConvertTo-Json -Depth 6 | Set-Content -Path $Report -Encoding UTF8
}

New-Item -ItemType Directory -Path $WorkRoot -Force | Out-Null
Write-Host "work root: $WorkRoot"

# --- 1. interpreter: CPython 3.12.10 is the supported baseline
$py = Get-Command py -ErrorAction SilentlyContinue
if (-not $py) { Fail "python launcher" "py.exe not found: install CPython 3.12.10" }
& py -3.12 -c "import sys; assert sys.version_info[:2]==(3,12), sys.version; print(sys.version)" |
    ForEach-Object { Record "interpreter 3.12" "EXECUTED" $_ }
if ($LASTEXITCODE -ne 0) { Fail "interpreter 3.12" "CPython 3.12 is required (V1 baseline is 3.12.10)" }

# --- 2. isolated environment, hash-pinned dependencies
& py -3.12 -m venv $Venv
& $Venv\Scripts\python.exe -m pip install --upgrade pip --quiet
& $Venv\Scripts\python.exe -m pip install --no-deps --require-hashes -r (Join-Path $RepoRoot "requirements.lock.hashes.txt") --quiet
if ($LASTEXITCODE -ne 0) { Fail "locked dependencies" "hash-pinned install failed" }
Record "locked dependencies" "EXECUTED" "requirements.lock.hashes.txt installed with --require-hashes"

# PySide6 is an optional extra; pin matches pyproject.toml [project.optional-dependencies].desktop
& $Venv\Scripts\python.exe -m pip install "PySide6==6.11.2" --quiet
if ($LASTEXITCODE -ne 0) { Fail "PySide6" "install failed" }
Record "PySide6" "EXECUTED" "6.11.2 imported: $(& $Venv\Scripts\python.exe -c 'import PySide6; print(PySide6.__version__)')"

# --- 3. the desktop test modules (these skip on Linux without libGL)
Push-Location $RepoRoot
& $Venv\Scripts\python.exe -m pytest tests\test_phase26_desktop.py tests\test_phase27_desktop_ui.py tests\test_phase28_desktop_users_ui.py -q
$pytestExit = $LASTEXITCODE
Pop-Location
if ($pytestExit -ne 0) { Fail "desktop pytest modules" "exit $pytestExit" }
Record "desktop pytest modules" "EXECUTED" "phase26/27/28 green with a real Qt"

# --- 4. headless start -> login -> read -> stop (the CI proxy for "it starts")
Push-Location $RepoRoot
$smoke = & $Venv\Scripts\python.exe -m dz_erp.desktop --smoke 2>&1
$smokeExit = $LASTEXITCODE
Pop-Location
if ($smokeExit -ne 0) { Fail "headless smoke" $smoke }
Record "headless smoke (start/login/read/stop)" "EXECUTED" "dz_erp.desktop --smoke exit 0"

# --- 5. the real window, offscreen, so a GUI construct error is caught without a desktop
$env:QT_QPA_PLATFORM = "offscreen"
Push-Location $RepoRoot
& $Venv\Scripts\python.exe -m pytest tests\test_phase26_desktop.py -q -k "mainwindow or rtl"
$guiExit = $LASTEXITCODE
Pop-Location
Remove-Item Env:\QT_QPA_PLATFORM
if ($guiExit -ne 0) { Fail "window construction (offscreen)" "exit $guiExit" }
Record "window construction (offscreen)" "EXECUTED" "QMainWindow built and switched to RTL"

# --- 6. manual rows: a human must look at the screen
$manual = @(
    "Arabic RTL rendering: labels right-aligned, glyphs shaped, nothing clipped",
    "French <-> Arabic switching in the running window (all strings change)",
    "session expiry: after expires_at the next action re-prompts for login",
    "role-based navigation: clerk cannot see accountant-only actions",
    "invoice intake through the UI (PDF and a phone photo)",
    "OCR correction in the review screen, original still viewable",
    "invoice posting from the UI (approval + posting, bank transfer)",
    "error surfacing: server down -> actionable message, no traceback",
    "English language: NOT IMPLEMENTED in V1 (available_languages() == ('fr','ar'))"
)
foreach ($row in $manual) { Record $row "MANUAL" "requires a human at the keyboard" }

Save-Report
$failed = ($Rows | Where-Object { $_.status -eq "FAILED" }).Count
$manualCount = ($Rows | Where-Object { $_.status -eq "MANUAL" }).Count
Write-Host ""
Write-Host ("automated rows passed; {0} manual rows remain. Evidence: {1}" -f $manualCount, $Report)
exit $(if ($failed -gt 0) { 1 } else { 0 })
