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
    # Rich-text (HTML) body of the policy. Preferred way to store a document so a
    # new version is just text, with no static file/PDF hosting involved.
    content = models.TextField(blank=True, default="")
    # Optional fallback: a full URL to an externally hosted document (e.g. a PDF
    # on another origin). Used only when `content` is empty.
    document_url = models.CharField(max_length=500, blank=True, default="")
    effective_date = models.DateTimeField(default=now)
    created_at = models.DateTimeField(default=now)

    class Meta:
        ordering = ["-effective_date"]

    def __str__(self):
        return f"{self.document_type} {self.version}"


class Variable(models.Model):
    """
    Platform settings that are tuned, not deployed: one row per key, the value
    a JSON document. Editable in the admin; readers cache it briefly, so a
    change takes effect within about a minute.

    Read directly by the Node server too (server/reusables/media/config.js)
    as `core_variable`, Django's own name for it.

    Never put a secret here - credentials stay in the environment.

    Keys in use:
      upload_limits    {feature: {maxMB, types}} - what each upload feature
                       accepts; clients fetch it on boot
      upload_transfer  {multipartThresholdMB, partSizeMB, concurrency} - how
                       clients send big files
    """

    key = models.CharField(max_length=100, primary_key=True)
    value = models.JSONField()
    description = models.TextField(blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.key
