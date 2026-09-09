# The media remote as a cmdr command. The remote is Sync_Plex's own Textual
# TUI; cmdr only launches it, so this runs from `cmdr syncplex` in a shell
# and refuses (fast, with a message) inside cmdr's own TUI, which pipes step
# output into a viewport and hands steps no terminal. The check is the
# config probe: every service in hosts.json must resolve its key and host.
description: media remote - open the sonarr/radarr/plex tui
order: 200
platforms: darwin linux windows
steps:
  media_remote requires=uv
