from django.contrib import admin
from .models import Account, Verification, Connection

admin.site.register(Account)
admin.site.register(Verification)
admin.site.register(Connection)
