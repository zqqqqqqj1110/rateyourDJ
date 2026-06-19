"""External music provider abstractions and adapters."""

from .music import (
    CollectorMetadataProvider,
    ExternalMusicProvider,
    MusicSearchProvider,
    ProviderArtist,
    ProviderError,
    ProviderSearchResult,
    ProviderTrack,
    SimilarTracksProvider,
    SpotifySearchProvider,
    TrackMetadataProvider,
    TrackQuery,
)
from .factory import configured_music_provider_from_env

__all__ = [
    "CollectorMetadataProvider",
    "ExternalMusicProvider",
    "MusicSearchProvider",
    "ProviderArtist",
    "ProviderError",
    "ProviderSearchResult",
    "ProviderTrack",
    "SimilarTracksProvider",
    "SpotifySearchProvider",
    "TrackMetadataProvider",
    "TrackQuery",
    "configured_music_provider_from_env",
]
