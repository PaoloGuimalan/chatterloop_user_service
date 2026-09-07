from django.conf import settings
from django.http import JsonResponse


def version(request):
    """Unauthenticated version probe.

    Deliberately a plain Django view rather than a DRF one: REST_FRAMEWORK sets
    DEFAULT_PERMISSION_CLASSES to IsAuthenticated, so a DRF view here would 401
    and be useless for the deploy check this exists for.

    APP_VERSION comes from the image tag via the stack file, so a mismatch
    between this and the tag you deployed means the rollout did not take.
    """
    return JsonResponse(
        {
            "service": "user_service",
            "version": settings.APP_VERSION,
        }
    )
