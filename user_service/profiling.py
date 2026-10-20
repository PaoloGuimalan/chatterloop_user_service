"""Keep operation credentials out of DEBUG request/SQL profiling storage."""
ENCRYPTION_API_PREFIX = "/api/entity/encryption-configurations/"


def should_profile_request(request):
    return not request.path_info.startswith(ENCRYPTION_API_PREFIX)
