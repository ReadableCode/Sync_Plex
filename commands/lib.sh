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
    # the way. It needs a terminal, so inside cmdr's TUI (stdin is the null
    # device there) this fails with a pointer instead of hanging on a pipe.
    if [ ! -t 0 ]; then
        echo "media_remote needs a terminal: run 'cmdr syncplex' from a shell"
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

# Ask for the drive's media root. cmdr hands a step no arguments, so the
# path comes from stdin; the default is the same ~/Media the syncdrive shell
# function uses. `read -p` prompts on stderr, so stdout carries only the
# answer. A missing directory is a refusal, never a fallback: an unmounted
# drive must not turn into a sync onto the internal disk.
_drive_path() {
    local default="$HOME/Media" path
    if ! read -r -p "media path to sync [$default]: " path; then
        echo "drive_sync needs a terminal to ask for the path: run 'cmdr syncdrive' from a shell" >&2
        return 1
    fi
    path="${path:-$default}"
    if [ ! -d "$path" ]; then
        echo "$path is not a directory (drive not mounted?)" >&2
        return 1
    fi
    printf '%s\n' "$path"
}

drive_sync() {
    local path
    path=$(_drive_path) || return 1
    # No --yes: the scraper's own plan-and-confirm is the confirmation that
    # matters (what it will download and delete), and it only exists once
    # the path is known.
    _syncplex syncplex-drive-sync "$path"
}

drive_sync_check() {
    local path
    path=$(_drive_path) || return 1
    _syncplex syncplex-drive-sync "$path" --check
}
