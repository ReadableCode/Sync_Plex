"""Fan-out search/status/add across all configured instances.

This is the shared core both UIs call. Every function degrades gracefully:
an unreachable instance becomes an UNREACHABLE status entry, never an exception
that kills the whole search.
"""

import asyncio
import time

from .clients import PlexClient, RadarrClient, SonarrClient
from .config import ArrInstance, MediaConfig, load_media_config
from .models import (
    AddResult,
    AggregatedResult,
    EpisodeDetail,
    InstanceStatus,
    MediaType,
    PresenceState,
)


def _client_for(instance: ArrInstance, media_type: MediaType) -> SonarrClient | RadarrClient:
    return SonarrClient(instance) if media_type == MediaType.TV else RadarrClient(instance)


def _external_key(item: dict, media_type: MediaType) -> str:
    if media_type == MediaType.TV and item.get("tvdbId"):
        return f"tvdb:{item['tvdbId']}"
    if media_type == MediaType.MOVIE and item.get("tmdbId"):
        return f"tmdb:{item['tmdbId']}"
    return f"title:{item.get('title', '').casefold()}:{item.get('year') or 0}"


# Library snapshots per instance, indexed by external id. Lookup responses lie
# about presence detail (Radarr omits hasFile, Sonarr omits season statistics),
# so statuses are derived from the instance's real library instead. A short TTL
# keeps search-as-you-type from refetching the library on every keystroke.
_LIBRARY_TTL_SECONDS = 60.0
_library_cache: dict[str, tuple[float, dict[int, dict]]] = {}


def invalidate_library_cache(instance_name: str | None = None) -> None:
    if instance_name is None:
        _library_cache.clear()
    else:
        _library_cache.pop(instance_name, None)


async def _library_index(client: SonarrClient | RadarrClient) -> dict[int, dict]:
    now = time.monotonic()
    cached = _library_cache.get(client.name)
    if cached and now - cached[0] < _LIBRARY_TTL_SECONDS:
        return cached[1]
    id_field = "tvdbId" if isinstance(client, SonarrClient) else "tmdbId"
    index = {item[id_field]: item for item in await client.get_library() if item.get(id_field)}
    _library_cache[client.name] = (now, index)
    return index


async def _instance_snapshot(client: SonarrClient | RadarrClient, query: str) -> dict:
    """One instance's search results plus its library keyed by external id."""
    results, library = await asyncio.gather(client.lookup(query), _library_index(client))
    return {"results": results, "library": library}


def merge_lookups(
    per_instance: dict[str, dict | Exception],
    media_type: MediaType,
    config: MediaConfig,
) -> list[AggregatedResult]:
    """Merge per-instance snapshots into one AggregatedResult per unique title.

    Merging is keyed on external IDs (TVDB/TMDB), not title strings, so
    similarly-named shows never collapse into one entry. Presence comes from
    each instance's library record (authoritative), not the lookup item. Every
    configured instance gets a status row on every result: NOT_PRESENT when
    its library lacks the title, UNREACHABLE when the instance itself errored.
    """
    merged: dict[str, AggregatedResult] = {}
    items_by_key: dict[str, dict[str, dict]] = {}  # key -> instance -> raw lookup item

    for instance in config.arr_instances(media_type.value):
        snapshot = per_instance.get(instance.name)
        if not isinstance(snapshot, dict):
            continue
        client = _client_for(instance, media_type)
        for item in snapshot["results"]:
            key = _external_key(item, media_type)
            if key not in merged:
                merged[key] = AggregatedResult(result=client.to_search_result(item))
            items_by_key.setdefault(key, {})[instance.name] = item

    for aggregated_key, aggregated in merged.items():
        result = aggregated.result
        ext_id = result.tvdb_id if media_type == MediaType.TV else result.tmdb_id
        for instance in config.arr_instances(media_type.value):
            snapshot = per_instance.get(instance.name)
            if not isinstance(snapshot, dict):
                aggregated.statuses.append(
                    InstanceStatus(
                        instance=instance.name,
                        state=PresenceState.UNREACHABLE,
                        # httpx timeouts stringify to "" — fall back to the class name
                        error=str(snapshot) or type(snapshot).__name__,
                    )
                )
                continue
            client = _client_for(instance, media_type)
            if ext_id:
                item = snapshot["library"].get(ext_id)
            else:
                # No external id to match on — fall back to the lookup item
                item = items_by_key.get(aggregated_key, {}).get(instance.name)
            aggregated.statuses.append(client.to_status(item))

    return list(merged.values())


async def search_everywhere(
    query: str,
    media_type: MediaType,
    config: MediaConfig | None = None,
) -> list[AggregatedResult]:
    """Search all Sonarr (tv) or Radarr (movie) instances concurrently and merge."""
    if config is None:
        config = load_media_config()
    instances = config.arr_instances(media_type.value)
    if not instances:
        return []

    clients = [_client_for(i, media_type) for i in instances]
    snapshots = await asyncio.gather(*(_instance_snapshot(c, query) for c in clients), return_exceptions=True)
    per_instance: dict[str, dict | Exception] = {
        c.name: s for c, s in zip(clients, snapshots)  # type: ignore[misc]
    }
    return merge_lookups(per_instance, media_type, config)


async def check_plex_availability(
    aggregated: AggregatedResult,
    config: MediaConfig | None = None,
) -> AggregatedResult:
    """Annotate a result with watch-readiness across all Plex servers."""
    if config is None:
        config = load_media_config()
    clients = [PlexClient(s) for s in config.plex]
    aggregated.plex = list(await asyncio.gather(*(c.check_presence(aggregated.result) for c in clients)))
    return aggregated


async def refresh_status(
    aggregated: AggregatedResult,
    config: MediaConfig | None = None,
    include_plex: bool = True,
) -> AggregatedResult:
    """Re-poll every instance (and optionally Plex) for one title by its external ID."""
    if config is None:
        config = load_media_config()
    invalidate_library_cache()  # an explicit refresh must not serve cached presence
    result = aggregated.result
    # tvdb:/tmdb: keys work as lookup terms; title-keyed results fall back to a title search
    term = result.title if result.external_key.startswith("title:") else result.external_key

    refreshed = await search_everywhere(term, result.media_type, config)
    for candidate in refreshed:
        if candidate.result.external_key == result.external_key:
            if include_plex:
                await check_plex_availability(candidate, config)
            return candidate

    # Nothing came back (e.g. all instances down) — keep what we had
    return aggregated


async def enrich_tv_statuses(
    aggregated: AggregatedResult,
    config: MediaConfig | None = None,
) -> AggregatedResult:
    """Replace lookup-derived statuses with authoritative per-series data.

    Sonarr lookup responses often omit episode/season statistics for library
    entries; /api/v3/series/{id} always has them. Only touches instances that
    have the series; failures keep the lookup-derived status.
    """
    if config is None:
        config = load_media_config()
    if aggregated.result.media_type != MediaType.TV:
        return aggregated

    targets = [
        (instance, status)
        for instance in config.sonarr
        for status in [aggregated.status_for(instance.name)]
        if status is not None and status.series_id
    ]
    if not targets:
        return aggregated

    async def _refetch(instance: ArrInstance, status: InstanceStatus) -> InstanceStatus:
        client = SonarrClient(instance)
        try:
            item = await client.get_series(status.series_id)  # type: ignore[arg-type]
        except Exception:  # noqa: BLE001 — keep the lookup-derived status on failure
            return status
        # A response without an id would read as NOT_PRESENT and overwrite a
        # known-good status — only trust a well-formed series record.
        if not isinstance(item, dict) or not item.get("id"):
            return status
        return client.to_status(item)

    refreshed = await asyncio.gather(*(_refetch(i, s) for i, s in targets))
    by_name = {s.instance: s for s in refreshed}
    aggregated.statuses = [by_name.get(s.instance, s) for s in aggregated.statuses]
    return aggregated


async def episodes_everywhere(
    aggregated: AggregatedResult,
    config: MediaConfig | None = None,
) -> dict[str, list[EpisodeDetail]]:
    """Fetch the full episode list from every instance that has the series.

    Returns instance name -> episodes; instances without the series (or that
    error) are omitted.
    """
    if config is None:
        config = load_media_config()
    if aggregated.result.media_type != MediaType.TV:
        return {}

    targets = [
        (instance, status.series_id)
        for instance in config.sonarr
        for status in [aggregated.status_for(instance.name)]
        if status is not None and status.series_id
    ]
    if not targets:
        return {}

    async def _fetch(instance: ArrInstance, series_id: int) -> list[EpisodeDetail]:
        return await SonarrClient(instance).get_episodes(series_id)

    results = await asyncio.gather(
        *(_fetch(i, sid) for i, sid in targets),
        return_exceptions=True,
    )
    return {
        instance.name: episodes
        for (instance, _), episodes in zip(targets, results)
        if not isinstance(episodes, BaseException)
    }


async def add_to_instance(
    aggregated: AggregatedResult,
    instance_name: str,
    config: MediaConfig | None = None,
    quality_profile: str = "",
) -> AddResult:
    """Add the title to one specific instance, using its default quality profile
    and root folder unless overridden."""
    if config is None:
        config = load_media_config()
    result = aggregated.result

    instance = next(
        (i for i in config.arr_instances(result.media_type.value) if i.name == instance_name),
        None,
    )
    if instance is None:
        return AddResult(instance=instance_name, ok=False, message=f"No such instance: {instance_name}")
    if quality_profile:
        instance.quality_profile = quality_profile

    client = _client_for(instance, result.media_type)
    try:
        if isinstance(client, SonarrClient):
            if not result.tvdb_id:
                return AddResult(instance=instance_name, ok=False, message="Result has no TVDB id")
            items = await client.lookup_by_tvdb(result.tvdb_id)
        else:
            if not result.tmdb_id:
                return AddResult(instance=instance_name, ok=False, message="Result has no TMDB id")
            items = await client.lookup_by_tmdb(result.tmdb_id)
        if not items:
            return AddResult(instance=instance_name, ok=False, message="Title not found by external id")
        item = items[0]
        if item.get("id"):
            return AddResult(instance=instance_name, ok=False, message="Already present on this instance")

        profile_id, root_folder = await client.resolve_add_defaults()
        if isinstance(client, SonarrClient):
            await client.add_series(item, profile_id, root_folder)
        else:
            await client.add_movie(item, profile_id, root_folder)
    except Exception as exc:  # noqa: BLE001 — surfaced to the UI as a failed add
        return AddResult(instance=instance_name, ok=False, message=str(exc))

    invalidate_library_cache(instance_name)  # so the next search/refresh sees the new title
    return AddResult(instance=instance_name, ok=True, message=f"Added '{result.title}' to {instance_name}")


def search_and_merge(query: str, media_type: MediaType, config: MediaConfig | None = None):
    """Sync convenience wrapper for CLI/scripts."""
    return asyncio.run(search_everywhere(query, media_type, config))
