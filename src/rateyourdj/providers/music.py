from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol
from urllib.parse import urlencode

from rateyourdj.collectors.http import request_json
from rateyourdj.collectors.spotify import SPOTIFY_API_URL, SpotifyCollector


class ProviderError(RuntimeError):
    """Raised when an external music provider cannot satisfy a request."""


@dataclass(frozen=True, slots=True)
class TrackQuery:
    title: str
    artist: str
    album: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderArtist:
    name: str
    provider_artist_id: str | None = None
    external_urls: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ProviderTrack:
    track_id: str
    provider: str
    title: str
    artist: str
    artists: list[ProviderArtist] = field(default_factory=list)
    album: str | None = None
    release_year: int | None = None
    duration_ms: int | None = None
    image_url: str | None = None
    preview_url: str | None = None
    external_urls: dict[str, str] = field(default_factory=dict)
    tags: dict[str, float] = field(default_factory=dict)
    genres: dict[str, float] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["artists"] = [artist.to_dict() for artist in self.artists]
        return value


@dataclass(frozen=True, slots=True)
class ProviderSearchResult:
    provider: str
    query: str
    tracks: list[ProviderTrack]
    cache_hit: bool = False
    diagnostics: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "query": self.query,
            "tracks": [track.to_dict() for track in self.tracks],
            "cache_hit": self.cache_hit,
            "diagnostics": list(self.diagnostics),
        }


class MusicSearchProvider(Protocol):
    @property
    def provider_name(self) -> str:
        ...

    def search_tracks(
        self,
        query: str,
        *,
        limit: int = 10,
        market: str | None = None,
    ) -> ProviderSearchResult:
        ...


class TrackMetadataProvider(Protocol):
    @property
    def provider_name(self) -> str:
        ...

    def get_track_metadata(self, query: TrackQuery) -> ProviderTrack:
        ...


class SimilarTracksProvider(Protocol):
    @property
    def provider_name(self) -> str:
        ...

    def get_similar_tracks(
        self,
        *,
        seed_track_ids: list[str] | None = None,
        seed_artists: list[str] | None = None,
        seed_genres: list[str] | None = None,
        limit: int = 10,
        market: str | None = None,
    ) -> ProviderSearchResult:
        ...


class SpotifySearchProvider:
    def __init__(
        self,
        collector: SpotifyCollector,
        *,
        request: Any = request_json,
    ) -> None:
        self.collector = collector
        self._request_json = request

    @property
    def provider_name(self) -> str:
        return "spotify"

    def search_tracks(
        self,
        query: str,
        *,
        limit: int = 10,
        market: str | None = None,
    ) -> ProviderSearchResult:
        if not query.strip():
            raise ValueError("query must be non-empty")
        resolved_limit = _bounded_limit(limit)
        params: dict[str, Any] = {
            "q": query,
            "type": "track",
            "limit": resolved_limit,
        }
        if market:
            params["market"] = market
        try:
            payload = self._request_json(
                f"{SPOTIFY_API_URL}/search?{urlencode(params)}",
                headers={
                    "Authorization": f"Bearer {self.collector._access_token()}"
                },
            )
        except Exception as error:
            raise ProviderError(f"Spotify search failed: {error}") from error
        items = payload.get("tracks", {}).get("items", [])
        if not isinstance(items, list):
            raise ProviderError("Spotify search returned malformed tracks")
        return ProviderSearchResult(
            provider=self.provider_name,
            query=query,
            tracks=[_spotify_track_to_provider_track(item) for item in items],
        )


class CollectorMetadataProvider:
    """Metadata provider backed by the existing Spotify/MB/Last.fm collectors."""

    def __init__(
        self,
        *,
        spotify: Any | None = None,
        musicbrainz: Any | None = None,
        lastfm: Any | None = None,
    ) -> None:
        self.spotify = spotify
        self.musicbrainz = musicbrainz
        self.lastfm = lastfm

    @property
    def provider_name(self) -> str:
        return "collector"

    def get_track_metadata(self, query: TrackQuery) -> ProviderTrack:
        if not query.title.strip() or not query.artist.strip():
            raise ValueError("title and artist are required")
        raw: dict[str, Any] = {}
        diagnostics: list[str] = []

        spotify_data: dict[str, Any] | None = None
        if self.spotify is not None:
            try:
                spotify_data = self.spotify.collect_track(
                    query.title,
                    query.artist,
                    query.album,
                )
                raw["spotify"] = spotify_data
            except Exception as error:
                diagnostics.append(f"spotify: {error}")

        musicbrainz_data: dict[str, Any] | None = None
        if self.musicbrainz is not None:
            try:
                musicbrainz_data = self.musicbrainz.collect_recording(
                    query.title,
                    query.artist,
                )
                raw["musicbrainz"] = musicbrainz_data
            except Exception as error:
                diagnostics.append(f"musicbrainz: {error}")

        tags: dict[str, float] = {}
        if self.lastfm is not None:
            try:
                lastfm_data = self.lastfm.collect_tags(query.title, query.artist)
                raw["lastfm"] = lastfm_data
                tags = _lastfm_tags_to_weights(lastfm_data)
            except Exception as error:
                diagnostics.append(f"lastfm: {error}")

        source = spotify_data or musicbrainz_data
        if source is None:
            raise ProviderError(
                "No metadata provider returned a track"
                + (": " + "; ".join(diagnostics) if diagnostics else "")
            )
        return _collector_track_to_provider_track(
            source,
            tags=tags,
            raw=raw,
            diagnostics=diagnostics,
        )


class ExternalMusicProvider:
    """Facade that combines search, metadata, and similar-track providers."""

    def __init__(
        self,
        *,
        search_providers: list[MusicSearchProvider] | None = None,
        metadata_provider: TrackMetadataProvider | None = None,
        similar_provider: SimilarTracksProvider | None = None,
    ) -> None:
        self.search_providers = list(search_providers or [])
        self.metadata_provider = metadata_provider
        self.similar_provider = similar_provider

    def search_tracks(
        self,
        query: str,
        *,
        limit: int = 10,
        market: str | None = None,
    ) -> list[ProviderSearchResult]:
        if not self.search_providers:
            raise ProviderError("No search providers are configured")
        return [
            provider.search_tracks(query, limit=limit, market=market)
            for provider in self.search_providers
        ]

    def get_track_metadata(self, query: TrackQuery) -> ProviderTrack:
        if self.metadata_provider is None:
            raise ProviderError("No metadata provider is configured")
        return self.metadata_provider.get_track_metadata(query)

    def get_similar_tracks(
        self,
        *,
        seed_track_ids: list[str] | None = None,
        seed_artists: list[str] | None = None,
        seed_genres: list[str] | None = None,
        limit: int = 10,
        market: str | None = None,
    ) -> ProviderSearchResult:
        if self.similar_provider is None:
            raise ProviderError("No similar-tracks provider is configured")
        return self.similar_provider.get_similar_tracks(
            seed_track_ids=seed_track_ids,
            seed_artists=seed_artists,
            seed_genres=seed_genres,
            limit=limit,
            market=market,
        )


def _bounded_limit(limit: int, *, maximum: int = 50) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise ValueError("limit must be an integer")
    if not 1 <= limit <= maximum:
        raise ValueError(f"limit must be between 1 and {maximum}")
    return limit


def _spotify_track_to_provider_track(track: dict[str, Any]) -> ProviderTrack:
    album = track.get("album", {})
    images = album.get("images", [])
    artists = [
        ProviderArtist(
            name=str(item.get("name")),
            provider_artist_id=(
                f"spotify:artist:{item.get('id')}" if item.get("id") else None
            ),
            external_urls=dict(item.get("external_urls", {})),
        )
        for item in track.get("artists", [])
        if item.get("name")
    ]
    release_date = str(album.get("release_date", ""))
    track_id = str(track.get("id") or "")
    return ProviderTrack(
        track_id=f"spotify:track:{track_id}" if track_id else "",
        provider="spotify",
        title=str(track.get("name") or ""),
        artist=artists[0].name if artists else "",
        artists=artists,
        album=album.get("name"),
        release_year=(
            int(release_date[:4])
            if len(release_date) >= 4 and release_date[:4].isdigit()
            else None
        ),
        duration_ms=track.get("duration_ms"),
        image_url=images[0].get("url") if images else None,
        preview_url=track.get("preview_url"),
        external_urls=dict(track.get("external_urls", {})),
        raw={"spotify": track},
    )


def _collector_track_to_provider_track(
    source: dict[str, Any],
    *,
    tags: dict[str, float],
    raw: dict[str, Any],
    diagnostics: list[str],
) -> ProviderTrack:
    source_name = str(source.get("source", "collector")).casefold()
    if "spotify_track_id" in source and source.get("spotify_track_id"):
        provider = "spotify"
        track_id = f"spotify:track:{source['spotify_track_id']}"
    elif (
        "musicbrainz_recording_id" in source
        and source.get("musicbrainz_recording_id")
    ):
        provider = "musicbrainz"
        track_id = f"musicbrainz:recording:{source['musicbrainz_recording_id']}"
    else:
        provider = source_name.replace(".", "")
        track_id = f"{provider}:track:{source.get('title', '')}"
    artists = [
        ProviderArtist(name=str(name))
        for name in source.get("artists", [])
        if name
    ]
    return ProviderTrack(
        track_id=track_id,
        provider=provider,
        title=str(source.get("title") or ""),
        artist=artists[0].name if artists else "",
        artists=artists,
        album=source.get("album"),
        release_year=source.get("release_year"),
        duration_ms=source.get("duration_ms"),
        tags=tags,
        raw={**raw, "diagnostics": list(diagnostics)},
    )


def _lastfm_tags_to_weights(lastfm_data: dict[str, Any]) -> dict[str, float]:
    tags = [
        *lastfm_data.get("track_tags", []),
        *lastfm_data.get("artist_tags", []),
    ]
    counts: dict[str, int] = {}
    for tag in tags:
        name = str(tag.get("name", "")).strip()
        if not name:
            continue
        try:
            count = int(tag.get("count", 0))
        except (TypeError, ValueError):
            count = 0
        counts[name] = max(counts.get(name, 0), count)
    maximum = max(counts.values(), default=0)
    if maximum <= 0:
        return {name: 0.0 for name in counts}
    return {name: count / maximum for name, count in counts.items()}
