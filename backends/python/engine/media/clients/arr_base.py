"""Shared HTTP plumbing for Sonarr and Radarr (both expose the same v3 API shape)."""

from typing import Any

import httpx

from ..config import ArrInstance

DEFAULT_TIMEOUT = 8.0


class ArrClientBase:
    def __init__(self, instance: ArrInstance, timeout: float = DEFAULT_TIMEOUT):
        self.instance = instance
        self.timeout = timeout

    @property
    def name(self) -> str:
        return self.instance.name

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.instance.base_url,
            headers={"X-Api-Key": self.instance.api_key},
            timeout=self.timeout,
        )

    async def _get(self, path: str, params: dict | None = None) -> Any:
        async with self._client() as client:
            resp = await client.get(path, params=params)
            resp.raise_for_status()
            return resp.json()

    async def _post(self, path: str, payload: dict) -> Any:
        async with self._client() as client:
            resp = await client.post(path, json=payload)
            resp.raise_for_status()
            return resp.json()

    async def quality_profiles(self) -> list[dict]:
        return await self._get("/api/v3/qualityprofile")

    async def root_folders(self) -> list[dict]:
        return await self._get("/api/v3/rootfolder")

    async def resolve_add_defaults(self) -> tuple[int, str]:
        """Pick the quality profile id and root folder path to use for an add.

        Honors the instance's configured preferences, falling back to the first
        of each the server offers.
        """
        profiles = await self.quality_profiles()
        if not profiles:
            raise RuntimeError(f"{self.name}: no quality profiles configured on server")
        profile_id = profiles[0]["id"]
        if self.instance.quality_profile:
            for p in profiles:
                if p.get("name", "").casefold() == self.instance.quality_profile.casefold():
                    profile_id = p["id"]
                    break
            else:
                raise RuntimeError(
                    f"{self.name}: quality profile '{self.instance.quality_profile}' not found on server"
                )

        folders = await self.root_folders()
        if not folders:
            raise RuntimeError(f"{self.name}: no root folders configured on server")
        root_path = folders[0]["path"]
        if self.instance.root_folder:
            for f in folders:
                if f.get("path") == self.instance.root_folder:
                    root_path = f["path"]
                    break
            else:
                raise RuntimeError(f"{self.name}: root folder '{self.instance.root_folder}' not found on server")

        return profile_id, root_path


def poster_url(item: dict) -> str:
    for image in item.get("images", []):
        if image.get("coverType") == "poster":
            return image.get("remoteUrl") or image.get("url") or ""
    return ""
