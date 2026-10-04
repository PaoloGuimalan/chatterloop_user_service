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


class Update(models.Model):
    """
    A client release that older installs should be told about. Clients ask
    /api/user/system-update with their platform and build; see pending_for.

    Compared by `build`, never `version`: build is the integer the stores force
    up on every upload (Android versionCode / iOS CFBundleVersion - the mobile
    app's AppVersion.build), while version is a display string that can move
    in any direction.
    """

    PLATFORM_CHOICES = [
        ("android", "Android"),
        ("ios", "iOS"),
        ("web", "Web"),
    ]

    SEVERITY_CHOICES = [
        # The client may dismiss it.
        ("optional", "Optional"),
        # The client blocks until updated.
        ("required", "Required"),
    ]

    platform = models.CharField(max_length=20, choices=PLATFORM_CHOICES)
    # Shown to the user, e.g. "1.2.0".
    version = models.CharField(max_length=50)
    build = models.PositiveIntegerField()
    severity = models.CharField(
        max_length=20, choices=SEVERITY_CHOICES, default="optional"
    )
    title = models.CharField(max_length=150, blank=True, default="")
    # What changed, shown in the banner. Plain text.
    details = models.TextField(blank=True, default="")
    # Where "Update" goes. Blank: Android opens its Play listing by package
    # name; iOS (App Store id) and web need it set.
    store_url = models.CharField(max_length=500, blank=True, default="")
    # Off pulls a release (a bad build, a rollout put on hold) without losing
    # its row.
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=now)

    class Meta:
        ordering = ["platform", "-build"]
        constraints = [
            models.UniqueConstraint(
                fields=["platform", "build"], name="unique_update_build"
            ),
        ]

    def __str__(self):
        return f"{self.platform} {self.version} ({self.build}, {self.severity})"

    @classmethod
    def pending_for(cls, platform, build):
        """
        The update a client on `build` should be offered, or None.

        Describes the LATEST release, but is required when ANY release newer
        than the client is - so a user several versions behind cannot skip
        past a required one just because the newest happens to be optional.
        """
        newer = cls.objects.filter(
            platform=platform, is_active=True, build__gt=build
        )
        latest = newer.order_by("-build").first()
        if latest is None:
            return None
        required = newer.filter(severity="required").exists()
        return {
            "severity": "required" if required else "optional",
            "version": latest.version,
            "build": latest.build,
            "title": latest.title,
            "details": latest.details,
            "store_url": latest.store_url,
        }
