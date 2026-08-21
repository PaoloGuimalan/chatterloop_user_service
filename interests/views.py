from django.shortcuts import get_object_or_404
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied
from rest_framework.pagination import PageNumberPagination

from .models import EntityInterest, EntityInterestAffinity, Interest, InterestTrendingScore
from entity.permissions import PermissionEffect
import logging

logger = logging.getLogger(__name__)


class Pagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = "page_size"


class InterestListView(APIView):
    """
    Search/autocomplete over the shared interest vocabulary - generalized
    version of diary.views.TagListView (which now delegates here so
    /api/diary/tags/ keeps working unchanged).
    """

    permission_classes = [IsAuthenticated]
    pagination_class = Pagination

    def get(self, request):
        try:
            search = request.query_params.get("search", None)
            queryset = Interest.objects.all().order_by("id")

            if search:
                queryset = queryset.filter(name__icontains=search)

            paginator = self.pagination_class()
            paginated_queryset = paginator.paginate_queryset(queryset, request, view=self)

            results = [{"id": i.id, "name": i.name} for i in paginated_queryset]
            is_new = bool(search and search.strip()) and not Interest.objects.filter(
                normalized_name=search.strip().lower()
            ).exists()

            return paginator.get_paginated_response({"list": results, "is_new": is_new})
        except Exception as e:
            logger.exception("InterestListView.get failed")
            return Response(
                {"status": False, "message": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class EntityInterestOverrideListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            entity = request.entity
            overrides = EntityInterest.objects.filter(entity=entity).select_related("interest")
            data = [
                {
                    "id": o.id,
                    "interest": {"id": o.interest.id, "name": o.interest.name},
                    "effect": o.effect,
                    "reason": o.reason,
                    "created_at": o.created_at,
                    "expires_at": o.expires_at,
                }
                for o in overrides
            ]
            return Response({"status": True, "data": data}, status=status.HTTP_200_OK)
        except Exception as e:
            logger.exception("EntityInterestOverrideListView.get failed")
            return Response(
                {"status": False, "message": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class EntityInterestOverrideView(APIView):
    """
    Create/update a grant or deny for the requesting entity, or delete one -
    ownership-scoped to request.entity throughout (never lets one entity
    touch another's overrides).
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            entity = request.entity
            interest_id = request.data.get("interest_id")
            interest_name = request.data.get("interest_name")
            effect = request.data.get("effect")
            reason = request.data.get("reason")
            expires_at = request.data.get("expires_at")

            if effect not in (PermissionEffect.GRANT, PermissionEffect.DENY):
                return Response(
                    {"status": False, "message": "effect must be 'grant' or 'deny'"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            if interest_id:
                interest = get_object_or_404(Interest, id=interest_id)
            elif interest_name:
                interest, _ = Interest.objects.get_or_create_by_name(interest_name)
            else:
                return Response(
                    {"status": False, "message": "interest_id or interest_name is required"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            override, _ = EntityInterest.objects.update_or_create(
                entity=entity,
                interest=interest,
                defaults={
                    "effect": effect,
                    "reason": reason,
                    "expires_at": expires_at,
                    "created_by": entity,
                },
            )

            return Response(
                {
                    "status": True,
                    "message": "Interest preference saved",
                    "data": {"id": override.id, "interest": interest.name, "effect": override.effect},
                },
                status=status.HTTP_200_OK,
            )
        except Exception as e:
            logger.exception("EntityInterestOverrideView.post failed")
            return Response(
                {"status": False, "message": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def delete(self, request, override_id):
        try:
            override = get_object_or_404(EntityInterest, id=override_id)

            try:
                if override.entity_id != request.entity.id:
                    raise PermissionDenied("You do not own this resource.")
            except PermissionDenied:
                raise

            override.delete()
            return Response(
                {"status": True, "message": "Interest preference removed"},
                status=status.HTTP_200_OK,
            )
        except PermissionDenied:
            raise
        except Exception as e:
            logger.exception("EntityInterestOverrideView.delete failed")
            return Response(
                {"status": False, "message": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class MyTopInterestsView(APIView):
    """
    Per-entity interest ranking - "which interests has THIS entity mostly
    interacted with", generalizing diary's existing per-user top_tags
    (DiaryTotalView) from a raw entry count to real engagement weight.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            limit = int(request.query_params.get("limit", 10))
            affinities = (
                EntityInterestAffinity.objects.filter(entity=request.entity)
                .select_related("interest")
                .order_by("-score")[:limit]
            )
            data = [
                {"interest": {"id": a.interest.id, "name": a.interest.name}, "score": a.score}
                for a in affinities
            ]
            return Response({"status": True, "data": data}, status=status.HTTP_200_OK)
        except Exception as e:
            logger.exception("MyTopInterestsView.get failed")
            return Response(
                {"status": False, "message": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class TrendingInterestsView(APIView):
    """
    Global ranking of interests against each other - "what's trending
    platform-wide right now". Unscoped/public, unlike MyTopInterestsView.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            limit = int(request.query_params.get("limit", 10))
            trending = (
                InterestTrendingScore.objects.select_related("interest").order_by("-score")[:limit]
            )
            data = [
                {"interest": {"id": t.interest.id, "name": t.interest.name}, "score": t.score}
                for t in trending
            ]
            return Response({"status": True, "data": data}, status=status.HTTP_200_OK)
        except Exception as e:
            logger.exception("TrendingInterestsView.get failed")
            return Response(
                {"status": False, "message": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
