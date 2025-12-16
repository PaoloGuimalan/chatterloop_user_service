from django.contrib import admin
from .models import Entry, Attachment, Tag

admin.site.register(Entry)
admin.site.register(Attachment)
admin.site.register(Tag)


