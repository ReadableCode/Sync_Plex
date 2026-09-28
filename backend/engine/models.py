from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Service:
    """A network service offered by a host (sonarr, radarr, plex, ...).

    Credentials are never stored in the inventory — `api_key_env` names the
    environment variable (typically set via .env) that holds the API key/token.
    """

    type: str
    name: str
    port: int
    scheme: str = "http"
    base_url: str = ""  # optional override; built from host + port when empty
    api_key_env: str = ""
    quality_profile: str = ""  # arr-only: preferred profile name, else first available
    root_folder: str = ""  # arr-only: preferred root folder, else first available


@dataclass
class Machine:
    id: str
    name: str
    hostname: str
    user: str
    port: int = 22
    os: str = "other"  # linux | macos | windows | android | ios | other
    harness: str = "ssh"  # ssh | ping | none
    groups: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    tags: dict[str, str] = field(default_factory=dict)
    identity_file: str | None = None
    services: list[Service] = field(default_factory=list)
    status: str = "unknown"
    last_seen: datetime | None = None
    metadata: dict = field(default_factory=dict)


@dataclass
class CommandResult:
    machine_id: str
    command: str
    stdout: str
    stderr: str
    exit_code: int
    duration_ms: int
    timestamp: datetime
