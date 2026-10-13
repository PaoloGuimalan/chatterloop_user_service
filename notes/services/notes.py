from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import APIException, ValidationError
from notes.models import Entry
from .content import normalize_note_content


class VersionConflict(APIException):
    status_code = 409
    default_detail = "This note changed. Reload the latest version before saving."


def check_version(note, expected_version):
    if type(expected_version) is not int or expected_version < 1:
        raise ValidationError({"version": "A positive integer version is required"})
    if note.version != expected_version:
        raise VersionConflict()


@transaction.atomic
def create_note(*, entity, title, content, is_private=True, attachments=None, actor):
    from .attachments import replace_note_attachments
    html, plain = normalize_note_content(content)
    note = Entry.objects.create(entity=entity, title=title, content=html, search_text=plain, is_private=is_private)
    if attachments is not None:
        replace_note_attachments(note=note, requested=attachments, actor=actor)
    return note


@transaction.atomic
def update_note(*, note_id, entity, expected_version, changes, actor):
    from .attachments import replace_note_attachments
    note = get_object_or_404(Entry.objects.select_for_update(), id=note_id, entity=entity)
    check_version(note, expected_version)
    for key in ("title", "is_private"):
        if key in changes:
            setattr(note, key, changes[key])
    if "content" in changes:
        note.content, note.search_text = normalize_note_content(changes["content"])
    if "attachments" in changes:
        replace_note_attachments(note=note, requested=changes["attachments"], actor=actor)
    note.version += 1
    note.save()
    return note


@transaction.atomic
def delete_note(*, note_id, entity, expected_version):
    from .attachments import release_attachments
    note = get_object_or_404(Entry.objects.select_for_update(), id=note_id, entity=entity)
    check_version(note, expected_version)
    release_attachments(note.id, list(note.attachments.all()))
    note.delete()
