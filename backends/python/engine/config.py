import os
from pathlib import Path

# Repo root (this file is backends/python/engine/config.py). In shallower
# layouts (e.g. the docker image, where the package sits at /app/engine) the
# repo-relative paths don't exist — fall back to the package's parent dir and
# rely on SYNCPLEX_HOSTS / the container environment instead.
try:
    REPO_ROOT = Path(__file__).resolve().parents[3]
except IndexError:
    REPO_ROOT = Path(__file__).resolve().parents[1]

# Inventory search path — first match wins. SYNCPLEX_HOSTS env var overrides.
# Canonical copy lives in the personal_credentials repo (assumed checked out
# beside this one), same place the .env symlink points.
INVENTORY_SEARCH_PATH = [
    REPO_ROOT.parent / "personal_credentials" / "hosts.json",
    REPO_ROOT / "hosts.json",
    Path.home() / ".config" / "syncplex" / "hosts.json",
    Path.home() / "syncplex_hosts.json",
]


def get_inventory_path() -> Path | None:
    # SYNCPLEX_HOSTS wins; HERDSTONE_HOSTS is honored as a fallback because
    # deployments share the same hosts.json convention with herdstone during
    # the migration of the media remote out of that repo.
    env_path = os.environ.get("SYNCPLEX_HOSTS") or os.environ.get("HERDSTONE_HOSTS")
    if env_path:
        p = Path(env_path).expanduser()
        return p if p.is_file() else None
    for path in INVENTORY_SEARCH_PATH:
        if path.is_file():
            return path
    return None


def get_data_dir() -> Path:
    """Writable state dir (web UI user accounts, media requests).

    SYNCPLEX_DATA_DIR overrides (the docker deployment mounts a host dir at
    /data and sets it); default is ~/.config/syncplex.
    """
    env_dir = os.environ.get("SYNCPLEX_DATA_DIR")
    directory = Path(env_dir).expanduser() if env_dir else Path.home() / ".config" / "syncplex"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def load_env() -> None:
    """Load .env from the repo root (and CWD as fallback). Idempotent, safe to call often."""
    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env")
    load_dotenv()  # CWD .env, does not override already-set vars
