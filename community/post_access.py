"""Shared direct-object guard for the existing post interaction endpoints."""

from contextlib import ExitStack

from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import PermissionDenied, ValidationError

from community.posts import assert_post_access, request_anonymity


class CommunityPostAccessMixin:
    allow_author_withdrawal = False

    def dispatch(self, request, *args, **kwargs):
        # Authentication runs in initial(), before opening a write transaction.
        # Keep its scope locks until the existing handler has finished writing.
        with ExitStack() as self._post_write_context:
            self._post_write_atomic = False
            response = super().dispatch(request, *args, **kwargs)
            if self._post_write_atomic and response.status_code >= 400:
                transaction.set_rollback(True)
            return response

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        from newsfeed.models import Comment, Post

        body = request.data if isinstance(request.data, dict) else {}
        post_id = (
            kwargs.get("post_id")
            or request.query_params.get("post_id")
            or body.get("post_id")
        )
        comment_id = (
            kwargs.get("comment_id")
            or request.query_params.get("comment_id")
            or body.get("comment_id")
        )
        post = get_object_or_404(Post.objects.select_related('entity'), post_id=post_id) if post_id else None
        if comment_id:
            comment = get_object_or_404(
                Comment.objects.select_related("post__entity"), comment_id=comment_id
            )
            if post is not None and str(comment.post_id) != str(post.pk):
                raise ValidationError(
                    {"comment_id": "Comment does not belong to this post"}
                )
            post = comment.post
        parent_id = request.query_params.get("parent_id") or body.get("parent_id")
        if parent_id:
            parent = get_object_or_404(Comment, comment_id=parent_id)
            if post is None or str(parent.post_id) != str(post.pk):
                raise ValidationError(
                    {"parent_id": "Comment does not belong to this post"}
                )

        posts = [post] if post is not None else []
        if isinstance(body.get("post_ids"), list):
            posts.extend(Post.objects.select_related('entity').filter(post_id__in=body["post_ids"]))
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            from community.access import lock_active_realm_scopes
            from community.models import Realm
            from community.posts import community_for_post
            from entity.services.acting import can_act_as_entity

            actor = getattr(request, 'entity', None)
            entity_ids = {current.entity_id for current in posts if current.entity.type == 'realm'}
            if actor is not None and actor.type == 'realm':
                entity_ids.add(actor.pk)
            scopes = list(Realm.objects.filter(entity_id__in=entity_ids)) if entity_ids else []
            scopes.extend(realm for current in posts if (realm := community_for_post(current)) is not None)
            if scopes:
                self._post_write_context.enter_context(transaction.atomic())
                self._post_write_atomic = True
                lock_active_realm_scopes(scopes)
                if actor is not None and actor.type == 'realm' and not can_act_as_entity(request.user, actor):
                    raise PermissionDenied('You cannot use this identity.')
                # Discard pre-lock snapshots, including destination caches.
                posts = list(Post.objects.select_related('entity').filter(pk__in=[current.pk for current in posts]))
        for current in posts:
            # Only the owning post-delete endpoint may withdraw a hidden
            # submission. Comment/reaction/tag DELETE endpoints keep all guards.
            if (self.allow_author_withdrawal and request.method == 'DELETE'
                    and current.approval in (0, 2) and getattr(request, 'entity', None) is not None
                    and str(current.entity_id) == str(request.entity.pk)):
                from community.posts import community_for_post
                if community_for_post(current) is not None:
                    continue
            assert_post_access(
                current,
                request,
                contribute=request.method not in ("GET", "HEAD", "OPTIONS"),
            )
            if "is_anonymous" in body:
                request_anonymity(request, current)
