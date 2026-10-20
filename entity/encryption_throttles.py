"""Shared mutation limits; a cache outage must not disable these limits."""
from django.conf import settings
from django.core.cache import caches
from rest_framework.exceptions import APIException
from rest_framework.throttling import SimpleRateThrottle


class MutationCacheUnavailable(APIException):
    status_code = 503
    default_detail = "Configuration changes are temporarily unavailable."
    default_code = "mutation_cache_unavailable"


class MutationThrottle(SimpleRateThrottle):
    def __init__(self):
        self.cache = caches[getattr(settings, "ENCRYPTION_MUTATION_CACHE_ALIAS", "encryption_mutations")]
        super().__init__()

    def allow_request(self, request, view):
        try:
            return super().allow_request(request, view)
        except Exception:
            raise MutationCacheUnavailable() from None


class AccountMutationThrottle(MutationThrottle):
    scope = "encryption_configuration_account"

    def get_rate(self):
        return getattr(settings, "ENCRYPTION_MUTATION_ACCOUNT_RATE", "60/hour")

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": str(request.user.pk)}


class IPMutationThrottle(MutationThrottle):
    scope = "encryption_configuration_ip"

    def get_rate(self):
        return getattr(settings, "ENCRYPTION_MUTATION_IP_RATE", "120/hour")

    def get_cache_key(self, request, view):
        # Forwarded addresses are trusted only if the deployment explicitly
        # guarantees its proxy replaces (rather than appends to) caller input.
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        ident = request.META.get("REMOTE_ADDR", "unknown")
        if getattr(settings, "ENCRYPTION_TRUST_PROXY_HEADERS", False) and forwarded:
            ident = forwarded.split(",", 1)[0].strip()
        return self.cache_format % {"scope": self.scope, "ident": ident}
