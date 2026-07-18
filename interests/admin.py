from django.contrib import admin

from .models import (
    Interest,
    EntityInterest,
    EntityInterestAffinity,
    InterestTrendingScore,
    EntryTagLink,
    PostInterestLink,
)

class InterestAdmin(admin.ModelAdmin):
    """
    Interests are shared, globally-referenced vocabulary - deleting one
    cascades across every entity's diary entries, affinity scores, overrides,
    and trending data platform-wide, not just the current user's. Tags
    should only ever be unlinked from a specific entry (delete the
    EntryTagLink row), never deleted here.
    """

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register(Interest, InterestAdmin)
admin.site.register(EntityInterest)
admin.site.register(EntityInterestAffinity)
admin.site.register(InterestTrendingScore)
admin.site.register(EntryTagLink)
admin.site.register(PostInterestLink)
