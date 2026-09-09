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
# Canonical copy is the personal context's inventory in the personal_credentials
# repo (assumed checked out beside this one, same place the .env symlink
# points); every context names its inventory <context>_hosts.json.
INVENTORY_SEARCH_PATH = [
    REPO_ROOT.parent / "personal_credentials" / "personal_hosts.json",
    REPO_ROOT / "hosts.json",
    Path.home() / ".config" / "syncplex" / "hosts.json",
    Path.home() / "syncplex_hosts.json",
]


def get_inventory_path() -> Path | None:
    """Resolve the inventory file, or None when nothing declared one.

    SYNCPLEX_HOSTS is an explicit declaration, so a path that is not a readable
    file is a deployment error and raises rather than degrading to None: a
    silent None leaves every UI answering "no results" to every search, which
    reads as an empty library instead of a broken deployment.
    """
    env_path = os.environ.get("SYNCPLEX_HOSTS")
    if env_path:
        p = Path(env_path).expanduser()
        if p.is_dir():
            raise RuntimeError(
                f"SYNCPLEX_HOSTS={p} is a directory, not a file. A docker bind mount whose "
                f"source path does not exist creates an empty directory exactly like this — "
                f"check the volume line in deploy/compose.elitedesk.yaml."
            )
        if not p.is_file():
            raise RuntimeError(f"SYNCPLEX_HOSTS={p} does not exist")
        return p
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


load_env()


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip().strip('"').strip("'")


# --- Postgres / PostgREST ---------------------------------------------------
# Accounts and the request queue live in the shared `apps` database. There is
# deliberately no fallback DSN and no local-file mode: an unreachable database
# is a loud failure, not a silent switch to a different store.

APP_SCHEMA = _env("APP_SCHEMA", "syncplex")

POSTGRES_URL = _env("POSTGRES_URL")
POSTGRES_PORT = _env("POSTGRES_PORT", "5432")
POSTGRES_DB = _env("POSTGRES_DB", "apps")
POSTGRES_USER = _env("POSTGRES_USER")
POSTGRES_PASSWORD = _env("POSTGRES_PASSWORD")

POSTGREST_URL = _env("POSTGREST_URL").rstrip("/")
AUTH_URL = _env("AUTH_URL", "https://auth.tinkernet.me").rstrip("/")
JWT_SECRET = _env("POSTGREST_JWT_SECRET") or _env("JWT_SECRET")

HTTP_TIMEOUT = 10.0


def superuser_dsn() -> str:
    """The bootstrap / credential-read connection (conventions I2, exceptions
    1-3). Application data never travels over this."""
    return (
        f"host={POSTGRES_URL} port={POSTGRES_PORT} dbname={POSTGRES_DB} "
        f"user={POSTGRES_USER} password={POSTGRES_PASSWORD} connect_timeout=5"
    )


def db_configured() -> bool:
    return bool(POSTGRES_URL and POSTGRES_USER and POSTGRES_PASSWORD)
