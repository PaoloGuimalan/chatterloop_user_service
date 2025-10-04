from django.contrib import admin
from models import (
    Post,
    PostTag,
    PostPrivacy,
    PostReference,
    PreviewCount,
    MapView,
    Emoji,
    Reaction,
    Comment,
)

admin.site.register(Post)
admin.site.register(PostTag)
admin.site.register(PostPrivacy)
admin.site.register(PostReference)
admin.site.register(PreviewCount)
admin.site.register(MapView)
admin.site.register(Emoji)
admin.site.register(Reaction)
admin.site.register(Comment)
