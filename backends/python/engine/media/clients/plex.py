import httpx

from ..config import PlexServer
from ..models import MediaSearchResult, MediaType, PlexAvailability

DEFAULT_TIMEOUT = 8.0

# Plex library item types per media type
_PLEX_TYPES = {MediaType.TV: "show", MediaType.MOVIE: "movie"}


class PlexClient:
    """Minimal async Plex client — presence checks only, via the /library/all filter
    endpoint with includeGuids so titles can be matched by TVDB/TMDB id, not name."""

    def __init__(self, server: PlexServer, timeout: float = DEFAULT_TIMEOUT):
        self.server = server
        self.timeout = timeout

    @property
    def name(self) -> str:
        return self.server.name

    async def _search(self, title: str) -> list[dict]:
        async with httpx.AsyncClient(
            base_url=self.server.base_url,
            headers={"X-Plex-Token": self.server.token, "Accept": "application/json"},
            timeout=self.timeout,
        ) as client:
            resp = await client.get("/library/all", params={"title": title, "includeGuids": "1"})
            resp.raise_for_status()
            return resp.json().get("MediaContainer", {}).get("Metadata", []) or []

    async def check_presence(self, result: MediaSearchResult) -> PlexAvailability:
        try:
            items = await self._search(result.title)
        except Exception as exc:  # noqa: BLE001 — one server down must not break the check
            return PlexAvailability(server=self.name, available=False, error=str(exc))

        wanted_type = _PLEX_TYPES[result.media_type]
        wanted_guids = set()
        if result.tvdb_id:
            wanted_guids.add(f"tvdb://{result.tvdb_id}")
        if result.tmdb_id:
            wanted_guids.add(f"tmdb://{result.tmdb_id}")
        if result.imdb_id:
            wanted_guids.add(f"imdb://{result.imdb_id}")

        for item in items:
            if item.get("type") != wanted_type:
                continue
            guids = {g.get("id", "") for g in item.get("Guid", [])}
            if wanted_guids & guids:
                return PlexAvailability(server=self.name, available=True)
            # Fallback for items without external GUIDs: exact title + year
            if not guids and item.get("title", "").casefold() == result.title.casefold():
                if not result.year or item.get("year") in (result.year, result.year - 1, result.year + 1):
                    return PlexAvailability(server=self.name, available=True)

        return PlexAvailability(server=self.name, available=False)
