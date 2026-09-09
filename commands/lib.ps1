# Step functions for Sync_Plex commands (windows). Dot-sourced by cmdr.
# Same contract as lib.sh: <step> applies, <step>_check is read-only and
# exits nonzero on drift. cmdr sets $env:CMDR_REPO_DIR (this checkout).
# Every step runs from the repo root on purpose: the drive scraper finds
# .env by searching upward from the current directory.

function Invoke-Syncplex {
    Set-Location $env:CMDR_REPO_DIR
    & uv run --project backends\python @args
    return $LASTEXITCODE
}

# --- syncplex (the media remote) ---

function media_remote {
    # The remote is its own Textual TUI: cmdr launches it and gets out of
    # the way. The .cmd marks the step terminal, so cmdr's TUI hands the
    # screen over; any caller that pipes gets a pointer instead of a hang.
    if ([Console]::IsInputRedirected) {
        Write-Host "media_remote needs a terminal (stdin was not one)"
        exit 1
    }
    exit (Invoke-Syncplex syncplex tui)
}

function media_remote_check {
    # Config probe: `syncplex instances --json` lists every configured
    # service plus a warning per service whose key or host did not resolve.
    Set-Location $env:CMDR_REPO_DIR
    $json = & uv run --project backends\python syncplex instances --json | Out-String
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    Write-Host $json
    # Zero instances is drift too: the inventory did not resolve at all.
    if ($json -notmatch '"name":') {
        Write-Host "no instances configured: the inventory (hosts json) did not resolve"
        exit 1
    }
    if ($json -match '"warnings": \[\]') {
        Write-Host "every instance resolved its key and host"
        exit 0
    }
    Write-Host "config warnings above: an instance is missing its key or host"
    exit 1
}

# --- syncdrive (drive sync) ---

# cmdr hands a step no arguments, so the scraper gets no path and opens its
# folder browser (ncdu-style: enter opens, backspace up, space picks) on the
# console - the .cmd marks the step terminal so cmdr's TUI hands the screen
# over. The scraper then prints its own plan and asks before touching files,
# so cmdr's --yes never reaches it.
function Test-DriveSyncTerminal {
    if ([Console]::IsInputRedirected -or [Console]::IsOutputRedirected) {
        Write-Host "drive_sync needs a terminal for the folder browser (stdin or stdout was not one)"
        return $false
    }
    return $true
}

function drive_sync {
    if (-not (Test-DriveSyncTerminal)) { exit 1 }
    exit (Invoke-Syncplex syncplex-drive-sync)
}

function drive_sync_check {
    if (-not (Test-DriveSyncTerminal)) { exit 1 }
    exit (Invoke-Syncplex syncplex-drive-sync --check)
}
