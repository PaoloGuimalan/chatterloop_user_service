from django.contrib import admin
from .models import Entry, Attachment, MapView, Mood

admin.site.register(Entry)
admin.site.register(Attachment)
admin.site.register(Mood)
admin.site.register(MapView)
