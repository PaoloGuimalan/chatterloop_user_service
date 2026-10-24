from django.conf.urls import include
from django.urls import re_path, path
from rest_framework import routers

from community import views
from community.community_views import (
    CommunitiesView,
    CommunityDetailView,
    CommunityJoinView,
    CommunityLeaveView,
)

router = routers.DefaultRouter()

app_name = "community"

urlpatterns = [
    path("communities", CommunitiesView.as_view(), name="communities"),
    path(
        "communities/<str:realm_id>",
        CommunityDetailView.as_view(),
        name="community-detail",
    ),
    path(
        "communities/<str:realm_id>/join",
        CommunityJoinView.as_view(),
        name="community-join",
    ),
    path(
        "communities/<str:realm_id>/leave",
        CommunityLeaveView.as_view(),
        name="community-leave",
    ),
    re_path("", include((router.urls, "community-routes"))),
    re_path("top", views.TopRealms.as_view(), name="community-top"),
    re_path("realm-followers", views.FollowersView.as_view(), name="followers-list"),
    re_path("follow", views.FollowRealmView.as_view(), name="community-follows"),
    # NEW (search redesign): one-click join for public group chats. Existing
    # membership routes (invites) are pinned by mobile and stay untouched.
    path("join/v2", views.JoinGroupRealmV2.as_view(), name="community-join-v2"),
    re_path("my-list", views.MyRealms.as_view(), name="community-list"),
    re_path("members", views.RealmMembersView.as_view(), name="members-list"),
    re_path("invites", views.InviteView.as_view(), name="community-invites"),
]
