"""External music provider abstractions and adapters."""

from .music import (
    CollectorMetadataProvider,
    ExternalMusicProvider,
    LastfmSimilarArtistsProvider,
    MusicSearchProvider,
    ProviderArtist,
    ProviderError,
    ProviderSearchResult,
    ProviderSimilarArtist,
    ProviderSimilarArtistsResult,
    ProviderTrack,
    SimilarArtistsProvider,
    SimilarTracksProvider,
    SpotifySearchProvider,
    TrackMetadataProvider,
    TrackQuery,
)
from .factory import configured_music_provider_from_env

__all__ = [
    "CollectorMetadataProvider",
    "ExternalMusicProvider",
    "LastfmSimilarArtistsProvider",
    "MusicSearchProvider",
    "ProviderArtist",
    "ProviderError",
    "ProviderSearchResult",
    "ProviderSimilarArtist",
    "ProviderSimilarArtistsResult",
    "ProviderTrack",
    "SimilarArtistsProvider",
    "SimilarTracksProvider",
    "SpotifySearchProvider",
    "TrackMetadataProvider",
    "TrackQuery",
    "configured_music_provider_from_env",
]
