"""Shared direct-object guard for the existing post interaction endpoints."""

from django.shortcuts import get_object_or_404
from rest_framework.exceptions import ValidationError

from community.posts import assert_post_access, request_anonymity


class CommunityPostAccessMixin:
    allow_author_withdrawal = False
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
        post = get_object_or_404(Post, post_id=post_id) if post_id else None
        if comment_id:
            comment = get_object_or_404(
                Comment.objects.select_related("post"), comment_id=comment_id
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
            posts.extend(Post.objects.filter(post_id__in=body["post_ids"]))
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
