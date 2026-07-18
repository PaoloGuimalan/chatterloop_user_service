import hmac

from django.conf import settings
from rest_framework.permissions import BasePermission


class AllowsInternalService(BasePermission):
    """
    Grants access to either (a) a normally-authenticated end user (existing
    x-access-token / request.user flow), or (b) a caller presenting a valid
    shared-secret internal-service header. Used so the Node chat server can
    call the link-preview endpoint server-to-server without a per-user token.
    """

    message = "Authentication or a valid internal-service secret is required."

    def has_permission(self, request, view):
        if request.user and request.user.is_authenticated:
            return True

        provided = request.headers.get("X-Internal-Service-Secret")
        expected = settings.INTERNAL_SERVICE_SECRET
        return bool(expected) and hmac.compare_digest(provided or "", expected)
