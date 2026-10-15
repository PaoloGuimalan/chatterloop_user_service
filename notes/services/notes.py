from django.db import transaction
from django.db.models import Max
from django.http import Http404
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import APIException, ValidationError
from entity.models import Entity
from notes.models import Entry
from .content import normalize_note_fields


class VersionConflict(APIException):
    status_code = 409
    default_detail = "This note changed. Reload the latest version before saving."


def check_version(note, expected_version):
    if type(expected_version) is not int or expected_version < 1:
        raise ValidationError({"version": "A positive integer version is required"})
    if note.version != expected_version:
        raise VersionConflict()


@transaction.atomic
def create_note(*, entity, title="", content="", is_private=True, attachments=None, actor):
    from .attachments import replace_note_attachments
    try:
        html, plain = normalize_note_fields(title, content)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    # Lock a row that exists even for an empty notebook. Locking only notes
    # would allow two first creates to allocate the same position.
    Entity.objects.select_for_update().get(pk=entity.pk)
    maximum = Entry.objects.filter(entity=entity).aggregate(max_sort=Max("sort"))["max_sort"]
    note = Entry.objects.create(entity=entity, title=title, content=html, search_text=plain,
                                is_private=is_private, sort=(maximum or 0) + 1)
    if attachments is not None:
        replace_note_attachments(note=note, requested=attachments, actor=actor)
    return note


@transaction.atomic
def update_note(*, note_id, entity, expected_version, changes, actor):
    from .attachments import replace_note_attachments
    note = get_object_or_404(Entry.objects.select_for_update(), id=note_id, entity=entity)
    check_version(note, expected_version)
    try:
        html, plain = normalize_note_fields(changes.get("title", note.title), changes.get("content", note.content))
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    for key in ("title", "is_private"):
        if key in changes:
            setattr(note, key, changes[key])
    if "content" in changes:
        note.content, note.search_text = html, plain
    if "attachments" in changes:
        replace_note_attachments(note=note, requested=changes["attachments"], actor=actor)
    note.version += 1
    # A concurrent reorder owns sort. Content edits never write that column.
    note.save(update_fields=["title", "content", "search_text", "is_private", "version", "updated_at"])
    return note


@transaction.atomic
def reorder_notes(*, entity, note_id, before_id):
    """Move one note within the complete notebook, independently of filters."""
    Entity.objects.select_for_update().get(pk=entity.pk)
    notes = list(Entry.objects.filter(entity=entity).select_for_update().order_by("-sort", "-id"))
    by_id = {str(note.id): note for note in notes}
    moved = by_id.get(str(note_id))
    target = by_id.get(str(before_id)) if before_id is not None else None
    if moved is None or (before_id is not None and target is None):
        raise Http404
    if moved is not target:
        notes.remove(moved)
        notes.insert(notes.index(target) if target is not None else len(notes), moved)
    for index, note in enumerate(notes):
        note.sort = len(notes) - index
    # bulk_update intentionally leaves content versions and timestamps alone.
    Entry.objects.bulk_update(notes, ["sort"])
    return [{"id": str(note.id), "sort": note.sort} for note in notes]


@transaction.atomic
def delete_note(*, note_id, entity, expected_version):
    from .attachments import release_attachments
    note = get_object_or_404(Entry.objects.select_for_update(), id=note_id, entity=entity)
    check_version(note, expected_version)
    release_attachments(note.id, list(note.attachments.all()))
    note.delete()
