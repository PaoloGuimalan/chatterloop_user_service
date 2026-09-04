"""
Search v2 section endpoints - the redesigned Search page's data source.

The page renders five independently-loadable sections (Topics / People /
Realms / Bots / Content), each backed by its OWN paginated endpoint for the
"See all" infinite scrolls, plus one overview endpoint that settles every
section preview in a single round-trip on page init / query change. Topics are the exception to
"its own endpoint here": they are interests, so their list lives with the rest
of the interests app at /api/interests/topics/.

These are all NEW routes: the live mobile app pins the existing
/api/entity/search/<query>/ and /api/user/search/<query>/ shapes, so those
stay untouched and keep serving post tagging / the top-bar drawer until
their surfaces migrate here.

Results are RANKED, not merely filtered: people order by prefix-match then
mutual-connection count, realms by prefix-match then follower/member reach,
bots by prefix-match then follower reach, and posts (see
newsfeed/services/post_search.py, reused by the overview here) by
PostScore.ranking_score - so the most relevant hits land on top of every
section.

The Bots section has no UI yet. It is here because a bot is an entity like any
other - it can be added to a group, messaged directly, mentioned and followed -
and every one of those starts with being findable. Shipping the endpoint first
means the page gains a section without a backend change.
"""

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.pagination import PageNumberPagination
from django.db.models import (
    Q,
    Exists,
    OuterRef,
    Subquery,
    F,
    Value,
    Count,
    Case,
    When,
    IntegerField,
    BooleanField,
    ExpressionWrapper,
)
from django.db.models.functions import Coalesce

from bot.models import Bot
from community.models import Realm, Member
from entity.models import Connection, Follow
from entity.utils import mutual_count_subquery
from user.models import Account
from user.utils.blocking import get_blocked_account_ids
import logging

logger = logging.getLogger(__name__)


class Pagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = "page_size"


# The only realm kinds search ever surfaces. Channels, conferences and voice
# rooms are conversation-infrastructure realms, not discoverable communities -
# they must never leak into search no matter what realm_types a client asks
# for (the filter below INTERSECTS with this set rather than trusting input).
PUBLIC_REALM_TYPES = ("page", "server", "group")


def _clean_profile(profile):
    # Same rule as EntitySearch: both "no photo" sentinels (user: "none",
    # realm: "N/A") normalize to null so the client has one rule.
    return None if profile in (None, "", "none", "N/A") else profile


def _split_query(query):
    """
    "@handle" searches handles only; plain text searches names AND handles.
    Returns (bare_term, is_handle_query) - bare_term is what prefix-ranking
    compares against either way.
    """
    if query.startswith("@"):
        return query.split("@", 1)[1], True
    return query, False


def build_people_queryset(entity, query, blocked_ids):
    """
    Accounts matching `query`, annotated with everything the People cards
    render: follow state (button label), mutual-connection count (subtitle
    + ranking), and the same connection-state keys EntitySearch emits so
    the card can keep deep-linking into contact flows later.

    Ranked: prefix matches first, then mutual connections desc, then name -
    "people you plausibly know" surface above incidental substring hits.
    """
    term, handle_only = _split_query(query)
    if handle_only:
        user_filter = Q(username__icontains=term)
    else:
        user_filter = (
            Q(first_name__icontains=term)
            | Q(middle_name__icontains=term)
            | Q(last_name__icontains=term)
            | Q(username__icontains=term)
        )

    # Connection state between the acting entity and each hit - identical
    # construction to EntitySearch (see its comments on the mirrored-rows
    # model and why the initiator needs an action_date-ordered Subquery).
    base_connection_qs = Connection.objects.filter(
        Q(action_by=entity, involved_entity=OuterRef("entity_id"))
        | Q(action_by=OuterRef("entity_id"), involved_entity=entity),
        ~Q(action_by=F("involved_entity")),
    )

    # Mutual connections - shared with the Contacts page's network sections,
    # see entity/utils.mutual_count_subquery() for how the mirrored-rows
    # model is collapsed into a single scalar COUNT.
    mutual_qs = mutual_count_subquery(entity)

    return (
        Account.objects.filter(
            user_filter,
            is_active=True,
            is_verified=True,
        )
        .exclude(entity_id=entity.id)
        .exclude(entity_id__in=blocked_ids)
        .annotate(
            is_followed=Exists(
                Follow.objects.filter(
                    follower=entity,
                    followee=OuterRef("entity_id"),
                    status=True,
                )
            ),
            # A follow of a PRIVATE profile is created pending and stays that
            # way until its owner approves it. Without this the card falls
            # back to "Follow" for someone you have already asked, and tapping
            # again is a no-op - follow_entity is idempotent and will not
            # re-notify, so the button would look permanently broken.
            is_follow_pending=Exists(
                Follow.objects.filter(
                    follower=entity,
                    followee=OuterRef("entity_id"),
                    status=False,
                )
            ),
            has_connection=Exists(base_connection_qs),
            connection_accomplished=Exists(base_connection_qs.filter(status=True)),
            connection_id=Subquery(base_connection_qs.values("connection_id")[:1]),
            connection_initiator_id=Subquery(
                base_connection_qs.order_by("action_date").values("action_by_id")[:1]
            ),
            mutual_count=Coalesce(
                Subquery(mutual_qs, output_field=IntegerField()), Value(0)
            ),
            search_rank=Case(
                When(
                    Q(first_name__istartswith=term)
                    | Q(last_name__istartswith=term)
                    | Q(username__istartswith=term),
                    then=Value(0),
                ),
                default=Value(1),
                output_field=IntegerField(),
            ),
        )
        .order_by("search_rank", "-mutual_count", "first_name", "id")
        .values(
            "id",
            "entity_id",
            "username",
            "first_name",
            "middle_name",
            "last_name",
            "profile",
            "is_badged",
            "is_followed",
            "is_follow_pending",
            "has_connection",
            "connection_accomplished",
            "connection_id",
            "connection_initiator_id",
            "mutual_count",
        )
    )


def normalize_person(row, acting_entity_id):
    first = row.get("first_name") or ""
    middle = row.get("middle_name") or ""
    middle = "" if middle == "N/A" else middle
    last = row.get("last_name") or ""
    display_name = " ".join(p for p in [first, middle, last] if p).strip()
    return {
        "entity_id": row.get("entity_id"),
        "type": "user",
        "display_name": display_name or (row.get("username") or ""),
        "handle": row.get("username") or "",
        "profile": _clean_profile(row.get("profile")),
        # is_badged is the visible verified badge; is_verified on Account is
        # the email-confirmation gate (same normalization as EntitySearch).
        "is_verified": bool(row.get("is_badged")),
        "mutual_count": row.get("mutual_count") or 0,
        "is_followed": bool(row.get("is_followed")),
        # Mutually exclusive with is_followed - the pair is what distinguishes
        # Follow / Requested / Following on the card.
        "is_follow_pending": bool(row.get("is_follow_pending")),
        "id": row.get("id"),
        "has_connection": bool(row.get("has_connection")),
        "connection_accomplished": bool(row.get("connection_accomplished")),
        "connection_id": row.get("connection_id"),
        "is_action_by_entity": bool(
            acting_entity_id
            and str(row.get("connection_initiator_id") or "") == acting_entity_id
        ),
    }


def build_realms_queryset(entity, query, blocked_ids, realm_types):
    """
    Public realms (page / server / group only - channel, conference and
    voice never surface in search), annotated with the reach counters the cards
    show and the viewer's own standing (is_follower / is_member) so the
    action button can render Follow/Following vs Open without a second
    call. Annotation patterns match TopRealms / FollowRealmView.

    Ranked: prefix matches first, then follower/member reach.
    """
    term, handle_only = _split_query(query)
    if handle_only:
        realm_filter = Q(slug__icontains=term)
    else:
        realm_filter = Q(name__icontains=term) | Q(slug__icontains=term)

    # "all" means "all PUBLIC kinds"; explicit lists are intersected with the
    # public set so channel/conference/voice can't be requested into results.
    if not realm_types or "all" in realm_types:
        effective_types = list(PUBLIC_REALM_TYPES)
    else:
        effective_types = [t for t in realm_types if t in PUBLIC_REALM_TYPES]

    qs = Realm.objects.filter(
        realm_filter,
        is_active=True,
        is_private=False,
        type__in=effective_types,
    )

    return (
        qs.exclude(entity_id=entity.id)
        .exclude(entity_id__in=blocked_ids)
        .annotate(
            members_count=Count("member", distinct=True),
            followers_count=Count(
                "entity__followers",
                filter=Q(entity__followers__status=True),
                distinct=True,
            ),
            is_follower=Exists(
                Follow.objects.filter(
                    followee=OuterRef("entity_id"),
                    follower=entity,
                    status=True,
                )
            ),
            # A page's own entity is never a Member row of its own realm, so
            # Q(entity=entity) catches the self-administration case (same
            # note as TopRealms).
            is_member=ExpressionWrapper(
                Q(Exists(Member.objects.filter(realm=OuterRef("pk"), entity=entity)))
                | Q(entity=entity),
                output_field=BooleanField(),
            ),
            search_rank=Case(
                When(
                    Q(name__istartswith=term) | Q(slug__istartswith=term),
                    then=Value(0),
                ),
                default=Value(1),
                output_field=IntegerField(),
            ),
        )
        .order_by("search_rank", "-followers_count", "-members_count", "name")
        .values(
            "id",
            "entity_id",
            "name",
            "slug",
            "profile",
            "is_verified",
            "type",
            "members_count",
            "followers_count",
            "is_follower",
            "is_member",
        )
    )


def normalize_realm(row):
    return {
        "entity_id": row.get("entity_id"),
        "type": "realm",
        "display_name": row.get("name") or (row.get("slug") or ""),
        "handle": row.get("slug") or "",
        "profile": _clean_profile(row.get("profile")),
        "is_verified": bool(row.get("is_verified")),
        "realm_type": row.get("type"),
        "members_count": row.get("members_count") or 0,
        "followers_count": row.get("followers_count") or 0,
        "is_follower": bool(row.get("is_follower")),
        "is_member": bool(row.get("is_member")),
        "id": row.get("id"),
    }


def build_bots_queryset(entity, query, blocked_ids):
    """
    Active, non-system bots matching `query`, annotated with follower reach and
    the viewer's own follow state so the card can render Follow/Following
    without a second call.

    SYSTEM BOTS ARE EXCLUDED. The moderation bot is platform machinery - it
    speaks as "the platform" in reports and notifications, and offering it as
    something to follow, tag or add to a group invites confusion about who is
    talking. Same call EntitySearch makes.

    Ranked: prefix matches first, then follower reach, then name. There is no
    mutual-connection equivalent to rank by - a bot has no social graph of its
    own worth ordering on - so reach is the only signal available.
    """
    term, handle_only = _split_query(query)
    if handle_only:
        bot_filter = Q(handle__icontains=term)
    else:
        bot_filter = Q(name__icontains=term) | Q(handle__icontains=term)

    return (
        Bot.objects.filter(bot_filter, is_active=True)
        .exclude(is_system=True)
        .exclude(entity_id=entity.id)
        .exclude(entity_id__in=blocked_ids)
        .annotate(
            # Bot.entity is a OneToOne to Entity, which is what `followers`
            # hangs off - the same one-hop path Realm uses.
            followers_count=Count(
                "entity__followers",
                filter=Q(entity__followers__status=True),
                distinct=True,
            ),
            is_followed=Exists(
                Follow.objects.filter(
                    follower=entity,
                    followee=OuterRef("entity_id"),
                    status=True,
                )
            ),
            search_rank=Case(
                When(
                    Q(name__istartswith=term) | Q(handle__istartswith=term),
                    then=Value(0),
                ),
                default=Value(1),
                output_field=IntegerField(),
            ),
        )
        .order_by("search_rank", "-followers_count", "name", "id")
        .values(
            "id",
            "entity_id",
            "name",
            "handle",
            "description",
            "profile",
            "is_verified",
            "followers_count",
            "is_followed",
        )
    )


def normalize_bot(row):
    return {
        "entity_id": row.get("entity_id"),
        "type": "bot",
        "display_name": row.get("name") or (row.get("handle") or ""),
        "handle": row.get("handle") or "",
        "profile": _clean_profile(row.get("profile")),
        "is_verified": bool(row.get("is_verified")),
        # What the bot is for. People and realms have no equivalent one-liner
        # on their cards, but a bot without one is indistinguishable from any
        # other bot, which defeats the point of finding it.
        "description": row.get("description") or "",
        "followers_count": row.get("followers_count") or 0,
        "is_followed": bool(row.get("is_followed")),
        # Present and always False so one client card can render any entity
        # kind: a bot has no privacy gate, so a follow of one is never pending
        # the way a follow of a private profile is.
        "is_follow_pending": False,
        "id": row.get("id"),
    }


class SearchPeopleV2(APIView):
    """
    GET /api/entity/search/v2/people/<query>/?page=&page_size=
    Paginated - drives the People "See all" infinite scroll.
    """

    permission_classes = [IsAuthenticated]
    pagination_class = Pagination

    def get(self, request, query):
        entity = request.entity
        try:
            blocked_ids = get_blocked_account_ids(entity)
            queryset = build_people_queryset(entity, query, blocked_ids)

            paginator = self.pagination_class()
            page = paginator.paginate_queryset(queryset, request, view=self)
            acting_entity_id = str(entity.id)
            return paginator.get_paginated_response(
                [normalize_person(row, acting_entity_id) for row in page]
            )
        except Exception as e:
            logger.exception("SearchPeopleV2.get failed")
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class SearchRealmsV2(APIView):
    """
    GET /api/entity/search/v2/realms/<query>/?page=&page_size=&realm_types=all
    Paginated - drives the Realms "See all" infinite scroll. realm_types is
    a comma list of Realm.type values, or "all" (the default - the redesign
    surfaces servers, groups and pages alike).
    """

    permission_classes = [IsAuthenticated]
    pagination_class = Pagination

    def get(self, request, query):
        entity = request.entity
        try:
            raw_realm_types = request.query_params.get("realm_types", "all")
            realm_types = [t.strip() for t in raw_realm_types.split(",") if t.strip()]

            blocked_ids = get_blocked_account_ids(entity)
            queryset = build_realms_queryset(entity, query, blocked_ids, realm_types)

            paginator = self.pagination_class()
            page = paginator.paginate_queryset(queryset, request, view=self)
            return paginator.get_paginated_response(
                [normalize_realm(row) for row in page]
            )
        except Exception as e:
            logger.exception("SearchRealmsV2.get failed")
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class SearchBotsV2(APIView):
    """
    GET /api/entity/search/v2/bots/<query>/?page=&page_size=
    Paginated - drives the Bots "See all" infinite scroll.
    """

    permission_classes = [IsAuthenticated]
    pagination_class = Pagination

    def get(self, request, query):
        entity = request.entity
        try:
            blocked_ids = get_blocked_account_ids(entity)
            queryset = build_bots_queryset(entity, query, blocked_ids)

            paginator = self.pagination_class()
            page = paginator.paginate_queryset(queryset, request, view=self)
            return paginator.get_paginated_response(
                [normalize_bot(row) for row in page]
            )
        except Exception as e:
            logger.exception("SearchBotsV2.get failed")
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class SearchOverviewV2(APIView):
    """
    GET /api/entity/search/v2/overview/<query>/

    The Search page's single init call: previews of every section in one
    round-trip. Each section fetches previewN+1 rows so `has_more` can
    be answered without COUNT queries - the "See all" buttons don't show
    totals, and the detail endpoints' DRF pagination reports `count`
    anyway.

    The posts and topics builders are deferred-imported from newsfeed and
    interests: this module is imported at URLconf load, and both of those
    import back into user/entity models, so a module-scope import would close
    a cycle (same trick as entity/services/follows.py).
    """

    permission_classes = [IsAuthenticated]

    PEOPLE_PREVIEW = 8
    REALMS_PREVIEW = 6
    POSTS_PREVIEW = 5
    # Smallest section: the bot population is tiny next to people and realms,
    # and a query that matches many of them is matching on a common word
    # rather than finding a particular one.
    BOTS_PREVIEW = 4
    # Topics are the shortest section on the screen and the most likely to be
    # what was meant: somebody typing "sunset" into Explore is usually after the
    # topic, not a person whose surname contains it. Five is enough to show the
    # near-misses around an exact hit without pushing People below the fold.
    TOPICS_PREVIEW = 5

    def get(self, request, query):
        entity = request.entity
        try:
            from newsfeed.services.post_search import (
                build_post_search_queryset,
                serialize_post_hit,
            )
            from interests.services.topics import (
                build_topic_queryset,
                serialize_topics,
            )

            blocked_ids = get_blocked_account_ids(entity)
            acting_entity_id = str(entity.id)

            people_rows = list(
                build_people_queryset(entity, query, blocked_ids)[
                    : self.PEOPLE_PREVIEW + 1
                ]
            )
            realm_rows = list(
                build_realms_queryset(entity, query, blocked_ids, ["all"])[
                    : self.REALMS_PREVIEW + 1
                ]
            )
            bot_rows = list(
                build_bots_queryset(entity, query, blocked_ids)[
                    : self.BOTS_PREVIEW + 1
                ]
            )
            post_rows = list(
                build_post_search_queryset(entity, query, blocked_ids)[
                    : self.POSTS_PREVIEW + 1
                ]
            )
            topic_rows = list(
                build_topic_queryset(entity, query)[: self.TOPICS_PREVIEW + 1]
            )

            return Response(
                {
                    "status": True,
                    "result": {
                        "people": {
                            "has_more": len(people_rows) > self.PEOPLE_PREVIEW,
                            "results": [
                                normalize_person(row, acting_entity_id)
                                for row in people_rows[: self.PEOPLE_PREVIEW]
                            ],
                        },
                        "realms": {
                            "has_more": len(realm_rows) > self.REALMS_PREVIEW,
                            "results": [
                                normalize_realm(row)
                                for row in realm_rows[: self.REALMS_PREVIEW]
                            ],
                        },
                        # No UI yet - a client that does not know this key
                        # ignores it, which is why adding it here needs no
                        # coordinated release.
                        "bots": {
                            "has_more": len(bot_rows) > self.BOTS_PREVIEW,
                            "results": [
                                normalize_bot(row)
                                for row in bot_rows[: self.BOTS_PREVIEW]
                            ],
                        },
                        "posts": {
                            "has_more": len(post_rows) > self.POSTS_PREVIEW,
                            "results": [
                                serialize_post_hit(post)
                                for post in post_rows[: self.POSTS_PREVIEW]
                            ],
                        },
                        # Same row shape as /api/interests/popular/ and the
                        # topic directory, so one client widget renders a topic
                        # wherever it appears. drop_empty is off because
                        # build_topic_queryset already excluded topics with
                        # nothing visible - see interests/services/topics.py.
                        "topics": {
                            "has_more": len(topic_rows) > self.TOPICS_PREVIEW,
                            "results": serialize_topics(
                                topic_rows[: self.TOPICS_PREVIEW],
                                {row.id: row.trending for row in topic_rows},
                                entity,
                                drop_empty=False,
                            ),
                        },
                    },
                },
                status=status.HTTP_200_OK,
            )
        except Exception as e:
            logger.exception("SearchOverviewV2.get failed")
            return Response(
                {"status": False, "message": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
