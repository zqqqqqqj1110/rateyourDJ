"""L2 song profile schema, dictionary migration and persistence."""

from .models import (
    EXTERNAL_ID_FIELDS,
    METADATA_FIELDS,
    SOURCE_TAG_FIELDS,
    VERSION_TYPES,
    SongProfile,
    SongValidationError,
    empty_song_dict,
    song_schema,
    validate_song_patch,
)
from .matching import (
    SourceMatchError,
    classify_version,
    select_preferred_version,
)
from .merger import SongDataMerger
from .normalizers import GenreNormalizer
from .service import SongProfileService
from .store import JsonSongStore, SongNotFoundError
from .tools import (
    get_song_profile,
    get_song_schema,
    import_song_dictionary,
    merge_and_store_song,
    validate_song_dictionary,
)

__all__ = [
    "EXTERNAL_ID_FIELDS",
    "METADATA_FIELDS",
    "SOURCE_TAG_FIELDS",
    "VERSION_TYPES",
    "GenreNormalizer",
    "JsonSongStore",
    "SongNotFoundError",
    "SongProfile",
    "SongProfileService",
    "SongDataMerger",
    "SourceMatchError",
    "SongValidationError",
    "empty_song_dict",
    "get_song_profile",
    "get_song_schema",
    "import_song_dictionary",
    "merge_and_store_song",
    "classify_version",
    "select_preferred_version",
    "song_schema",
    "validate_song_dictionary",
    "validate_song_patch",
]
