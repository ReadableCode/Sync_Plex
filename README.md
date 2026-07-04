# Sync_Plex

> The household media application: search/add shows and movies across every
> Sonarr/Radarr/Plex instance you run — from one CLI, TUI, or phone-friendly
> web UI — plus the original selective media file sync scripts.

---

## Media remote

One Python engine, three thin presentation layers. There is no internal REST
API — the CLI, TUI, and web UI all import the same `engine` package and call
the same functions in-process.

```plaintext
Sync_Plex/
├── backends/python/
│   ├── pyproject.toml        # Python project config (uv)
│   ├── engine/               # The engine package
│   │   ├── models.py             # Machine, Service dataclasses
│   │   ├── config.py             # hosts.json search path, .env loading
│   │   ├── inventory.py          # hosts.json parser + Ansible INI importer
│   │   ├── cli.py                # Typer entry point (--json everywhere)
│   │   ├── media/                # media remote core (shared by CLI/TUI/web)
│   │   │   ├── config.py             # builds instance list from hosts.json services + .env
│   │   │   ├── models.py             # pydantic domain models (AggregatedResult, ...)
│   │   │   ├── aggregation.py        # search_everywhere, add_to_instance, plex checks
│   │   │   ├── clients/              # httpx async clients: sonarr, radarr, plex
│   │   │   └── tui/app.py            # Textual TUI (couch/SSH use)
│   │   └── web/app.py            # NiceGUI web UI (phone use, Tailscale-bound)
│   └── tests/
├── cli/syncplex              # shell wrapper: uv run into backends/python
├── src/                      # legacy selective sync scripts (untouched)
├── sync_config.json          # selective sync configuration
├── .env -> ../personal_credentials/personal.env   # symlink, gitignored
├── .env.example              # API key placeholders
└── README.md

../personal_credentials/hosts.json   # THE inventory — machines + services they offer
../personal_credentials/personal.env # API keys/tokens referenced by hosts.json
```

### Inventory: `hosts.json`

One JSON file is the single source of truth. Each host declares **which
services it offers**. Adding another Sonarr/Radarr/Plex instance is a
config-only change — no code.

```json
{
  "hosts": [
    {
      "name": "behemoth",
      "hostname": "192.168.86.31",
      "os": "linux",
      "groups": ["unraid"],
      "services": [
        { "type": "sonarr", "name": "sonarr-behemoth", "port": 8989,  "api_key_env": "SONARR_BEHEMOTH_API_KEY" },
        { "type": "radarr", "name": "radarr-behemoth", "port": 7878,  "api_key_env": "RADARR_BEHEMOTH_API_KEY" },
        { "type": "plex",   "name": "plex-behemoth",   "port": 32400, "api_key_env": "PLEX_TOKEN" }
      ]
    }
  ]
}
```

Optional service fields: `scheme` (default `http`), `base_url` (full override),
`quality_profile` and `root_folder` (preferred add-time defaults; first
available on the server otherwise).

Search order: `$SYNCPLEX_HOSTS` (`$HERDSTONE_HOSTS` accepted as a fallback —
shared deployment convention during the migration) →
`../personal_credentials/hosts.json` (canonical — it carries internal
IPs/usernames, so it lives in the private credentials repo) → repo-root
`hosts.json` → `~/.config/syncplex/hosts.json` → `~/syncplex_hosts.json`.

Secrets never live in the inventory — each service names the env var
(`api_key_env`) that holds its key/token. `.env` in this repo is a gitignored
symlink to `../personal_credentials/personal.env` (see `.env.example` for the
expected keys).

### CLI

| Command | Description |
| --- | --- |
| `syncplex media instances` | Show configured Sonarr/Radarr/Plex instances |
| `syncplex media search "title" [-t tv\|movie] [--plex]` | Search every instance, one merged status view |
| `syncplex media seasons "title" [--episodes]` | Per-season (and per-episode) monitored/on-disk breakdown |
| `syncplex media add "title" --to {instance}` | Add the top result to a chosen instance |
| `syncplex tui` | Launch the TUI (Textual): media remote + sync jobs screen (`ctrl+s`) |
| `syncplex media tui` | Same TUI (kept for muscle memory) |
| `syncplex web [--host IP] [--port 8788]` | Launch the media remote web UI (NiceGUI) |

All data commands support `--json`, which is how native UI shells consume the
engine as a subprocess.

### Media remote in 30 seconds

```bash
syncplex media instances     # verify what's configured (keys come from .env)
syncplex media search "severance" --plex
#   Severance (2022)  [tvdb:371980]
#     ● sonarr-behemoth      monitored_complete
#     ○ sonarr-elitedesk     not_present
#     ▶ plex-behemoth        watch-ready
syncplex media add "severance" --to sonarr-elitedesk
```

Statuses merge by TVDB/TMDB id (never by title string), one instance being
down degrades to a `✗ unreachable` row instead of breaking the search, and
Plex rows tell you whether it's actually watch-ready.

### Web UI deployment

Runs as a single process; bind it to your Tailscale IP on an always-on box so
phones on the tailnet can reach it. Never expose it publicly — there is no
auth layer by design (tailnet membership is the auth).

```bash
syncplex web --host 100.x.x.x --port 8788
```

### Development setup

```bash
cd backends/python
uv sync
uv run syncplex --help
uv run pytest
uv run ruff check .
```

---

## Selective sync (legacy)

The original purpose of this repo: mirror a chosen subset of a media library
onto a local drive (e.g. specific audiobook series onto a portable disk).
These scripts are untouched by the media remote and keep working as-is.

- `src/selective_sync.py` — reads `sync_config.json` at the repo root; for
  each entry in `sync_folders` it syncs the listed `included_subfolders` (and
  `included_files`) from a source (e.g. a Windows network mount like
  `\\192.168.86.31\Media\Audiobooks`) to a destination (e.g. local drive
  `I:\Media\Audiobooks`) using `robocopy /MIR` on Windows, then prunes
  anything at the destination that is not part of the configuration.
- `sync_config.json` — declares the sync jobs: `sync_name`, `src_path_type` /
  `src_path`, `dest_path_type` / `dest_path`, and the include lists (paths as
  arrays of path components).

Run from the repo root (Windows is the primary target — the copy step uses
robocopy):

```bash
uv run python src/selective_sync.py
```

The sync configuration also has a screen in the TUI: `syncplex tui`, then
`ctrl+s` to inspect the configured jobs and `R` to run them all (after an
explicit confirmation — the run mirrors and prunes destinations). The legacy
script stays the execution engine; the TUI just shells out to it.
