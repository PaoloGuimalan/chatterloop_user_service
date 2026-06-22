import uuid
from django.db import models
from django.utils.timezone import now


class TPAuthentication(models.Model):

    SERVICE_CHOICES = [
        ("google", "Google"),
    ]

    service_id = models.TextField(blank=False, null=False)
    service_type = models.CharField(max_length=150, null=False, choices=SERVICE_CHOICES)


class PolicyDocument(models.Model):

    DOCUMENT_TYPE_CHOICES = [
        ("terms", "Terms and Conditions"),
        ("privacy", "Privacy Policy"),
    ]

    id = models.CharField(
        max_length=150, default=uuid.uuid4, unique=True, primary_key=True
    )
    document_type = models.CharField(max_length=20, choices=DOCUMENT_TYPE_CHOICES)
    version = models.CharField(max_length=50)
    document_url = models.CharField(max_length=500)
    effective_date = models.DateTimeField(default=now)
    created_at = models.DateTimeField(default=now)

    class Meta:
        ordering = ["-effective_date"]

    def __str__(self):
        return f"{self.document_type} {self.version}"
