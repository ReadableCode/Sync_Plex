# Sync_Plex

The household media app. Two things, one project:

1. **Media remote** — search and add shows/movies across every Sonarr, Radarr,
   and Plex instance you run, from a CLI, a TUI, or a phone-friendly web UI.
2. **Drive sync** — mirror the shows/movies you pick onto an external drive
   (road trips, flights), pulling the files from the Plex server.

## Quick start

```bash
syncplex search "severance"        # status on every instance, merged
syncplex add "severance" --to sonarr-elitedesk
syncplex tui                       # full-screen remote (ctrl+s = drive sync)
syncdrive /Volumes/ExtSSD/Media    # mirror configured media onto a drive
```

(`syncplex` and `syncdrive` are shell functions from dotfiles that `uv run`
into this repo; from a bare clone use
`uv run --project backends/python syncplex ...`.)

## Commands

All commands are flat — no nested groups except `users`.

| Command | What it does |
| --- | --- |
| `syncplex search "title" [-t tv\|movie] [--plex]` | One merged status view across every instance |
| `syncplex seasons "title" [--episodes]` | Per-season / per-episode breakdown |
| `syncplex add "title" --to <instance>` | Add the top result to that instance |
| `syncplex instances` | List configured instances (from hosts.json + .env) |
| `syncplex tui` | Textual TUI: search/add, plus drive sync on `ctrl+s` |
| `syncplex web [--host IP] [--port 8788]` | The web UI (NiceGUI) |
| `syncplex users <add\|list\|passwd\|role\|disable\|enable\|remove>` | Web UI accounts |
| `syncplex-drive-sync <path> [--yes]` | Mirror configured media onto a drive |

Data commands take `--json` for scripting.

## How it's put together

One Python project at `backends/python`, two packages, no internal REST API —
every UI imports the same code in-process:

```plaintext
backends/python/
├── engine/            # media remote: inventory, per-service clients,
│   │                  #   status aggregation, request queue
│   ├── cli.py             # all the flat commands above
│   ├── media/tui/app.py   # the TUI
│   └── web/               # web UI + its login/accounts
├── drive_sync/        # drive sync: plex_api_wrapper.py + plex_scraper.py
└── tests/
```

Repo root: `cli/` shell wrappers, `deploy/compose.elitedesk.yaml` (web
deployment), `.env` → symlink into personal_credentials, `pyrightconfig.json`
(points editors at `backends/python/.venv`).

## Configuration

Two files, both living in the sibling `personal_credentials` repo:

- **`hosts.json`** — the inventory. Each host lists the services it offers;
  adding another Sonarr/Radarr/Plex is config-only:

  ```json
  {
    "hosts": [
      {
        "name": "behemoth",
        "hostname": "192.168.86.31",
        "services": [
          { "type": "sonarr", "name": "sonarr-behemoth", "port": 8989, "api_key_env": "SONARR_BEHEMOTH_API_KEY" },
          { "type": "plex", "name": "plex-behemoth", "port": 32400, "api_key_env": "PLEX_TOKEN" }
        ]
      }
    ]
  }
  ```

  Optional service fields: `scheme`, `base_url`, `quality_profile`,
  `root_folder`. Search order for the file: `$SYNCPLEX_HOSTS` →
  `../personal_credentials/hosts.json` → repo-root `hosts.json` →
  `~/.config/syncplex/hosts.json` → `~/syncplex_hosts.json`.

- **`.env`** — the secrets. The inventory never holds keys; each service
  names its env var (`api_key_env`). See `.env.example` for expected keys.

## Drive sync

Each drive carries its own `config.yaml` at its media root, listing the shows
(with how many next-unwatched episodes to keep) and movies it should hold:

```yaml
shows:
  - name: American Dad!
    num_next_episodes: 3
movies:
  - name: Zootopia
quality_profile_pref:
  - quality_profile: original
  - quality_profile: optimized for mobile
```

Run it with the drive's media path (it offers to create a starter config if
none exists):

```bash
syncdrive /Volumes/ExtSSD/Media        # shows the plan, asks before touching files
syncdrive /Volumes/ExtSSD/Media --yes  # skip the confirmation (what the TUI uses)
```

It compares what the drive has against what the config wants, then downloads
the missing files from the Plex server (SMB copy on Windows, rsync-over-SSH
on Linux, local copy on macOS) and deletes files under `TV/` and `Movies/`
that are no longer wanted. In the TUI, `ctrl+s` opens the same tool: type
the path, confirm, watch the output stream.

## Web UI

Self-contained login (argon2id, rate-limited, server-side sessions — no
Authelia needed); TLS comes from the reverse proxy in front.

Accounts live in `users.json` inside the app's data dir, which is resolved
**per process**: `$SYNCPLEX_DATA_DIR` if set, else `~/.config/syncplex`. The
deployed container sets `SYNCPLEX_DATA_DIR=/data` (a mounted host volume), so
**account commands for the deployed web UI must run inside the container** —
running them on the host writes to a different `users.json` the web UI never
reads:

```bash
# deployed instance (the normal case):
sudo docker exec -it syncplex_web syncplex users add jason --role admin
sudo docker exec -it syncplex_web syncplex users add friendname   # default role: user

# only for a locally-run `syncplex web` on this machine:
syncplex users add jason --role admin
```

The first account must be created this way — with zero accounts nobody can
log in. Admins add titles directly and work the approval queue at
`/requests`; users can search everything but only *request* — nothing
downloads until an admin approves and picks the server. Password
changes/disables kill sessions immediately. Set `SYNCPLEX_SESSION_SECRET` so
sessions survive restarts.

## Deployment

The web UI deploys as one container behind SWAG via
`deploy/compose.elitedesk.yaml`, pulled into `Docker/docker_compose_projects.yaml`
with `include:`. The image build installs only the media-remote dependencies
(`uv sync --no-default-groups`) — drive-sync deps (including the private
`readable-utils` package) are in a dependency group the build never touches.

## Development

```bash
cd backends/python
uv sync              # one venv for everything, readable-utils included
uv run pytest
uv run ruff check .
```

Editors resolve imports via the repo-root `pyrightconfig.json` — no
per-machine settings needed.
