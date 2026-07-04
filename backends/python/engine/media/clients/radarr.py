from ..models import InstanceStatus, MediaSearchResult, MediaType, PresenceState
from .arr_base import ArrClientBase, poster_url


class RadarrClient(ArrClientBase):
    """Thin async client for the Radarr v3 API."""

    async def lookup(self, term: str) -> list[dict]:
        """Search movies. Items already in this instance's library carry a non-zero id."""
        return await self._get("/api/v3/movie/lookup", params={"term": term})

    async def lookup_by_tmdb(self, tmdb_id: int) -> list[dict]:
        return await self.lookup(f"tmdb:{tmdb_id}")

    async def get_library(self) -> list[dict]:
        """Every movie in this instance's library. Lookup responses leave
        hasFile empty even for downloaded movies — these records are authoritative."""
        return await self._get("/api/v3/movie")

    async def add_movie(self, lookup_item: dict, quality_profile_id: int, root_folder: str) -> dict:
        payload = dict(lookup_item)
        payload.update(
            {
                "qualityProfileId": quality_profile_id,
                "rootFolderPath": root_folder,
                "monitored": True,
                "addOptions": {"searchForMovie": True},
            }
        )
        return await self._post("/api/v3/movie", payload)

    @staticmethod
    def to_search_result(item: dict) -> MediaSearchResult:
        return MediaSearchResult(
            media_type=MediaType.MOVIE,
            title=item.get("title", ""),
            year=item.get("year") or None,
            tmdb_id=item.get("tmdbId") or None,
            imdb_id=item.get("imdbId") or None,
            overview=item.get("overview", ""),
            poster_url=poster_url(item),
            network=item.get("studio", ""),
            status=item.get("status", ""),
            genres=item.get("genres", []),
            runtime=item.get("runtime") or None,
        )

    def to_status(self, item: dict | None) -> InstanceStatus:
        """Derive this instance's presence status from a lookup item (None = not in results)."""
        if not item or not item.get("id"):
            return InstanceStatus(instance=self.name, state=PresenceState.NOT_PRESENT)

        has_file = bool(item.get("hasFile"))
        state = PresenceState.MONITORED_COMPLETE if has_file else PresenceState.MONITORED_INCOMPLETE
        return InstanceStatus(
            instance=self.name,
            state=state,
            monitored=item.get("monitored", False),
        )
