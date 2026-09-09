# The media remote as a cmdr command. The remote is Sync_Plex's own Textual
# TUI; cmdr only launches it. The step is marked terminal, so cmdr's TUI
# hands the screen over (suspend, run, resume) instead of piping into its
# viewport, and the lib's own no-terminal refusal only fires for callers that
# pipe. The check is the config probe: every service in the inventory must
# resolve its key and host.
description: media remote - open the sonarr/radarr/plex tui
order: 200
platforms: darwin linux windows
steps:
  media_remote requires=uv terminal
