"""
Files and the content that uses them - the Django half.

Deleting a post, comment or account here publishes ONE media_release job;
worker_service consumes it (internal/services/media) and decides per file: kept if anything live still uses it, held if the content was
reported, otherwise removed from storage. This module only says which
content went and which URLs it used.

Comments and diary entries carry files uploaded straight to storage (Node's
/media/uploads). resolve_own_upload checks each is the author's own finished
upload of the right purpose - nothing else is accepted - and attach_upload
records the content on it.
"""

import logging

from user.ext_models.mongomodels import UploadedFile
from user_service.services.rabbitmq import RabbitMQClient, Queues

from newsfeed.models import Comment, Post, PostReference

logger = logging.getLogger(__name__)


class UploadRejected(Exception):
    """Content named a file its author may not use."""


def _url(value):
    """The URL half of a stored value ("url%%%name" in old rows)."""
    return (value or "").split("%%%")[0].strip() if isinstance(value, str) else ""


def post_items(post_ids):
    """One release item per post: its media references and moment poster."""
    post_ids = [str(p) for p in post_ids or []]
    if not post_ids:
        return []
    urls = {pid: set() for pid in post_ids}
    for post_id, reference in PostReference.objects.filter(
        post_id__in=post_ids
    ).values_list("post_id", "reference"):
        url = _url(reference)
        if url.startswith("http"):
            urls[str(post_id)].add(url)
    for post_id, details in Post.objects.filter(post_id__in=post_ids).values_list(
        "post_id", "details"
    ):
        poster = ((details or {}).get("poster") or {}).get("url")
        if poster:
            urls[str(post_id)].add(poster)
    return [
        {"target": {"type": "post", "id": pid}, "urls": sorted(found)}
        for pid, found in urls.items()
        if found
    ]


def comment_items(comment_ids):
    """One release item per comment that had an attachment."""
    rows = Comment.objects.filter(
        comment_id__in=[str(c) for c in comment_ids or []],
        attachment__isnull=False,
    ).values_list("comment_id", "attachment")
    return [
        {"target": {"type": "comment", "id": str(cid)}, "urls": [_url(att)]}
        for cid, att in rows
        if _url(att).startswith("http")
    ]


def publish_media_release(items):
    """Publishes once the surrounding transaction commits. Never raises."""
    items = [item for item in items if item.get("urls")]
    if not items:
        return
    try:
        RabbitMQClient.publish_on_commit(Queues.MEDIA_RELEASE, {"items": items})
    except Exception:
        # A missed release leaves a file behind; the cleanup job finds it.
        logger.exception("media_release publish failed")


def resolve_own_upload(url, account_id, purpose):
    """
    The upload record behind `url`, checked: a file `account_id` uploaded
    through /media/uploads for `purpose`, and confirmed. Anything else - an
    external link, someone else's file, a file from the retired upload
    paths - raises UploadRejected.
    """
    if not isinstance(url, str) or not url.strip():
        raise UploadRejected("Files must be uploaded to Chatterloop first")
    record = UploadedFile.objects(version=2, fileDetails__data=url.strip()).first()
    if record is None:
        raise UploadRejected("Files must be uploaded to Chatterloop first")
    if record.ownerAccount != str(account_id):
        raise UploadRejected("You can only use files you uploaded")
    if record.status not in ("ready", "attached"):
        raise UploadRejected("That file isn't ready to use")
    if record.purpose != purpose:
        raise UploadRejected("That file was uploaded for something else")
    return record


def resolve_comment_upload(value, account_id):
    """
    The upload record behind a comment's ATTACHED file, checked - or None
    when the comment has no attachment. Links typed in the comment's text are
    a different field and never come through here.
    """
    if value is None or value == "":
        return None
    return resolve_own_upload(value, account_id, "comment")


def attach_upload(record, target_type, target_id):
    """Records the target as a user of the file. Never raises."""
    if record is None:
        return
    try:
        UploadedFile.objects(id=record.id).update_one(
            add_to_set__attachedTo={"type": target_type, "id": str(target_id)},
            set__status="attached",
        )
    except Exception:
        logger.exception("attaching an upload failed")
