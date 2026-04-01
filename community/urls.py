from django.conf.urls import include
from django.urls import re_path, path
from rest_framework import routers

from community import views

router = routers.DefaultRouter()

app_name = "community"

urlpatterns = [
    re_path("", include((router.urls, "community-routes"))),
    re_path("top", views.TopRealms.as_view(), name="community-top"),
    re_path("follow", views.FollowRealmView.as_view(), name="community-follows"),
    re_path("my-list", views.MyRealms.as_view(), name="community-list"),
]
