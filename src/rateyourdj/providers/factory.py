from __future__ import annotations

import os

from rateyourdj.collectors.lastfm import LastfmCollector
from rateyourdj.collectors.musicbrainz import MusicBrainzCollector
from rateyourdj.collectors.spotify import SpotifyCollector

from .music import (
    CollectorMetadataProvider,
    ExternalMusicProvider,
    SpotifySearchProvider,
)


def configured_music_provider_from_env() -> ExternalMusicProvider | None:
    spotify = _spotify_collector_from_env()
    lastfm = _lastfm_collector_from_env()
    musicbrainz = MusicBrainzCollector()

    search_providers = [SpotifySearchProvider(spotify)] if spotify else []
    metadata_provider = (
        CollectorMetadataProvider(
            spotify=spotify,
            musicbrainz=musicbrainz,
            lastfm=lastfm,
        )
        if spotify or lastfm
        else None
    )
    if not search_providers and metadata_provider is None:
        return None
    return ExternalMusicProvider(
        search_providers=search_providers,
        metadata_provider=metadata_provider,
    )


def _spotify_collector_from_env() -> SpotifyCollector | None:
    client_id = os.getenv("SPOTIFY_CLIENT_ID", "").strip()
    client_secret = os.getenv("SPOTIFY_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        return None
    return SpotifyCollector(client_id, client_secret)


def _lastfm_collector_from_env() -> LastfmCollector | None:
    api_key = os.getenv("LASTFM_API_KEY", "").strip()
    if not api_key:
        return None
    return LastfmCollector(api_key)
