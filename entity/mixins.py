"""DRF mixin to resolve the active acting-as entity for a request.

Apply to in-scope write views; after authentication runs, ``request.active_actor``
holds the validated :class:`entity.models.Entity` the caller is acting as (their
own user entity by default, or a realm they are authorized to operate).
"""

from entity.services import get_active_actor


class ActingAsMixin:
    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        # Runs after authentication/permission checks, so request.user is set.
        request.active_actor = get_active_actor(request)
