"""L1 collection-profile schema, dictionary migration and persistence."""

from .models import (
    FEEDBACK_RECORD_FIELDS,
    FEEDBACK_TYPES,
    PREFERENCE_FIELDS,
    ProfileValidationError,
    UserProfile,
    empty_profile_dict,
    profile_schema,
    validate_profile_patch,
)
from .service import UserProfileService
from .store import JsonProfileStore, ProfileNotFoundError
from .tools import (
    get_profile_schema,
    get_user_profile,
    import_profile_dictionary,
    inspect_user_profile,
    validate_profile_dictionary,
)

__all__ = [
    "FEEDBACK_RECORD_FIELDS",
    "FEEDBACK_TYPES",
    "PREFERENCE_FIELDS",
    "JsonProfileStore",
    "ProfileNotFoundError",
    "ProfileValidationError",
    "UserProfile",
    "UserProfileService",
    "empty_profile_dict",
    "get_profile_schema",
    "get_user_profile",
    "import_profile_dictionary",
    "inspect_user_profile",
    "profile_schema",
    "validate_profile_dictionary",
    "validate_profile_patch",
]
