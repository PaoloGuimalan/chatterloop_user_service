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
]
