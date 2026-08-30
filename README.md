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
into this repo — nothing is installed on PATH. From a bare clone, or on
Windows where those functions don't exist, run from the repo root:
`uv run --project backends/python syncplex ...` and
`uv run --project backends/python syncplex-drive-sync <path> ...`. See
[Drive sync on Windows](#drive-sync) for the Windows specifics.)

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
on Linux and macOS — no mounted share needed) and deletes files under `TV/` and `Movies/`
that are no longer wanted. In the TUI, `ctrl+s` opens the same tool: type
the path, confirm, watch the output stream.

### Drive sync on Windows

There is no `syncdrive` command on Windows — the `cli/` wrappers and the
dotfiles functions are bash/zsh only. Run the entry point through uv,
**from the repo root**, with the drive's media path:

```powershell
cd C:\GitHub\Sync_Plex
uv run --project backends\python syncplex-drive-sync E:\Media
uv run --project backends\python syncplex-drive-sync E:\Media --yes
```

Windows notes:

- **Run from the repo root.** The scraper finds `.env` by searching upward
  from the current directory, not from the repo.
- **`.env` is a symlink** (to `../personal_credentials/personal.env`). Git on
  Windows checks symlinks out as plain text files unless the clone was made
  with `core.symlinks=true` (needs Developer Mode or admin). If the symlink
  is broken, copy `personal_credentials\personal.env` to `.env` at the repo
  root instead.
- **Files come over SMB** from `\\<plex-host>\Media` (host parsed from
  `PLEX_SERVER` in `.env`). Open that share once in Explorer first if it
  needs credentials.
- **NumPy `DLL load failed` on import** means uv resolved a pre-release
  Python (look for the `3.14.0aX` warning at the top of the output) —
  binary wheels don't load on alpha builds. Run `uv python upgrade 3.14`,
  delete `backends\python\.venv`, and rerun.

## Web UI

Own login page (no Authelia in front); TLS comes from the reverse proxy.
Passwords are verified by the shared `postgrest-auth` service, which owns the
argon2id policy and the per-username/per-IP lockout for every app at once. The
JWT it returns is both the session credential and the Bearer token for
PostgREST, so row-level security scopes the request queue off the same claims
the login produced.

Accounts and the request queue live in the `syncplex` schema of the shared
`apps` Postgres database. There is no file fallback: if Postgres is
unreachable the app refuses to start rather than quietly serving an empty
store. Account commands need the deployment's database environment, so run
them through compose:

```bash
docker compose -f docker_compose_projects.yaml exec syncplex-web \
    syncplex users add jason --role admin
docker compose -f docker_compose_projects.yaml exec syncplex-web \
    syncplex users add friendname          # default role: user
```

A host shell would either fail on missing `POSTGRES_*` env or reach a
different database, so the compose form is the only supported one.

The first account must be created this way — with zero accounts nobody can
log in. Admins add titles directly and work the approval queue at
`/requests`; users can search everything but only *request* — nothing
downloads until an admin approves and picks the server. A password change,
disable, re-enable, or role change all bump `password_changed_at`, which kills
that account's live sessions immediately. Set `SYNCPLEX_SESSION_SECRET` so the
NiceGUI session cookie survives restarts.

`$SYNCPLEX_DATA_DIR` (`/data` in the container) still exists, but it now holds
only NiceGUI's server-side session scratch — plus the
`users.json.migrated` / `requests.json.migrated` originals kept by the
one-shot import (`scripts/import_json_stores.py`, run with `--dry-run` first).

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
