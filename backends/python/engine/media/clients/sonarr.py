from ..models import EpisodeDetail, InstanceStatus, MediaSearchResult, MediaType, PresenceState, SeasonDetail
from .arr_base import ArrClientBase, poster_url


class SonarrClient(ArrClientBase):
    """Thin async client for the Sonarr v3 API."""

    async def lookup(self, term: str) -> list[dict]:
        """Search series. Items already in this instance's library carry a non-zero id
        and inline statistics, so one call answers both 'what matches' and 'do I have it'."""
        return await self._get("/api/v3/series/lookup", params={"term": term})

    async def lookup_by_tvdb(self, tvdb_id: int) -> list[dict]:
        return await self.lookup(f"tvdb:{tvdb_id}")

    async def get_series(self, series_id: int) -> dict:
        """Authoritative series record — unlike lookup, this includes full
        per-season statistics."""
        return await self._get(f"/api/v3/series/{series_id}")

    async def get_library(self) -> list[dict]:
        """Every series in this instance's library, with authoritative statistics."""
        return await self._get("/api/v3/series")

    async def get_episodes(self, series_id: int) -> list[EpisodeDetail]:
        """Full episode list for a series already in this instance's library."""
        items = await self._get("/api/v3/episode", params={"seriesId": series_id})
        return [
            EpisodeDetail(
                season_number=e.get("seasonNumber", 0),
                episode_number=e.get("episodeNumber", 0),
                title=e.get("title", ""),
                monitored=e.get("monitored", False),
                has_file=e.get("hasFile", False),
                air_date=e.get("airDate", ""),
            )
            for e in items
        ]

    async def add_series(self, lookup_item: dict, quality_profile_id: int, root_folder: str) -> dict:
        payload = dict(lookup_item)
        payload.update(
            {
                "qualityProfileId": quality_profile_id,
                "rootFolderPath": root_folder,
                "monitored": True,
                "seasonFolder": True,
                "addOptions": {"searchForMissingEpisodes": True},
            }
        )
        return await self._post("/api/v3/series", payload)

    @staticmethod
    def to_search_result(item: dict) -> MediaSearchResult:
        stats = item.get("statistics", {})
        return MediaSearchResult(
            media_type=MediaType.TV,
            title=item.get("title", ""),
            year=item.get("year") or None,
            tvdb_id=item.get("tvdbId") or None,
            imdb_id=item.get("imdbId") or None,
            overview=item.get("overview", ""),
            poster_url=poster_url(item),
            network=item.get("network", ""),
            status=item.get("status", ""),
            genres=item.get("genres", []),
            runtime=item.get("runtime") or None,
            season_count=stats.get("seasonCount") or len(item.get("seasons", [])) or None,
        )

    def to_status(self, item: dict | None) -> InstanceStatus:
        """Derive this instance's presence status from a lookup item (None = not in results)."""
        if not item or not item.get("id"):
            return InstanceStatus(instance=self.name, state=PresenceState.NOT_PRESENT)

        stats = item.get("statistics", {})
        total = stats.get("episodeCount", 0)
        files = stats.get("episodeFileCount", 0)
        missing = max(total - files, 0)
        if total == 0 and files == 0:
            # In the library but nothing monitored/downloaded — calling that
            # "complete" misleads; surface it as incomplete instead.
            state = PresenceState.MONITORED_INCOMPLETE
        else:
            state = PresenceState.MONITORED_COMPLETE if missing == 0 else PresenceState.MONITORED_INCOMPLETE
        seasons = [
            SeasonDetail(
                season_number=s.get("seasonNumber", 0),
                monitored=s.get("monitored", False),
                episode_file_count=s.get("statistics", {}).get("episodeFileCount", 0),
                episode_count=s.get("statistics", {}).get("episodeCount", 0),
                total_episode_count=s.get("statistics", {}).get("totalEpisodeCount", 0),
            )
            for s in item.get("seasons", [])
        ]
        return InstanceStatus(
            instance=self.name,
            state=state,
            monitored=item.get("monitored", False),
            missing_episode_count=missing,
            total_episode_count=total,
            series_id=item.get("id"),
            seasons=seasons,
        )
