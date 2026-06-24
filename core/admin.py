from django import forms
from django.contrib import admin
from django.db import models
from .models import TPAuthentication, PolicyDocument

admin.site.register(TPAuthentication)


@admin.register(PolicyDocument)
class PolicyDocumentAdmin(admin.ModelAdmin):
    list_display = ("document_type", "version", "has_content", "document_url", "effective_date")
    list_filter = ("document_type",)
    ordering = ("-effective_date",)
    # Give the rich-text body a roomy editor in the admin.
    formfield_overrides = {
        models.TextField: {"widget": forms.Textarea(attrs={"rows": 30, "cols": 100})},
    }

    @admin.display(boolean=True, description="Has content")
    def has_content(self, obj):
        return bool(obj.content)
