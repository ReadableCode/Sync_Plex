# Drive sync as a cmdr command. cmdr passes no arguments to a command, so
# the step asks for the drive's media root on stdin (default ~/Media, the
# same default the syncdrive shell function uses) and runs the scraper from
# the repo root, which is where it finds .env. The step is marked terminal
# so that prompt works from cmdr's TUI too (it hands the screen over). The
# scraper prints its own plan and asks before touching files, so cmdr's
# --yes never reaches it.
# Check is the scraper's --check: plan only, exit 1 when the drive is behind
# its config.
description: drive sync - mirror configured media onto a drive (asks for the path)
order: 210
platforms: darwin linux windows
steps:
  drive_sync requires=uv terminal
