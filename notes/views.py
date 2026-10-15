from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.http import Http404
from rest_framework.views import APIView
from rest_framework.permissions import AllowAny
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError
from .models import Entry
from .permissions import OptionalNotesAuthentication, require_notes_access, can_read_note
from .serializers import NoteReadSerializer, NoteWriteSerializer, VersionSerializer, NoteReorderSerializer
from .services.notes import create_note, update_note, delete_note, reorder_notes


class NotePagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = "page_size"
    max_page_size = 100


class NotesView(APIView):
    authentication_classes = [OptionalNotesAuthentication]
    permission_classes = [AllowAny]

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response


class NoteListView(NotesView):
    def get(self, request):
        entity = require_notes_access(request)
        queryset = Entry.objects.filter(entity=entity).prefetch_related("attachments")
        search = request.query_params.get("search", "").strip()
        if search:
            queryset = queryset.filter(Q(title__icontains=search) | Q(search_text__icontains=search))
        paginator = NotePagination()
        page = paginator.paginate_queryset(queryset.order_by("-sort", "-id"), request, view=self)
        return paginator.get_paginated_response(NoteReadSerializer(page, many=True).data)

    def post(self, request):
        entity = require_notes_access(request)
        serializer = NoteWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if "version" in serializer.validated_data:
            raise ValidationError({"version": "Version is assigned by the server on creation"})
        note = create_note(entity=entity, actor=request.user, **serializer.validated_data)
        return Response(NoteReadSerializer(note).data, status=201)


class NoteDetailView(NotesView):
    def get(self, request, note_id):
        note = get_object_or_404(Entry.objects.prefetch_related("attachments"), id=note_id)
        if not can_read_note(request, note):
            raise Http404
        return Response(NoteReadSerializer(note).data)

    def patch(self, request, note_id):
        entity = require_notes_access(request)
        serializer = NoteWriteSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        changes = dict(serializer.validated_data)
        version = changes.pop("version", None)
        if version is None:
            raise ValidationError({"version": "Version is required"})
        if not changes:
            raise ValidationError("Include at least one change")
        note = update_note(note_id=note_id, entity=entity, expected_version=version, changes=changes, actor=request.user)
        return Response(NoteReadSerializer(note).data)

    def delete(self, request, note_id):
        entity = require_notes_access(request)
        serializer = VersionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        delete_note(note_id=note_id, entity=entity, expected_version=serializer.validated_data["version"])
        return Response(status=204)


class NoteReorderView(NotesView):
    def post(self, request):
        entity = require_notes_access(request)
        serializer = NoteReorderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response({"order": reorder_notes(entity=entity, **serializer.validated_data)})


