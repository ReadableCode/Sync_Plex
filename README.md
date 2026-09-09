# Sync_Plex

The household media app. Two things, one project:

1. **Media remote** — search and add shows/movies across every Sonarr, Radarr,
   and Plex instance you run, from a CLI, a TUI, or a phone-friendly web UI.
2. **Drive sync** — mirror the shows/movies you pick onto an external drive
   (road trips, flights), pulling the files from the Plex server.

## Quick start

```bash
syncplex                           # the TUI: search, add or request, the queue (ctrl+s = drive sync)
syncplex search "severance"        # status on every instance, merged
syncplex add "severance" --to sonarr-elitedesk
syncdrive                          # drive sync TUI: browse for the drive, edit its config, sync
```

(`syncplex` and `syncdrive` are shell functions from my dotfiles that `uv run`
into this repo — nothing is installed on PATH, and neither `syncplex` nor
`syncplex-drive-sync` works as a bare command without them. Without those
functions, or on Windows, run from the repo root:

```bash
uv run --project backends/python syncplex ...
uv run --project backends/python syncplex-drive-sync [<path>] [--check]
```

`syncdrive` is just the second line. See
[Drive sync on Windows](#drive-sync) for the Windows specifics.)

## Commands

All commands are flat — no nested groups except `users`.

| Command | What it does |
| --- | --- |
| `syncplex search "title" [-t tv\|movie] [--plex]` | One merged status view across every instance |
| `syncplex seasons "title" [--episodes]` | Per-season / per-episode breakdown |
| `syncplex add "title" --to <instance>` | Add the top result to that instance |
| `syncplex instances` | List configured instances (from personal_hosts.json + .env) |
| `syncplex` (or `syncplex tui`) | Textual TUI: search, add or request, the approval queue, server health; drive sync on `ctrl+s` |
| `syncplex web [--host IP] [--port 8788]` | The web UI (NiceGUI) |
| `syncplex users <add\|list\|passwd\|role\|disable\|enable\|remove>` | Web UI accounts |
| `syncdrive [path] [--check]` | Drive sync TUI (`uv run ... syncplex-drive-sync`): browse for the drive, edit its config, sync with progress; `--check` prints the plan headless and exits 1 if the drive is behind its config |

Data commands take `--json` for scripting.

Both also run through `cmdr`, the fleet CLI/TUI from dotfiles: `commands/`
holds the definitions it discovers (`cmdr syncplex` opens the remote,
`cmdr syncdrive` opens the drive-sync TUI; `--check` on either is the
read-only probe). Both steps are marked `terminal`, so cmdr's TUI hands the
screen over to them and resumes when they exit.

## The TUI

`syncplex tui` is the media remote and the request queue in one terminal app,
signed in with the same accounts as the web UI (`ctrl+l`; the token is kept
in `~/.config/syncplex/tui_session.json` for the session's 30 days, so it
asks once). Under the header, a strip shows every server: up or down,
latency, free disk, and, for an admin, how many requests are waiting.

- **media**: type to search; `escape` hops to the results, where the letter
  keys work, and `/` goes back to typing. `t` flips tv/movies, `↑↓` picks a
  result, `r` refreshes it. The detail pane shows presence, seasons and size
  per server, Plex watch-readiness, and what an add would cost against each
  server's free space. `a` adds (a picker when more than one server could
  take it, preselecting the one with the most room). Signed in as a
  non-admin, `w` files or withdraws a request instead, with the same ntfy
  ping the web sends.
- **requests** (`ctrl+r`): the pending queue with the requester and when,
  and the same server picture for the highlighted one. `enter` or `a`
  approves onto a server (picker, best server preselected; the download
  only starts when the server accepts, exactly like the web), `d` denies
  with a reason, `r` refreshes. History sits below. A non-admin sees their
  own requests and can withdraw with `w`.
- `f1` or `?` opens the key reference, `ctrl+q` quits.

## How it's put together

One Python project at `backends/python`, two packages, no internal REST API —
every UI imports the same code in-process:

```plaintext
backends/python/
├── engine/            # media remote: inventory, per-service clients,
│   │                  #   status aggregation, request queue
│   ├── cli.py             # all the flat commands above
│   ├── media/present.py   # wording both UIs share (badges, sizes, headroom)
│   ├── media/tui/         # the TUI: app.py (screens), session.py (login), theme.py
│   └── web/               # web UI + its login/accounts
├── drive_sync/        # drive sync: drive_config, library (plex + fuzzy),
│                      #   plan, transfer, screens (the TUI), cli
└── tests/
```

Repo root: `cli/` shell wrappers, `commands/` (cmdr definitions and their
step functions), `deploy/compose.elitedesk.yaml` (web deployment), `.env` →
symlink into personal_credentials, `pyrightconfig.json` (points editors at
`backends/python/.venv`).

## Configuration

Two files, both living in the sibling `personal_credentials` repo:

- **`personal_hosts.json`** — the inventory. Each host lists the services it offers;
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
  `../personal_credentials/personal_hosts.json` → repo-root `hosts.json` →
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

`syncdrive` (or `uv run --project backends/python syncplex-drive-sync`) is a
TUI. With no path it opens an ncdu-style folder browser: enter opens a
folder, backspace goes up, `/` jumps to the root (the drive list on Windows),
and space picks the folder you are in; folders already holding a
`config.yaml` are marked. With a path it goes straight to that drive.

The drive screen is one row per configured title: how many files Plex has for
it, how many are on the drive, and what a sync would get and remove, with a
summary line of sizes against the drive's free space. Keys:

- `enter` syncs (after a confirmation that names the counts and sizes)
- `a` / `m` add a show / movie: type part of the title, pick the Plex match
- `f` on a title that is **not on plex** picks the right one and rewrites the
  config entry (the row already shows the closest guess)
- `d` removes the title; its files go on the next sync
- `+` / `-` change how many next episodes a show keeps
- `e` opens the config in an editor: VS Code when `code` is on PATH, else
  `$VISUAL`/`$EDITOR`, else nvim, else vim; `c` creates an empty config on a
  folder that has none; `r` reloads

The sync screen runs deletes first, then downloads, one row per file with
live progress (rsync over SSH on macOS and Linux, a copy off the SMB share
on Windows) and a log of what finished or failed; escape asks before stopping.
A failed file does not stop the rest.

For scripts and cmdr's check convention, `--check` prints the plan without
the TUI and exits 1 when the drive differs from its config (or names a title
that is not on Plex):

```bash
syncdrive /Volumes/ExtSSD/Media --check
```

`cmdr syncdrive` (from the dotfiles fleet CLI) is the no-path TUI form;
`cmdr syncdrive --check /Volumes/ExtSSD/Media` is the headless check, and it
asks for the folder when none is given.

### Drive sync on Windows

The `cli/` wrappers are bash-only; on Windows use the `syncdrive` PowerShell
function from the dotfiles shard, `cmdr syncdrive`, or run the entry point
through uv, **from the repo root**, with the drive's media path:

```powershell
cd C:\GitHub\Sync_Plex
uv run --project backends\python syncplex-drive-sync E:\Media
uv run --project backends\python syncplex-drive-sync E:\Media --check
```

Windows notes:

- **Run from the repo root.** The Plex wrapper finds `.env` by searching
  upward from the current directory, not from the repo.
- **`.env` is a symlink** (to `../personal_credentials/personal.env`). Git on
  Windows checks symlinks out as plain text files unless the clone was made
  with `core.symlinks=true` (needs Developer Mode or admin). If the symlink
  is broken, copy `personal_credentials\personal.env` to `.env` at the repo
  root instead.
- **Files come over SMB** from `\\<plex-host>\Media` (host parsed from
  `PLEX_SERVER` in `.env`). Open that share once in Explorer first if it
  needs credentials.

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
uv run mypy           # `type: ignore` comments are only real if something checks them
```

Editors resolve imports via the repo-root `pyrightconfig.json` — no
per-machine settings needed.
