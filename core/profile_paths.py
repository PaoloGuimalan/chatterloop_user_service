"""Authoritative profile namespace policy shared by native and web writers."""
import unicodedata
from django.core.exceptions import AppRegistryNotReady, ImproperlyConfigured
from django.db import DatabaseError

# Seed defaults used only when the Variable is unavailable; no runtime JSON file.
RESERVED_PROFILE_PATHS = frozenset([
    "api", "assets", "conference", "contacts", "delete-account", "design",
    "edits", "explore", "invite", "login", "mapfeed", "messages", "moderation",
    "moments", "notes", "notifications", "pages", "post", "privacy", "realms",
    "register", "scenes", "servers", "settings", "setup", "share", "studio",
    "studios", "support", "terms", "topics", "user", "verification",
])
RESERVED_PATH_MESSAGE = "That link name is reserved by Chatterloop. Choose another name."
VARIABLE_KEY = "reserved_profile_paths"


class ProfilePathError(ValueError):
    def __init__(self, field, code="RESERVED_PATH", message=RESERVED_PATH_MESSAGE):
        super().__init__(message)
        self.payload = {"status": False, "code": code, "field": field, "message": message}


def normalized_profile_path(value):
    # Match the JavaScript clients, including Unicode case variants such as
    # Greek final sigma. Explicit trimming matches ECMAScript String.trim.
    trim_chars = " \t\n\r\v\f\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
    return unicodedata.normalize("NFKC", value).strip(trim_chars).upper().lower().replace("ß", "ss")


def parse_reserved_profile_paths(value):
    """Validate the whole operator setting rather than silently dropping entries."""
    if not isinstance(value, dict) or not isinstance(value.get("paths"), list):
        raise ValueError('Use a JSON object with a "paths" list of single link names.')
    paths = set()
    for path in value["paths"]:
        if not isinstance(path, str):
            raise ValueError("Every reserved path must be a string.")
        normalized = normalized_profile_path(path)
        if (not normalized or normalized in (".", "..") or any(c in normalized for c in "/\\%?#")
                or any(ord(c) < 32 or ord(c) == 127 for c in path)):
            raise ValueError("Reserved paths must be single link names without separators, escapes, or controls.")
        paths.add(normalized)
    return frozenset(paths)


def get_reserved_profile_paths():
    # Name changes are infrequent: read on validation so admin edits apply
    # immediately across processes, without a cache invalidation mechanism.
    try:
        from core.models import Variable
        value = Variable.objects.filter(key=VARIABLE_KEY).values_list("value", flat=True).first()
        return parse_reserved_profile_paths(value)
    except (ValueError, DatabaseError, AppRegistryNotReady, ImproperlyConfigured):
        # Before migration, missing/malformed row, or database unavailable.
        return RESERVED_PROFILE_PATHS


def validate_profile_path(value, field="username", reserved_paths=None):
    # Null/empty realm slugs fall back to their stable numeric realm ID.
    if value is None or value == "":
        return
    normalized = normalized_profile_path(value) if isinstance(value, str) else ""
    if (not isinstance(value, str) or any(ord(c) < 32 or ord(c) == 127 for c in value)
            or any(c in normalized for c in "/\\%?#") or normalized in (".", "..")):
        raise ProfilePathError(field, "INVALID_PROFILE_PATH", "Use a link name without URL separators or escapes.")
    paths = get_reserved_profile_paths() if reserved_paths is None else reserved_paths
    if normalized in paths:
        raise ProfilePathError(field)


def validate_model_profile_path(instance, field, update_fields=None):
    """Reject new claims; do not strand legacy collisions during unrelated saves."""
    if update_fields is not None and field not in update_fields:
        return
    value = getattr(instance, field)
    try:
        validate_profile_path(value, field)
    except ProfilePathError:
        if not instance._state.adding:
            previous = type(instance)._default_manager.filter(pk=instance.pk).values_list(field, flat=True).first()
            if previous == value:
                return
        raise


def clean_model_profile_path(instance, field):
    from django.core.exceptions import ValidationError

    try:
        validate_model_profile_path(instance, field)
    except ProfilePathError as error:
        raise ValidationError({field: error.payload["message"]}, code=error.payload["code"]) from error
