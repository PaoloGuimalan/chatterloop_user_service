from django.contrib import admin
from .models import TPAuthentication, PolicyDocument

admin.site.register(TPAuthentication)


@admin.register(PolicyDocument)
class PolicyDocumentAdmin(admin.ModelAdmin):
    list_display = ("document_type", "version", "document_url", "effective_date")
    list_filter = ("document_type",)
    ordering = ("-effective_date",)
