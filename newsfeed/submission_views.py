"""Private author reads; ordinary feeds and direct previews keep their guards."""
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from community.models import Approvals
from community.posts import prime_community_memberships
from community.review_serializers import submission_payload
from community.views import Pagination
from newsfeed.models import Post
from newsfeed.services.post_cards import read_only_post_cards
from newsfeed.services.post_visibility import available_post_author_filter
from community.access import active_realms
from community.models import Realm


def author_submissions(entity):
    return Post.objects.filter(available_post_author_filter(), entity=entity, on_feed="feed", approval__in=[0, 2], deleted_at=None,
        tagging__entity__realms__pk__in=active_realms(Realm.objects.all()).values('pk')).annotate(
        community_count=Count("tagging__entity_id", distinct=True,
            filter=Q(tagging__entity__realms__type="community")),
    ).filter(community_count=1).order_by("-date_posted", "-pk")


def serialize_submissions(posts, request):
    from newsfeed.moment_views import _shared_previews
    posts = list(posts)
    cache = {}
    prime_community_memberships(posts, request.entity, cache)
    context = {"request": request, "community_memberships": cache}
    decisions = {}
    for decision in Approvals.objects.filter(target_kind="post", target_id__in=[p.pk for p in posts]):
        decisions.setdefault(decision.target_id, decision)
    shared = _shared_previews(request.entity, posts, membership_cache=cache)
    return [{**submission_payload(post, request, decisions.get(post.pk), context=context),
        "read_only": True, "shared_preview": shared.get(post.pk)} for post in posts]


class AuthorSubmissionsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        paginator = Pagination()
        page = paginator.paginate_queryset(read_only_post_cards(author_submissions(request.entity)), request, view=self)
        return paginator.get_paginated_response(serialize_submissions(page, request))


class AuthorSubmissionDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, post_id):
        post = get_object_or_404(read_only_post_cards(author_submissions(request.entity)), pk=post_id)
        return Response({"status": True, "result": serialize_submissions([post], request)[0]})
