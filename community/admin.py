from django.contrib import admin
from .models import Realm, Member, Invite

admin.site.register(Realm)
admin.site.register(Member)
admin.site.register(Invite)
