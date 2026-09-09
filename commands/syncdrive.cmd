# Drive sync as a cmdr command. cmdr passes no arguments to a command, so
# the scraper gets no path and opens its folder browser (ncdu-style) to pick
# the drive's media root; it runs from the repo root, which is where it finds
# .env. The step is marked terminal so the browser works from cmdr's TUI too
# (it hands the screen over). The scraper prints its own plan and asks before
# touching files, so cmdr's --yes never reaches it.
# Check is the scraper's --check: plan only, exit 1 when the drive is behind
# its config.
description: drive sync - mirror configured media onto a drive (browse for the folder)
order: 210
platforms: darwin linux windows
steps:
  drive_sync requires=uv terminal
