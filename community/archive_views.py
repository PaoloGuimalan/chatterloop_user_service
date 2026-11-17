"""Delegated archive actions for the community's own authored posts only."""
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from community.models import Realm
from community.posts import community_for_post, prime_community_memberships
from community.role_policy import require_realm_actor
from community.views import Pagination
from entity.permissions import Permission
from entity.services.permission_resolver import has_active_permission
from newsfeed.models import Post
from newsfeed.serializers import PostSerializer
from newsfeed.services.post_cards import read_only_post_cards
from newsfeed.services.post_editing import archive_changes


def archive_realm(actor, realm_id, *, locked=False):
    realms = Realm.objects.select_for_update() if locked else Realm.objects.all()
    realm = get_object_or_404(realms, pk=realm_id, type='community', is_active=True)
    require_realm_actor(actor, realm)
    if not has_active_permission(actor, Permission.REALM_POST_ARCHIVE, realm, fresh=locked):
        raise PermissionDenied('You cannot manage this community’s archive.')
    return realm


class CommunityArchiveView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, realm_id):
        realm = archive_realm(request.entity, realm_id)
        query = Post.objects.filter(entity_id=realm.entity_id, on_feed='feed', deleted_at=None,
            is_archived=True).annotate(
                community_count=Count('tagging__entity_id', distinct=True,
                    filter=Q(tagging__entity__realms__type='community'))).filter(
                        community_count=1, tagging__entity_id=realm.entity_id)
        paginator = Pagination()
        posts = list(paginator.paginate_queryset(read_only_post_cards(query).order_by('-date_posted', '-pk'), request, view=self))
        cache = {}
        prime_community_memberships(posts, request.entity, cache)
        context = {'request': request, 'community_memberships': cache}
        rows = [{**PostSerializer(post, context=context).data, 'read_only': True, 'can_archive': True} for post in posts]
        return paginator.get_paginated_response(rows)


class CommunityArchivePostView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, realm_id, post_id):
        if not isinstance(request.data, dict) or set(request.data) != {'is_archived'}:
            raise ValidationError({'body': 'Send only is_archived.'})
        realm = archive_realm(request.entity, realm_id, locked=True)
        post = get_object_or_404(Post.objects.select_for_update(), pk=post_id,
            entity_id=realm.entity_id, on_feed='feed', deleted_at=None)
        destination = community_for_post(post)
        if destination is None or destination.pk != realm.pk:
            raise NotFound('Post not available')
        changes = archive_changes(post, request.data['is_archived'])
        if changes:
            Post.objects.filter(pk=post.pk).update(**changes)
            from community.post_events import notify_post_state_changed
            transaction.on_commit(lambda: notify_post_state_changed(post.pk), robust=True)
        # Content version, approval and platform removal are never changed.
        return Response({'status': True, 'changed': bool(changes), 'post': {
            'post_id': post.pk, 'is_archived': request.data['is_archived'], 'approval': post.approval}})
