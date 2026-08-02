"""
Network section endpoints - the redesigned Contacts page's data source.

Covers the three graph sections (Connections / Followers / Following). Each
has its OWN paginated endpoint for the "See all" infinite scrolls, plus one
overview endpoint that settles every preview in a single round-trip on page
init.

All NEW routes: the live mobile app pins /api/user/contacts, so that stays
untouched and keeps serving the existing contact list.

Results are RANKED, not merely listed - connections by
Connection.interaction_score and follows by Follow.interaction_score (both
bumped by real interaction, see entity/services/follows.py), so the people
you actually deal with land on top instead of whoever happens to be newest.

The page's fourth section - group chats - is deliberately NOT here. Those
are conversation shortcuts read from Mongo on the Node side
(GET /m/v2/group-shortcuts), not part of the connection/follow graph.
"""

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.pagination import PageNumberPagination
from django.db.models import Exists, F, OuterRef, Q, Value, IntegerField
from django.db.models.functions import Coalesce

from entity.models import Connection, Follow
from entity.utils import entity_side_is_visible, mutual_count_subquery
from user.utils.blocking import get_blocked_account_ids


class Pagination(PageNumberPagination):
    page_size = 12
    page_size_query_param = "page_size"


# Preview sizes for the overview call - one grid row each.
CONNECTIONS_PREVIEW = 6
FOLLOWERS_PREVIEW = 6
FOLLOWING_PREVIEW = 6


def _clean_profile(profile):
    # Same rule as search v2: both "no photo" sentinels (user "none", realm
    # "N/A") normalize to null so the client has one check.
    return None if profile in (None, "", "none", "N/A") else profile


def normalize_network_entity(entity_obj):
    """
    One row shape for BOTH kinds of counterpart, so the Contacts cards render
    a person and a page identically.

    Reads the reverse OneToOnes (`users` / `realms`) that the callers
    select_related, so this costs no extra query per row.

    Note the badge normalization: an Account's visible badge is `is_badged`
    (`is_verified` there is the email-confirmation gate), while a Realm's is
    `is_verified`. Both surface as `is_verified` = "show a check", matching
    what search v2 emits.
    """
    if entity_obj is None:
        return None

    account = getattr(entity_obj, "users", None)
    if account is not None:
        first = account.first_name or ""
        middle = account.middle_name or ""
        middle = "" if middle == "N/A" else middle
        last = account.last_name or ""
        display_name = " ".join(p for p in [first, middle, last] if p).strip()
        return {
            "entity_id": str(entity_obj.id),
            "type": "user",
            "display_name": display_name or (account.username or ""),
            "handle": account.username or "",
            "profile": _clean_profile(account.profile),
            "is_verified": bool(account.is_badged),
            "id": str(account.id),
        }

    realm = getattr(entity_obj, "realms", None)
    if realm is not None:
        return {
            "entity_id": str(entity_obj.id),
            "type": "realm",
            "display_name": realm.name or (realm.slug or ""),
            "handle": realm.slug or "",
            "profile": _clean_profile(realm.profile),
            "is_verified": bool(realm.is_verified),
            "id": str(realm.id),
            "realm_type": realm.type,
        }

    return None


def _counterpart_select_related(queryset, *fields):
    """
    LEFT JOIN both concrete sides of each entity FK. They are 1:1, so this
    never fans rows out - it just keeps normalize_network_entity() from
    issuing a query per row per branch (same approach UserContacts.get()
    uses).
    """
    paths = []
    for field in fields:
        paths.extend([field, f"{field}__users", f"{field}__realms"])
    return queryset.select_related(*paths)


def connection_counterpart(row, entity):
    """The OTHER side of a connection row, whichever column `entity` sits in."""
    if str(row.action_by_id) == str(entity.id):
        return row.involved_entity
    return row.action_by


def build_connections_queryset(entity, blocked_ids):
    """
    Settled connections involving `entity`, ranked by interaction.

    Matched from BOTH sides - `Q(action_by=entity) | Q(involved_entity=entity)`,
    the same predicate /api/user/contacts uses. A connection is *supposed* to
    be two mirrored rows, so filtering to `action_by=entity` alone looks like
    it yields each connection exactly once; in practice it silently drops
    connections whose mirror row is missing (legacy rows imported one-sided,
    see user/scripts/import_legacy_accounts.py - prod had an ODD count of
    accepted directions, which a fully mirrored table cannot produce). Those
    connections show up on /contacts and vanished here.

    Mirrored pairs still have to collapse to one row, so where BOTH directions
    exist the `action_by=entity` one wins and its twin is dropped; a one-sided
    row is kept regardless of which column `entity` landed in. The counterpart
    is therefore not always `involved_entity` - read it via
    connection_counterpart().
    """
    own_direction = Connection.objects.filter(
        connection_id=OuterRef("connection_id"), action_by=entity
    )
    return (
        _counterpart_select_related(
            Connection.objects.filter(
                Q(action_by=entity) | Q(involved_entity=entity),
                entity_side_is_visible("action_by"),
                entity_side_is_visible("involved_entity"),
                status=True,
            )
            .exclude(action_by=F("involved_entity"))
            .exclude(action_by__in=blocked_ids)
            .exclude(involved_entity__in=blocked_ids),
            "action_by",
            "involved_entity",
        )
        .annotate(has_own_direction=Exists(own_direction))
        .filter(Q(action_by=entity) | Q(has_own_direction=False))
        .order_by("-interaction_score", "-last_interaction_at", "connection_id")
    )


def build_followers_queryset(entity, blocked_ids):
    """
    Entities following `entity`, ranked by interaction.

    `is_followed_back` drives the Follow back / Following button without a
    second round-trip.

    Approved follows only. Pending requests against a private profile are
    real Follow rows, but they belong in the requests inbox
    (build_follow_requests_queryset), not in the followers list - listing
    them here would show a private profile people who cannot actually see
    it.
    """
    return (
        _counterpart_select_related(
            Follow.objects.filter(
                entity_side_is_visible("follower"),
                followee=entity,
                status=True,
            )
            .exclude(follower=entity)
            .exclude(follower__in=blocked_ids),
            "follower",
        )
        .annotate(
            is_followed_back=Exists(
                Follow.objects.filter(
                    follower=entity,
                    followee=OuterRef("follower_id"),
                    status=True,
                )
            )
        )
        .order_by("-interaction_score", "-last_interaction_at", "follow_id")
    )


def build_following_queryset(entity, blocked_ids):
    """
    Entities `entity` follows, ranked by interaction.

    Approved only - a follow request still awaiting approval is reported as
    "Requested" on the profile itself, not as someone you follow.
    """
    return _counterpart_select_related(
        Follow.objects.filter(
            entity_side_is_visible("followee"),
            follower=entity,
            status=True,
        )
        .exclude(followee=entity)
        .exclude(followee__in=blocked_ids),
        "followee",
    ).order_by("-interaction_score", "-last_interaction_at", "follow_id")


def build_follow_requests_queryset(entity, blocked_ids):
    """
    Follow requests awaiting THIS entity's approval - the inbox side of a
    private profile.

    Newest first rather than interaction-ranked: these are an action queue,
    not a relationship list, and there is no interaction history with someone
    who cannot see you yet.
    """
    return (
        _counterpart_select_related(
            Follow.objects.filter(
                entity_side_is_visible("follower"),
                followee=entity,
                status=False,
            )
            .exclude(follower=entity)
            .exclude(follower__in=blocked_ids),
            "follower",
        )
    ).order_by("-created_at", "follow_id")


def serialize_connection(row, entity, mutual_by_entity=None):
    counterpart = normalize_network_entity(connection_counterpart(row, entity))
    if counterpart is None:
        return None
    return {
        **counterpart,
        # What the message button routes to (/messages/<connection_id>).
        "connection_id": row.connection_id,
        "mutual_count": (mutual_by_entity or {}).get(counterpart["entity_id"], 0),
    }


def serialize_follower(row):
    counterpart = normalize_network_entity(row.follower)
    if counterpart is None:
        return None
    return {**counterpart, "is_followed_back": bool(row.is_followed_back)}


def serialize_follow_request(row):
    """
    Pending-request card. Same counterpart shape as a follower row so the
    inbox reuses the Contacts card, plus the requester's entity id spelled
    out - that is what the approve/decline call addresses.
    """
    counterpart = normalize_network_entity(row.follower)
    if counterpart is None:
        return None
    return {
        **counterpart,
        "is_pending": True,
        "requested_at": row.created_at,
    }


def serialize_following(row):
    counterpart = normalize_network_entity(row.followee)
    if counterpart is None:
        return None
    # Every row here IS someone this entity follows, so the button is always
    # the Unfollow state - stated explicitly so the client needs no special
    # case per section.
    return {**counterpart, "is_followed": True}


def mutual_counts_for(entity, rows):
    """
    Mutual-connection counts for a PAGE of connection rows, in one query.

    Annotating the count onto the main queryset would make the correlated
    subquery run for every candidate row before pagination; this instead
    resolves it only for the ~12 rows actually being returned.
    """
    entity_ids = []
    for row in rows:
        counterpart_id = (
            row.involved_entity_id
            if str(row.action_by_id) == str(entity.id)
            else row.action_by_id
        )
        if counterpart_id:
            entity_ids.append(str(counterpart_id))
    if not entity_ids:
        return {}

    from entity.models import Entity

    counted = (
        Entity.objects.filter(id__in=entity_ids)
        .annotate(
            mutual_count=Coalesce(
                mutual_count_subquery(entity, outer_field="id"),
                Value(0),
                output_field=IntegerField(),
            )
        )
        .values_list("id", "mutual_count")
    )
    return {str(eid): count for eid, count in counted}


class NetworkOverview(APIView):
    """
    The Contacts page's single init call: previews of the three graph
    sections plus the totals the jump chips show. Each fetches previewN+1
    rows so `has_more` is known without a second COUNT.

    Group chats are deliberately NOT here - they are conversation shortcuts,
    not part of the follow/connection graph, and live in Mongo on the Node
    side (GET /m/v2/group-shortcuts).
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        entity = request.entity
        try:
            blocked_ids = get_blocked_account_ids(entity)

            connections_qs = build_connections_queryset(entity, blocked_ids)
            followers_qs = build_followers_queryset(entity, blocked_ids)
            following_qs = build_following_queryset(entity, blocked_ids)
            # Count only - the requests themselves come from
            # /network/follow-requests. This is what puts a badge on the
            # inbox without a second page-init call, and it is 0 for every
            # public profile since nothing can be pending against one.
            follow_requests_count = build_follow_requests_queryset(
                entity, blocked_ids
            ).count()

            connection_rows = list(connections_qs[: CONNECTIONS_PREVIEW + 1])
            follower_rows = list(followers_qs[: FOLLOWERS_PREVIEW + 1])
            following_rows = list(following_qs[: FOLLOWING_PREVIEW + 1])

            mutuals = mutual_counts_for(entity, connection_rows[:CONNECTIONS_PREVIEW])

            def section(rows, preview, serializer, total):
                visible = rows[:preview]
                return {
                    "has_more": len(rows) > preview,
                    "total": total,
                    "results": [
                        item
                        for item in (serializer(row) for row in visible)
                        if item is not None
                    ],
                }

            return Response(
                {
                    "connections": section(
                        connection_rows,
                        CONNECTIONS_PREVIEW,
                        lambda row: serialize_connection(row, entity, mutuals),
                        connections_qs.count(),
                    ),
                    "followers": section(
                        follower_rows,
                        FOLLOWERS_PREVIEW,
                        serialize_follower,
                        followers_qs.count(),
                    ),
                    "following": section(
                        following_rows,
                        FOLLOWING_PREVIEW,
                        serialize_following,
                        following_qs.count(),
                    ),
                    "follow_requests_count": follow_requests_count,
                },
                status=status.HTTP_200_OK,
            )
        except Exception as e:
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class NetworkConnections(APIView):
    """Ranked connections - the Connections "See all" infinite scroll."""

    permission_classes = [IsAuthenticated]
    pagination_class = Pagination

    def get(self, request):
        entity = request.entity
        try:
            blocked_ids = get_blocked_account_ids(entity)
            queryset = build_connections_queryset(entity, blocked_ids)

            paginator = self.pagination_class()
            page = paginator.paginate_queryset(queryset, request, view=self)
            mutuals = mutual_counts_for(entity, page)
            results = [
                item
                for item in (
                    serialize_connection(row, entity, mutuals) for row in page
                )
                if item is not None
            ]
            return paginator.get_paginated_response(results)
        except Exception as e:
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class NetworkFollowers(APIView):
    """Ranked followers - the Followers "See all" infinite scroll."""

    permission_classes = [IsAuthenticated]
    pagination_class = Pagination

    def get(self, request):
        entity = request.entity
        try:
            blocked_ids = get_blocked_account_ids(entity)
            queryset = build_followers_queryset(entity, blocked_ids)

            paginator = self.pagination_class()
            page = paginator.paginate_queryset(queryset, request, view=self)
            results = [
                item
                for item in (serialize_follower(row) for row in page)
                if item is not None
            ]
            return paginator.get_paginated_response(results)
        except Exception as e:
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class NetworkFollowRequests(APIView):
    """
    Pending follow requests addressed to the acting entity - what a private
    profile approves or declines, via PUT /api/community/follow.
    """

    permission_classes = [IsAuthenticated]
    pagination_class = Pagination

    def get(self, request):
        entity = request.entity
        try:
            blocked_ids = get_blocked_account_ids(entity)
            queryset = build_follow_requests_queryset(entity, blocked_ids)

            paginator = self.pagination_class()
            page = paginator.paginate_queryset(queryset, request, view=self)
            results = [
                item
                for item in (serialize_follow_request(row) for row in page)
                if item is not None
            ]
            return paginator.get_paginated_response(results)
        except Exception as e:
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class NetworkFollowing(APIView):
    """Ranked following - the Following "See all" infinite scroll."""

    permission_classes = [IsAuthenticated]
    pagination_class = Pagination

    def get(self, request):
        entity = request.entity
        try:
            blocked_ids = get_blocked_account_ids(entity)
            queryset = build_following_queryset(entity, blocked_ids)

            paginator = self.pagination_class()
            page = paginator.paginate_queryset(queryset, request, view=self)
            results = [
                item
                for item in (serialize_following(row) for row in page)
                if item is not None
            ]
            return paginator.get_paginated_response(results)
        except Exception as e:
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)


