"""Builds the media instance list from the hosts.json inventory + environment.

Instances are declared as `services` on hosts in hosts.json; credentials come
from the env var named by each service's `api_key_env` (loaded from .env).
Adding another Sonarr/Radarr/Plex instance is a config-only change.
"""

import os
from dataclasses import dataclass, field

from ..config import load_env
from ..inventory import parse_inventory
from ..models import Machine


@dataclass
class ArrInstance:
    name: str
    base_url: str
    api_key: str
    quality_profile: str = ""  # preferred profile name; first available when empty
    root_folder: str = ""  # preferred root folder path; first available when empty


@dataclass
class PlexServer:
    name: str
    base_url: str
    token: str


@dataclass
class MediaConfig:
    sonarr: list[ArrInstance] = field(default_factory=list)
    radarr: list[ArrInstance] = field(default_factory=list)
    plex: list[PlexServer] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def arr_instances(self, media_type: str) -> list[ArrInstance]:
        return self.sonarr if media_type == "tv" else self.radarr


def load_media_config(machines: list[Machine] | None = None) -> MediaConfig:
    load_env()
    if machines is None:
        machines = parse_inventory()

    config = MediaConfig()
    for machine in machines:
        for svc in machine.services:
            if svc.type not in ("sonarr", "radarr", "plex"):
                continue

            base_url = svc.base_url or f"{svc.scheme}://{machine.hostname}:{svc.port}"
            key = os.environ.get(svc.api_key_env, "") if svc.api_key_env else ""
            # docker-compose v1 env_file passes values literally, so KEY="abc"
            # arrives with the quotes attached — strip them.
            key = key.strip().strip('"').strip("'")
            if not key:
                config.warnings.append(
                    f"{svc.name}: env var {svc.api_key_env or '(none set in hosts.json)'} is empty — skipping"
                )
                continue

            if svc.type == "plex":
                config.plex.append(PlexServer(name=svc.name, base_url=base_url, token=key))
            else:
                instance = ArrInstance(
                    name=svc.name,
                    base_url=base_url,
                    api_key=key,
                    quality_profile=svc.quality_profile,
                    root_folder=svc.root_folder,
                )
                (config.sonarr if svc.type == "sonarr" else config.radarr).append(instance)

    return config
