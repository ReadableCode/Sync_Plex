#!/usr/bin/env bash
# Step functions for Sync_Plex commands (darwin + linux). Sourced by cmdr,
# never executed directly; <step>_check is the read-only probe (exit nonzero
# = drift). cmdr exports CMDR_REPO_DIR (this checkout).
# Every step runs from the repo root on purpose: the drive scraper finds
# .env by searching upward from the current directory.

_syncplex() {
    (cd "$CMDR_REPO_DIR" && uv run --project backends/python "$@")
}

# --- syncplex (the media remote) ---

media_remote() {
    # The remote is its own Textual TUI: cmdr launches it and gets out of
    # the way. It needs a terminal (the .cmd marks the step terminal, so
    # cmdr's TUI hands the screen over); any caller that pipes gets a
    # pointer instead of a hang.
    if [ ! -t 0 ]; then
        echo "media_remote needs a terminal (stdin was not one)"
        return 1
    fi
    _syncplex syncplex tui
}

media_remote_check() {
    # Config probe: `syncplex instances --json` lists every configured
    # service plus a warning per service whose key or host did not resolve.
    # json.dumps renders an empty list as exactly `"warnings": []`.
    # Zero instances is drift too: the inventory did not resolve at all.
    local json
    json=$(_syncplex syncplex instances --json) || return 1
    printf '%s\n' "$json"
    if ! printf '%s' "$json" | grep -q '"name":'; then
        echo "no instances configured: the inventory (hosts json) did not resolve"
        return 1
    fi
    if printf '%s' "$json" | grep -q '"warnings": \[\]'; then
        echo "every instance resolved its key and host"
        return 0
    fi
    echo "config warnings above: an instance is missing its key or host"
    return 1
}

# --- syncdrive (drive sync) ---

# cmdr hands a step no arguments, so the drive-sync TUI gets no path and
# opens with its folder browser (ncdu-style: enter opens, backspace up, space
# picks); the .cmd marks the step terminal so cmdr's TUI hands the screen
# over. The TUI confirms before touching files, so cmdr's --yes never
# reaches it. The check is the headless --check, which needs the folder, so
# it asks for one first (default ~/Media, the syncdrive shell function's).
_drive_sync_needs_terminal() {
    if [ ! -t 0 ] || [ ! -t 1 ]; then
        echo "drive_sync needs a terminal (stdin or stdout was not one)" >&2
        return 1
    fi
}

drive_sync() {
    _drive_sync_needs_terminal || return 1
    _syncplex syncplex-drive-sync
}

drive_sync_check() {
    # Headless, so no terminal needed: the folder can be piped in.
    local default="$HOME/Media" path
    if ! read -r -p "drive media folder to check [$default]: " path; then
        echo "drive_sync_check needs the folder on stdin (nothing was there)" >&2
        return 1
    fi
    path="${path:-$default}"
    if [ ! -d "$path" ]; then
        echo "$path is not a directory (drive not mounted?)" >&2
        return 1
    fi
    _syncplex syncplex-drive-sync "$path" --check
}
