"""Owner-only post mutations. Content, audience and archive are separate writes."""
import logging
from datetime import timedelta

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils.timezone import now
from rest_framework.exceptions import APIException, ValidationError

from entity.ownership import assert_owns
from interests.services.hashtags import save_post_hashtags
from newsfeed.models import Post, PostKind, PostReference
from newsfeed.services import link_preview
from newsfeed.services.media_release import (
    UploadRejected, attach_upload, publish_media_release, resolve_own_upload,
)


EDITABLE_POST_FIELDS = frozenset({"caption", "references", "privacy_status", "is_archived"})
CONTENT_FIELDS = frozenset({"caption", "references"})
FIXED_MEDIA_TYPES = frozenset({"profile", "cover_photo", "shared_post"})
logger = logging.getLogger(__name__)


class AttachmentUnavailable(APIException):
    status_code = 503
    default_detail = "Attachment service is unavailable. Please retry."


def validate_fields(fields):
    if not isinstance(fields, dict):
        raise ValidationError({"fields": "Send an object of fields to update"})
    disallowed = set(fields) - EDITABLE_POST_FIELDS
    if disallowed:
        raise ValidationError({"fields": f"Field(s) not editable: {', '.join(sorted(disallowed))}"})
    if "caption" in fields and not isinstance(fields["caption"], str):
        raise ValidationError({"caption": "Caption must be text"})
    if "is_archived" in fields and type(fields["is_archived"]) is not bool:
        raise ValidationError({"is_archived": "Send true or false"})
    if "privacy_status" in fields and not isinstance(fields["privacy_status"], str):
        raise ValidationError({"privacy_status": "Choose a supported audience"})
    groups = [bool(CONTENT_FIELDS.intersection(fields)), "privacy_status" in fields, "is_archived" in fields]
    if sum(groups) > 1:
        raise ValidationError({"fields": "Save content, audience and archive changes separately"})
    return fields


def prepare_references(post, requested, account_id, existing):
    if not isinstance(requested, list):
        raise ValidationError({"references": "Send the final list of attachments"})
    by_id = {item.reference_id: item for item in existing}
    kept, added, seen_ids, seen_urls = [], [], set(), set()
    fixed = post.content_type in FIXED_MEDIA_TYPES or post.is_shared
    for item in requested:
        if not isinstance(item, dict) or set(item) not in ({"reference_id"}, {"url"}):
            raise ValidationError({"references": "Keep an attachment by reference_id, or add a completed upload by url"})
        if "reference_id" in item:
            reference_id = item["reference_id"]
            if not isinstance(reference_id, str) or reference_id not in by_id:
                raise ValidationError({"references": "Attachment does not belong to this post"})
            if reference_id in seen_ids:
                raise ValidationError({"references": "Duplicate attachment"})
            seen_ids.add(reference_id)
            kept.append(by_id[reference_id])
            seen_urls.add(by_id[reference_id].reference)
        else:
            if fixed or post.content_type not in ("text", "media"):
                raise ValidationError({"references": "Attachments cannot be changed on this post"})
            try:
                upload = resolve_own_upload(item["url"], account_id, "post_media")
            except UploadRejected as exc:
                raise ValidationError({"references": str(exc)}) from exc
            except Exception as exc:
                raise AttachmentUnavailable() from exc
            if upload.status != "ready":
                raise ValidationError({"references": "Add a new upload; existing attachments are kept by reference_id"})
            if upload.ownerEntity != str(post.entity_id):
                raise ValidationError({"references": "Use uploads made for this posting identity"})
            if not (upload.mime or "").startswith(("image/", "video/")):
                raise ValidationError({"references": "Post attachments must be photos or videos"})
            url = upload.fileDetails["data"]
            if url in seen_urls or any(reference.reference == url for reference in existing):
                raise ValidationError({"references": "Duplicate attachment; keep existing files by reference_id"})
            seen_urls.add(url)
            added.append(upload)
    removed = [item for item in existing if item.reference_id not in seen_ids]
    if fixed and (removed or added):
        raise ValidationError({"references": "Profile, cover and shared post attachments cannot be changed"})
    return kept, added, removed


@transaction.atomic
def update_post(*, request, post_id, fields):
    fields = validate_fields(fields)
    if not isinstance(post_id, str) or not post_id.strip():
        raise ValidationError({"post_id": "A post_id is required"})
    post = get_object_or_404(Post.objects.select_for_update(), post_id=post_id, deleted_at=None)
    assert_owns(request, post)
    if post.on_feed != PostKind.FEED and (CONTENT_FIELDS.intersection(fields) or "privacy_status" in fields):
        raise ValidationError({"fields": "Use the moment or thought editor for this post"})

    original_caption = post.caption or ""
    caption = fields.get("caption", original_caption)
    if not caption.strip():
        caption = ""
    existing = list(post.references.order_by("date_posted", "reference_id"))
    kept, added, removed = existing, [], []
    if "references" in fields:
        kept, added, removed = prepare_references(post, fields["references"], str(request.user.id), existing)
    if CONTENT_FIELDS.intersection(fields) and not caption.strip() and not kept and not added:
        raise ValidationError({"caption": "Add a caption or an attachment to keep this post"})

    changes = {}
    if "caption" in fields and caption != original_caption:
        changes["caption"] = caption
    if "privacy_status" in fields:
        audience = fields["privacy_status"]
        # Preserve legacy custom audiences; creating/editing an allow-list is a
        # separate feature. Never coerce an unsupported value to Public.
        if audience not in ("public", "connections", "private") and not (audience == "custom" == post.privacy_status):
            raise ValidationError({"privacy_status": "Choose Public, Contacts only, or Only me"})
        if audience != post.privacy_status:
            changes["privacy_status"] = audience
    if "is_archived" in fields and fields["is_archived"] != post.is_archived:
        changes["is_archived"] = fields["is_archived"]

    content_changed = "caption" in changes or bool(added or removed)
    if content_changed:
        changes["updated_at"] = now()
        if post.content_type in ("text", "media") and not post.is_shared:
            media_type = "media" if kept or added else "text"
            changes.update(content_type=media_type, file_type=media_type)

    # All validation is complete before changing SQL or scheduling side effects.
    if removed:
        PostReference.objects.filter(post=post, reference_id__in=[item.reference_id for item in removed]).delete()
    reference_date = max(
        [changes.get("updated_at", post.date_posted)]
        + [item.date_posted + timedelta(microseconds=1) for item in existing]
    )
    for index, upload in enumerate(added):
        PostReference.objects.create(
            post=post, reference=upload.fileDetails["data"], caption="",
            reference_name=upload.name, reference_media_type=upload.mime,
            date_posted=reference_date + timedelta(microseconds=index),
        )
        transaction.on_commit(lambda record=upload: attach_upload(record, "post", post.post_id))
    if changes:
        # QuerySet.update deliberately bypasses Post.save's generic timestamp:
        # only the content branch above may supply updated_at.
        Post.objects.filter(pk=post.pk).update(**changes)
        for key, value in changes.items():
            setattr(post, key, value)
    if "caption" in changes:
        # Explicit links can also come from comments, so only add the newly
        # declared hashtags here.
        save_post_hashtags(post)
    if removed:
        publish_media_release([{"target": {"type": "post", "id": str(post.pk)},
                                "urls": sorted({item.reference for item in removed})}])
    # Capture membership while holding the row lock, so response construction
    # after commit cannot mix this caption with a subsequent edit's media.
    post._edit_references = list(post.references.order_by("date_posted", "reference_id"))
    return post, bool(changes)


def post_patch(post):
    """Viewer-independent canonical fields, merged onto existing client state."""
    from newsfeed.serializers import PostReferenceSerializer
    url = link_preview.extract_first_url(post.caption or "")
    preview = None
    if url:
        try:
            preview = link_preview.get_preview(url)
        except Exception:
            logger.exception("Link preview unavailable for edited post %s", post.pk)
    return {
        "post_id": post.post_id, "caption": post.caption or "",
        "updated_at": post.updated_at.isoformat() if post.updated_at else None,
        "is_edited": post.is_edited, "privacy_status": post.privacy_status,
        "is_archived": post.is_archived, "date_posted": post.date_posted.isoformat(),
        "content_type": post.content_type, "file_type": post.file_type,
        "references": PostReferenceSerializer(post._edit_references, many=True).data,
        "link_preview": preview,
    }
