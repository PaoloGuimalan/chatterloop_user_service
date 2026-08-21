from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.pagination import PageNumberPagination
from django.db.models import Q, Exists, OuterRef, Subquery, F

from entity.services.allowed_modules import resolve_allowed_modules_and_context
from entity.models import Connection
from user.models import Account
from user.utils.blocking import get_blocked_account_ids
from community.models import Realm
import logging

logger = logging.getLogger(__name__)


class MyAllowedModules(APIView):
    """
    Returns which entity-type-scoped module codenames the current active
    entity (request.entity) is allowed to see - the frontend's source of
    truth for which UI modules to show, since only the resolver knows about
    per-entity EntityPermission overrides (e.g. a suspended page individually
    denied a module) that client-side inference from entity.type alone
    would miss.

    Also resolves and returns the active entity's own context (type, display
    name, profile path). This is the only place that CAN reliably answer
    "am I currently acting as myself or as a page" after a page reload -
    the JWT's `entity` claim is an opaque id with no type flag, so the
    frontend cannot tell from the token alone.

    Login/register/third-party-auth now merge resolve_allowed_modules_and_
    context() directly into their own response instead of requiring this as
    a separate follow-up call - this endpoint remains for session restore
    (AuthCheck) and any future on-demand refresh.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        entity = request.entity
        user = request.user
        try:
            allowed_modules, active_entity = resolve_allowed_modules_and_context(
                entity, user
            )

            return Response(
                {
                    "status": True,
                    "result": {
                        "allowed_modules": allowed_modules,
                        "active_entity": active_entity,
                        "personal_entity_id": str(user.entity.id),
                    },
                },
                status=status.HTTP_200_OK,
            )
        except Exception as e:
            logger.exception("MyAllowedModules.get failed")
            return Response(
                {"status": False, "message": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class Pagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = "page_size"


class EntitySearch(APIView):
    """
    Search v2 - unified entity search returning both users and realms/pages in
    one normalized shape ({entity_id, type, display_name, handle, profile,
    is_verified, realm_type}). Built for post tagging (tag a person OR a page),
    but intended to become the go-forward search endpoint the rest of the app
    migrates onto - hence the flexible filters below and the deliberate
    decoupling from the user-only /api/user/search response (whose shape the
    Explore page and top-bar drawer still depend on).

    Query params:
      - types: comma list of entity kinds to include. Default "user,realm".
      - realm_types: comma list of Realm.type values to include when realms are
        in scope, or "all" for no type filter. Default "page" - only public,
        profile-like pages are taggable today; widen later with no code change.
    """

    permission_classes = [IsAuthenticated]
    pagination_class = Pagination

    @staticmethod
    def _clean_profile(profile):
        # The two models use different "no photo" sentinels (user: "none",
        # realm: "N/A"); normalize both to null so the client has one rule.
        return None if profile in (None, "", "none", "N/A") else profile

    def _normalize_account(self, row, acting_entity_id=None):
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
            "profile": self._clean_profile(row.get("profile")),
            # A user's verified badge is `is_badged`, NOT `is_verified` (which is
            # the account/email-verification gate). Realms carry their badge on
            # `is_verified`. Normalize both onto the output `is_verified` =
            # "show a verified badge", matching how PostItem renders each type.
            "is_verified": bool(row.get("is_badged")),
            "realm_type": None,
            # --- connection state (users only) ---
            # Explore and the top-bar drawer render Add / Accept / Decline off
            # these, and those actions address an Account id, not an entity_id.
            # Emitted here so those surfaces can move off the user-only
            # /api/user/search without losing behaviour. Realms get null for
            # all of them (see _normalize_realm) so the client has one rule:
            # "no id -> no connection actions".
            "id": row.get("id"),
            "has_connection": bool(row.get("has_connection")),
            "connection_accomplished": bool(row.get("connection_accomplished")),
            "connection_id": row.get("connection_id"),
            # A connection is stored as TWO mirrored rows (one per direction),
            # so "is there a row where I am action_by" is always true and
            # cannot identify the requester. The initiator is the action_by of
            # the EARLIEST row - same rule the profile endpoint uses.
            "is_action_by_entity": bool(
                acting_entity_id
                and str(row.get("connection_initiator_id") or "") == acting_entity_id
            ),
        }

    def _normalize_realm(self, row):
        return {
            "entity_id": row.get("entity_id"),
            "type": "realm",
            "display_name": row.get("name") or (row.get("slug") or ""),
            "handle": row.get("slug") or "",
            "profile": self._clean_profile(row.get("profile")),
            "is_verified": bool(row.get("is_verified")),
            "realm_type": row.get("type"),
            # Realms are not connection targets from search - the client opens
            # the page and follows from there. Keys are still present so both
            # kinds share one shape.
            "id": row.get("id"),
            "has_connection": False,
            "connection_accomplished": False,
            "connection_id": None,
            "is_action_by_entity": False,
        }

    def get(self, request, query):
        entity = request.entity
        try:
            raw_kinds = request.query_params.get("types", "user,realm")
            kinds = {t.strip() for t in raw_kinds.split(",") if t.strip()}

            raw_realm_types = request.query_params.get("realm_types", "page")
            realm_types = [t.strip() for t in raw_realm_types.split(",") if t.strip()]
            include_all_realm_types = "all" in realm_types

            blocked_ids = get_blocked_account_ids(entity)

            # Per-kind cap so one kind can't crowd the other out before we merge.
            per_kind_limit = 25
            results = []

            if "user" in kinds:
                if query.startswith("@"):
                    user_filter = Q(username__icontains=query.split("@", 1)[1])
                else:
                    user_filter = (
                        Q(first_name__icontains=query)
                        | Q(middle_name__icontains=query)
                        | Q(last_name__icontains=query)
                        | Q(username__icontains=query)
                    )

                # Connection state between the acting entity and each hit, so
                # Explore/drawer keep their Add / Accept / Decline behaviour.
                # Connection is entity<->entity, so this is NOT restricted to
                # user-only sides the way the old /api/user/search was.
                base_connection_qs = Connection.objects.filter(
                    Q(action_by=entity, involved_entity=OuterRef("entity_id"))
                    | Q(action_by=OuterRef("entity_id"), involved_entity=entity),
                    ~Q(action_by=F("involved_entity")),
                )

                accounts = (
                    Account.objects.filter(
                        user_filter,
                        is_active=True,
                        is_verified=True,
                    )
                    .exclude(entity_id=entity.id)
                    .exclude(entity_id__in=blocked_ids)
                    .annotate(
                        has_connection=Exists(base_connection_qs),
                        connection_accomplished=Exists(
                            base_connection_qs.filter(status=True)
                        ),
                        connection_id=Subquery(
                            base_connection_qs.values("connection_id")[:1]
                        ),
                        # Who actually initiated: the action_by of the EARLIEST
                        # of the two mirrored rows. Filtering on action_by=entity
                        # would always match (both directions exist), which is
                        # why this is a Subquery ordered by action_date rather
                        # than an Exists().
                        connection_initiator_id=Subquery(
                            base_connection_qs.order_by("action_date").values(
                                "action_by_id"
                            )[:1]
                        ),
                    )
                    .values(
                        "id",
                        "entity_id",
                        "username",
                        "first_name",
                        "middle_name",
                        "last_name",
                        "profile",
                        "is_badged",
                        "has_connection",
                        "connection_accomplished",
                        "connection_id",
                        "connection_initiator_id",
                    )[:per_kind_limit]
                )
                acting_entity_id = str(entity.id)
                results.extend(
                    self._normalize_account(a, acting_entity_id) for a in accounts
                )

            if "realm" in kinds:
                if query.startswith("@"):
                    realm_filter = Q(slug__icontains=query.split("@", 1)[1])
                else:
                    realm_filter = Q(name__icontains=query) | Q(slug__icontains=query)

                realm_qs = Realm.objects.filter(
                    realm_filter,
                    is_active=True,
                    is_private=False,
                )
                if not include_all_realm_types and realm_types:
                    realm_qs = realm_qs.filter(type__in=realm_types)

                realms = (
                    realm_qs.exclude(entity_id=entity.id)
                    .exclude(entity_id__in=blocked_ids)
                    .values(
                        "id",
                        "entity_id",
                        "name",
                        "slug",
                        "profile",
                        "is_verified",
                        "type",
                    )[:per_kind_limit]
                )
                results.extend(self._normalize_realm(r) for r in realms)

            # Prefix matches first (typing "jo" surfaces "John"/"jodoe" above
            # incidental substring hits), then alphabetical for stable ordering.
            q_lower = query.lstrip("@").lower()

            def sort_key(item):
                name = (item["display_name"] or "").lower()
                handle = (item["handle"] or "").lower()
                is_prefix = name.startswith(q_lower) or handle.startswith(q_lower)
                return (not is_prefix, name)

            results.sort(key=sort_key)

            paginator = self.pagination_class()
            page = paginator.paginate_queryset(results, request, view=self)
            return paginator.get_paginated_response(page)

        except Exception as e:
            logger.exception("EntitySearch.get failed")
            return Response(str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)
