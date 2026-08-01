from django.shortcuts import render
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.db.models import Count, Q, OuterRef, Subquery, Value, FloatField
from django.db.models.functions import Coalesce
from django.db import transaction
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.pagination import PageNumberPagination
from .models import Entry, Mood, Attachment
from interests.models import Interest, EntityInterestAffinity
from interests.services.affinity import bump_interest_affinity
from interests.services.interest_resolver import ensure_grant_override
from interests.views import InterestListView
from user.models import Account
from django.shortcuts import get_object_or_404
from .serializers import EntrySerializer, TagSerializer, MoodSerializer
from django.utils import timezone
from datetime import datetime
from entity.services.follows import get_profile_relationship_state


class Pagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = "page_size"


class DiaryTotalView(APIView):

    def get_permissions(self):
        if self.request.method in ["GET"]:
            return [AllowAny()]
        return super().get_permissions()

    def get_authenticators(self):
        """
        VIEW LEVEL FIX: If a guest visits without an 'x-access-token' header,
        completely strip the authentication class out of this execution thread.
        This stops the custom backend from throwing "Token not defined" errors!
        """
        if self.request.method in ["GET"]:
            token = self.request.headers.get("x-access-token")
            if not token:
                return ()  # Returns an empty tuple, bypassing your custom backend entirely for guests!

        return super().get_authenticators()

    pagination_class = Pagination

    def get(self, request, username):
        user = self.request.user
        entity = getattr(self.request, "entity", None)

        try:
            current_user = Account.objects.get(username=username)

            state = get_profile_relationship_state(entity, current_user.entity)
            can_view = state["can_view"]

            if not can_view:
                return Response(
                    {
                        "user": current_user.username,
                        "total_entries": 0,
                        "latest_entry": None,
                        "top_tags": [],
                    },
                    status=status.HTTP_200_OK,
                )

            limit_tags = 3
            query_set = Entry.objects.filter(account=current_user)

            # Pull just the latest entry's date with a single LIMIT 1 query
            # (only the date column) instead of serializing every entry — the
            # full serialization here was unused and caused an N+1 over each
            # entry's mood/map/attachments/tags relations.
            latest_entry_date = (
                query_set.order_by("-entry_date", "-created_at")
                .values_list("entry_date", flat=True)
                .first()
            )

            # Ranked by this entity's personal interest affinity (bumped
            # every time a diary entry is tagged, see bump_interest_affinity
            # in EntrySerializer._handle_tags / DiaryCRUDView.post) rather
            # than raw diary tag-usage count - "top tags" now means "tags
            # this entity actually cares about most", with entry_count only
            # as a tie-break for interests never bumped (e.g. entries tagged
            # before this ranking existed).
            personal_score_subquery = EntityInterestAffinity.objects.filter(
                entity_id=current_user.entity_id, interest_id=OuterRef("pk")
            ).values("score")[:1]

            top_tags = (
                Interest.objects.filter(entries__account=current_user)
                .annotate(
                    personal_score=Coalesce(
                        Subquery(personal_score_subquery, output_field=FloatField()),
                        Value(0.0),
                    ),
                    entry_count=Count("entries"),
                )
                .distinct()
                .order_by("-personal_score", "-entry_count")[:limit_tags]
            )

            serialized_tags = TagSerializer(top_tags, many=True).data

            return Response(
                {
                    "user": current_user.username,
                    "total_entries": query_set.count(),
                    "latest_entry": latest_entry_date,
                    "top_tags": serialized_tags,
                },
                status=status.HTTP_200_OK,
            )
        except Exception as ex:
            return Response(str(ex), status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class DiaryListView(APIView):
    permission_classes = [IsAuthenticated]
    pagination_class = Pagination

    def get(self, request):
        user = self.request.user

        try:
            queryset = (
                Entry.objects.select_related("entry_map_info", "mood")
                .prefetch_related("attachments")
                .filter(account=user)
                .order_by("-created_at")
            )

            paginator = self.pagination_class()
            paginated_queryset = paginator.paginate_queryset(
                queryset, request, view=self
            )

            serialized_result = EntrySerializer(paginated_queryset, many=True)
            data = paginator.get_paginated_response(serialized_result.data)

            return data

        except Exception as ex:
            return Response(str(ex), status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class MoodListView(APIView):
    permission_classes = [IsAuthenticated]
    pagination_class = Pagination

    def get(self, request):
        user = self.request.user

        try:
            queryset = Mood.objects.all().order_by("id")

            paginator = self.pagination_class()
            paginated_queryset = paginator.paginate_queryset(
                queryset, request, view=self
            )

            serialized_result = MoodSerializer(paginated_queryset, many=True)
            data = paginator.get_paginated_response(serialized_result.data)

            return data

        except Exception as ex:
            return Response(str(ex), status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class TagListView(InterestListView):
    """
    Thin alias so /api/diary/tags/ keeps working unchanged now that the
    search/autocomplete logic lives on the shared, system-wide
    InterestListView (interests/views.py). Also fixes a pre-existing
    inconsistency: this used to check "is_new" via an exact, case-sensitive
    name match, while InterestListView checks case/whitespace-insensitively
    against normalized_name - the same drift this whole move was meant to
    clean up.
    """

    pass


class DiaryCRUDView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, entry_id):
        user = self.request.user

        try:
            queryset = Entry.objects.select_related("entry_map_info").prefetch_related(
                "attachments"
            )
            final_query = get_object_or_404(
                queryset, Q(account=user) | Q(is_private=False), id=entry_id
            )

            serialized_response = EntrySerializer(final_query)

            return Response(serialized_response.data, status=status.HTTP_200_OK)
        except Exception as ex:
            return Response(str(ex), status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def post(self, request):
        user = self.request.user

        try:
            title = request.data.get("title")
            content = request.data.get("content")
            mood = request.data.get("mood")
            tags = request.data.get("tags")
            entry_date = request.data.get("entry_date")
            is_private = request.data.get("is_private")
            attachments = request.data.get("attachments")

            date_part = entry_date.split(" ")[0]  # "2026-01-17"
            dt = timezone.make_aware(datetime.strptime(date_part, "%Y-%m-%d"))
            formatted_entry_date = dt.strftime("%Y-%m-%d")

            if not (
                title and title.strip() != "" and content and content.strip() != ""
            ):
                return Response(
                    {"message": "Title or Content is empty or invalid"},
                    status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                )

            with transaction.atomic():
                # Both branches resolve through the same canonical,
                # race-safe, case/whitespace-insensitive lookup now -
                # previously this used a separate bulk_create(
                # ignore_conflicts=True) path with weaker dedup than
                # EntrySerializer's get_or_create, and trusted the client's
                # is_new flag even though it can be stale by request time.
                combined_tags = [
                    Interest.objects.get_or_create_by_name(tag["name"])[0]
                    for tag in tags
                ]

                entry_mood = None

                if mood:
                    entry_mood = Mood.objects.get(id=mood["id"])

                queryset = Entry.objects.create(
                    title=title,
                    content=content,
                    mood=entry_mood,
                    entry_date=formatted_entry_date,
                    is_private=is_private,
                    account=user,
                )

                queryset.tags.set(combined_tags)

                if combined_tags:
                    tag_ids = [tag.id for tag in combined_tags]
                    bump_interest_affinity(user.entity_id, tag_ids, "DIARY_TAG", False)
                    ensure_grant_override(user.entity_id, tag_ids)

                final_id = queryset.id

                created_entry_queryset = Entry.objects.select_related(
                    "entry_map_info"
                ).prefetch_related("attachments")
                final_query = get_object_or_404(
                    created_entry_queryset,
                    Q(account=user) | Q(is_private=False),
                    id=final_id,
                )

                if attachments and len(attachments) > 0:
                    for attachment in attachments:
                        Attachment.objects.create(
                            entry=queryset,
                            file_id=attachment["file_id"],
                            file_name=attachment["file_name"],
                            file_type=attachment["file_type"],
                            url=attachment["url"],
                        )

                serialized_response = EntrySerializer(final_query)

                return Response(serialized_response.data, status=status.HTTP_200_OK)
        except Exception as ex:
            return Response(str(ex), status=status.HTTP_500_INTERNAL_SERVER_ERROR)
