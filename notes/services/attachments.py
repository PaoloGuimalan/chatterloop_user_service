import logging
from django.db import transaction
from rest_framework.exceptions import APIException, ValidationError
from user.ext_models.mongomodels import UploadedFile
from notes.models import Attachment

logger = logging.getLogger(__name__)


class AttachmentUnavailable(APIException):
    status_code = 503
    default_detail = "Attachment service is unavailable. Please retry."


def resolve_note_upload(*, url, actor_account_id, entity_id):
    if not isinstance(url, str) or not url.strip():
        raise ValidationError({"attachments": "Send the URL returned by the completed upload"})
    try:
        record = UploadedFile.objects(version=2, fileDetails__data=url.strip()).first()
    except Exception as exc:
        logger.exception("Notes upload lookup failed")
        raise AttachmentUnavailable() from exc
    if (record is None or record.status not in ("ready", "attached")
            or record.purpose != "notes" or record.ownerAccount != str(actor_account_id)
            or record.ownerEntity != str(entity_id)):
        raise ValidationError({"attachments": "Use your completed Notes uploads for this entity"})
    return record


def prepare_attachments(*, note, requested, actor):
    existing = {str(item.id): item for item in note.attachments.all()}
    keep, add, seen = [], [], set()
    for item in requested:
        if "id" in item:
            retained = existing.get(str(item["id"]))
            if retained is None:
                raise ValidationError({"attachments": "Attachment does not belong to this note"})
            file_id = retained.file_id
            keep.append(retained)
        else:
            # Existing attachments may be retained by URL by another authorized manager.
            retained = next((a for a in existing.values() if a.url == item["url"]), None)
            if retained:
                file_id = retained.file_id
                keep.append(retained)
            else:
                upload = resolve_note_upload(url=item["url"], actor_account_id=actor.id, entity_id=note.entity_id)
                file_id = str(upload.fileID)
                add.append(upload)
        if file_id in seen:
            raise ValidationError({"attachments": "Duplicate attachments"})
        seen.add(file_id)
    keep_ids = {str(a.id) for a in keep}
    remove = [a for a in existing.values() if str(a.id) not in keep_ids]
    return keep, add, remove


def _attach(record, note_id):
    # Same best-effort marker convention as Diary; worker also reads SQL references.
    try:
        UploadedFile.objects(id=record.id).update_one(
            add_to_set__attachedTo={"type": "note", "id": str(note_id)}, set__status="attached")
    except Exception:
        logger.exception("Notes upload association marker failed")


def release_attachments(note_id, attachments):
    if not attachments:
        return
    from user_service.services.rabbitmq import RabbitMQClient, Queues
    RabbitMQClient.publish_on_commit(Queues.MEDIA_RELEASE, {
        "items": [{"target": {"type": "note", "id": str(note_id)},
                   "urls": sorted({a.url for a in attachments})}]
    })


def replace_note_attachments(*, note, requested, actor):
    _, add, remove = prepare_attachments(note=note, requested=requested, actor=actor)
    # Validate every new upload before making any SQL changes.
    for upload in add:
        Attachment.objects.create(note=note, file_id=str(upload.fileID), file_name=upload.name,
                                      file_type=upload.mime, url=upload.fileDetails["data"])
        transaction.on_commit(lambda record=upload: _attach(record, note.id))
    if remove:
        Attachment.objects.filter(note=note, id__in=[a.id for a in remove]).delete()
        release_attachments(note.id, remove)
