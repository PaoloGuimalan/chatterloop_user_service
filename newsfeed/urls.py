from django.conf.urls import include
from django.urls import re_path, path
from rest_framework import routers

from newsfeed import views

router = routers.DefaultRouter()

app_name = "newsfeed"

urlpatterns = [
    re_path("", include((router.urls, "newsfeed-routes"))),
    re_path("default", views.NewsfeedView.as_view(), name="newsfeed-default"),
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
]
