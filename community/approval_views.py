from django.db.models import BooleanField, CharField, Value
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from community.models import Invite, Realm, Approvals
from community.post_review import review_post
from community.review_serializers import decision_payload, submission_payload
from community.role_policy import require_realm_actor, realm_capabilities
from community.serializers import InviteSerializer
from community.views import Pagination
from entity.permissions import Permission
from entity.services.permission_resolver import has_active_permission as has_permission
from newsfeed.models import Post
from newsfeed.services.post_cards import load_post_cards


def reviewer_realm(request, realm_id, permission):
    realm = get_object_or_404(Realm, pk=realm_id, type="community", is_active=True)
    require_realm_actor(request.entity, realm)
    permissions = permission if isinstance(permission, tuple) else (permission,)
    if not any(has_permission(request.entity, code, realm) for code in permissions):
        raise PermissionDenied("You cannot review this community's submissions.")
    return realm


def review_cards(queryset):
    return load_post_cards(queryset).annotate(
        entity_reaction=Value(None, output_field=CharField()),
        entity_reaction_is_anonymous=Value(None, output_field=BooleanField()),
        is_saved=Value(False, output_field=BooleanField()))


class CommunityApprovalsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, realm_id):
        kind = request.query_params.get("kind", "posts")
        permissions = {"posts": Permission.REALM_POST_REVIEW, "invites": Permission.REALM_INVITE_REVIEW,
                       "joins": (Permission.REALM_REQUEST_APPROVE, Permission.REALM_REQUEST_DECLINE)}
        if kind not in permissions:
            raise ValidationError({"kind": "Choose a supported approval section."})
        state = request.query_params.get("state", "pending")
        if state not in ("pending", "not_approved"):
            raise ValidationError({"state": "Choose pending or not_approved."})
        realm = reviewer_realm(request, realm_id, permissions[kind])
        target_id = request.query_params.get('target_id')
        if target_id is not None and (not isinstance(target_id, str) or not target_id or len(target_id) > 150):
            raise ValidationError({'target_id': 'Choose a valid approval target.'})
        if kind != "posts":
            if state != "pending":
                raise ValidationError({"state": "Only posts have a not-approved filter."})
            rows = Invite.objects.filter(realm=realm, kind="invite" if kind == "invites" else "request",
                                         status="for_approval" if kind == "invites" else "pending")
            if target_id:
                rows = rows.filter(pk=target_id)
            paginator = Pagination()
            page = paginator.paginate_queryset(rows.select_related("role", "realm", "created_by__users", "created_by__realms",
                "target_entity__users", "target_entity__realms").order_by("-created_at", "-pk"), request, view=self)
            response = paginator.get_paginated_response(InviteSerializer(page, many=True).data)
            response.data["counts"] = approval_counts(request.entity, realm)
            return response
        queryset = Post.objects.filter(tagging__entity_id=realm.entity_id, deleted_at=None,
                                       on_feed="feed", approval=0 if state == "pending" else 2).distinct()
        if target_id:
            queryset = queryset.filter(pk=target_id)
        paginator = Pagination()
        posts = paginator.paginate_queryset(review_cards(queryset).order_by("-date_posted", "-pk"), request, view=self)
        decisions = {}
        for decision in Approvals.objects.filter(realm=realm, target_kind="post", target_id__in=[post.pk for post in posts]):
            decisions.setdefault(decision.target_id, decision)
        context = {"request": request}
        response = paginator.get_paginated_response([submission_payload(post, request, decisions.get(post.pk), context=context) for post in posts])
        response.data["counts"] = approval_counts(request.entity, realm)
        return response


def approval_counts(actor, realm):
    caps = realm_capabilities(actor, realm)
    counts = {}
    if caps["can_review_posts"]:
        counts["posts"] = Post.objects.filter(tagging__entity_id=realm.entity_id, deleted_at=None, approval=0).distinct().count()
    if caps["can_review_invites"]:
        counts["invites"] = Invite.objects.filter(realm=realm, kind="invite", status="for_approval").count()
    if caps["can_review_join_requests"] or caps["can_decline_join_requests"]:
        counts["joins"] = Invite.objects.filter(realm=realm, kind="request", status="pending").count()
    return counts


class CommunityInviteReviewView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, realm_id, kind, invite_id):
        from community.invite_review import transition_invite
        if kind not in ("joins", "invites"):
            raise ValidationError({"kind": "Choose joins or invites."})
        if not isinstance(request.data, dict) or set(request.data) - {"action", "expected_status"}:
            raise ValidationError({"body": "Send an action and the current invitation state."})
        if request.data.get("action") not in ("approve", "reject") or request.data.get("expected_status") != ("for_approval" if kind == "invites" else "pending"):
            raise ValidationError({"action": "Approve or reject a pending item."})
        get_object_or_404(Invite, pk=invite_id, realm_id=realm_id, kind="request" if kind == "joins" else "invite")
        invite = transition_invite(actor=request.entity, account=request.user, invite_id=invite_id, realm_id=realm_id,
            action=request.data["action"], expected_status=request.data["expected_status"])
        return Response({"status": True, "result": InviteSerializer(invite).data})


class CommunityPostReviewView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, realm_id, post_id):
        fields = {"action", "expected_approval", "expected_version", "reason"}
        if not isinstance(request.data, dict) or set(request.data) - fields:
            raise ValidationError({"body": "Send an action, current state/version and optional reason."})
        decision = review_post(actor=request.entity, realm_id=realm_id, post_id=post_id,
            action=request.data.get("action"), expected_approval=request.data.get("expected_approval"),
            expected_version=request.data.get("expected_version"), reason=request.data.get("reason", ""))
        return Response({"status": True, "result": decision_payload(decision)})
