from django.conf.urls import include
from django.urls import re_path, path
from rest_framework import routers

from newsfeed import views, moment_views

router = routers.DefaultRouter()

app_name = "newsfeed"

urlpatterns = [
    re_path("", include((router.urls, "newsfeed-routes"))),
    re_path("default", views.NewsfeedView.as_view(), name="newsfeed-default"),
    # Search v2 (redesigned Search page) - NEW route; every pre-existing
    # newsfeed route is pinned by the live mobile app and stays untouched.
    path(
        "search/v2/posts/<str:query>/",
        views.NewsfeedPostSearchView.as_view(),
        name="newsfeed-post-search-v2",
    ),
    path(
        "profile/<str:username>/",
        views.NewsfeedProfileView.as_view(),
        name="newsfeed-default",
    ),
    path(
        "preview/<str:post_id>/",
        views.NewsfeedPostPreviewView.as_view(),
        name="newsfeed-preview",
    ),
    path(
        "emojis",
        views.EmojisView.as_view(),
        name="newsfeed-emojis",
    ),
    path(
        "reaction",
        views.PostReactionsView.as_view(),
        name="newsfeed-reactions",
    ),
    path(
        "total_reactions/<str:post_id>/",
        views.ReactionsCountView.as_view(),
        name="newsfeed-total-reactions",
    ),
    path(
        "post_activities",
        views.ActivityCountView.as_view(),
        name="newsfeed-post-activities",
    ),
    path(
        "comments",
        views.CommentsView.as_view(),
        name="newsfeed-comments",
    ),
    # Comment reactions - mirrors reaction / total_reactions above one level
    # down. NEW routes; nothing the live mobile app calls is touched.
    path(
        "comment_reaction",
        views.CommentReactionsView.as_view(),
        name="newsfeed-comment-reactions",
    ),
    path(
        "comment_total_reactions/<str:comment_id>/",
        views.CommentReactionsCountView.as_view(),
        name="newsfeed-comment-total-reactions",
    ),
    path(
        "saves",
        views.PostSaveView.as_view(),
        name="newsfeed-saves",
    ),
    path(
        "link-preview",
        views.LinkPreviewView.as_view(),
        name="newsfeed-link-preview",
    ),
    path(
        "link-preview/image",
        views.LinkPreviewImageProxyView.as_view(),
        name="newsfeed-link-preview-image",
    ),
    # Moments & thoughts - reads, views, reactions' gating and in-place edits
    # (creation is Node's /posts/moments/create and /posts/thoughts/create).
    # All NEW routes. Fixed paths come before the <post_id> ones so "tray",
    # "status" and "rail" are never read as a post id.
    path(
        "moments/tray/",
        moment_views.MomentTrayView.as_view(),
        name="newsfeed-moments-tray",
    ),
    path(
        "moments/status/",
        moment_views.MomentStatusView.as_view(),
        name="newsfeed-moments-status",
    ),
    path(
        "moments/entity/<str:entity_id>/",
        moment_views.EntityMomentsView.as_view(),
        name="newsfeed-moments-entity",
    ),
    path(
        "moments/<str:post_id>/seen/",
        moment_views.MomentSeenView.as_view(),
        name="newsfeed-moments-seen",
    ),
    path(
        "moments/<str:post_id>/viewers/",
        moment_views.MomentViewersView.as_view(),
        name="newsfeed-moments-viewers",
    ),
    path(
        "moments/<str:post_id>/",
        moment_views.MomentDetailView.as_view(),
        name="newsfeed-moments-detail",
    ),
    path(
        "thoughts/",
        moment_views.ThoughtsView.as_view(),
        name="newsfeed-thoughts",
    ),
    path(
        "thoughts/rail/",
        moment_views.ThoughtsRailView.as_view(),
        name="newsfeed-thoughts-rail",
    ),
    path(
        "thoughts/<str:post_id>/seen/",
        moment_views.ThoughtSeenView.as_view(),
        name="newsfeed-thoughts-seen",
    ),
    path(
        "thoughts/<str:post_id>/viewers/",
        moment_views.ThoughtViewersView.as_view(),
        name="newsfeed-thoughts-viewers",
    ),
    path(
        "thoughts/<str:post_id>/",
        moment_views.ThoughtDetailView.as_view(),
        name="newsfeed-thoughts-detail",
    ),
    path(
        "archive/moments/",
        moment_views.MomentArchiveView.as_view(),
        name="newsfeed-archive-moments",
    ),
]
