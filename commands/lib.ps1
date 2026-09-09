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
    # the way. Inside cmdr's TUI there is no terminal, so fail with a
    # pointer instead of hanging on a pipe.
    if ([Console]::IsInputRedirected) {
        Write-Host "media_remote needs a terminal: run 'cmdr syncplex' from a shell"
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

# Ask for the drive's media root. cmdr hands a step no arguments, so the
# path comes from the console; the default is the same ~\Media the
# syncdrive shell function uses. A missing directory is a refusal, never a
# fallback: an unmounted drive must not turn into a sync onto the internal
# disk.
function Get-DrivePath {
    $default = Join-Path $HOME 'Media'
    if ([Console]::IsInputRedirected) {
        Write-Host "drive_sync needs a terminal to ask for the path: run 'cmdr syncdrive' from a shell"
        return $null
    }
    $path = Read-Host "media path to sync [$default]"
    if ([string]::IsNullOrWhiteSpace($path)) { $path = $default }
    if (-not (Test-Path -PathType Container $path)) {
        Write-Host "$path is not a directory (drive not mounted?)"
        return $null
    }
    return $path
}

function drive_sync {
    $path = Get-DrivePath
    if (-not $path) { exit 1 }
    # No --yes: the scraper's own plan-and-confirm is the confirmation that
    # matters, and it only exists once the path is known.
    exit (Invoke-Syncplex syncplex-drive-sync $path)
}

function drive_sync_check {
    $path = Get-DrivePath
    if (-not $path) { exit 1 }
    exit (Invoke-Syncplex syncplex-drive-sync $path --check)
}
