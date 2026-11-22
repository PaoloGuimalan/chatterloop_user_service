from django.conf.urls import include
from django.urls import re_path, path
from rest_framework import routers

from community import views
from community.lifecycle_views import (DeactivatedRealmsView, RealmLifecycleView, RealmDeactivateView,
                                       RealmDeleteView, RealmReactivateView)
from community.permission_views import RealmPermissionsView
from community.approval_views import CommunityApprovalsView, CommunityPostReviewView, CommunityInviteReviewView
from community.admin_notifications import CommunityNotificationsView
from community.archive_views import CommunityArchiveView, CommunityArchivePostView
from community.community_views import (
    CommunitiesView,
    CommunityDetailView,
    CommunityJoinView,
    CommunityLeaveView,
)

router = routers.DefaultRouter()

app_name = "community"

urlpatterns = [
    path('realms/deactivated', DeactivatedRealmsView.as_view(), name='realm-deactivated'),
    path('realms/<str:realm_id>/lifecycle', RealmLifecycleView.as_view(), name='realm-lifecycle'),
    path('realms/<str:realm_id>/deactivate', RealmDeactivateView.as_view(), name='realm-deactivate'),
    path('realms/<str:realm_id>/delete', RealmDeleteView.as_view(), name='realm-delete'),
    path('realms/<str:realm_id>/reactivate', RealmReactivateView.as_view(), name='realm-reactivate'),
    path('communities/<str:realm_id>/archive', CommunityArchiveView.as_view(), name='community-archive'),
    path('communities/<str:realm_id>/archive/<str:post_id>', CommunityArchivePostView.as_view(), name='community-archive-post'),
    path("communities/<str:realm_id>/approvals", CommunityApprovalsView.as_view(), name="community-approvals"),
    path("communities/<str:realm_id>/approvals/posts/<str:post_id>", CommunityPostReviewView.as_view(), name="community-post-review"),
    path("communities/<str:realm_id>/approvals/<str:kind>/<str:invite_id>", CommunityInviteReviewView.as_view(), name="community-invite-review"),
    path("realms/<str:realm_id>/permissions", RealmPermissionsView.as_view(), name="realm-permissions"),
    path("communities", CommunitiesView.as_view(), name="communities"),
    path(
        "communities/<str:realm_id>/notifications",
        CommunityNotificationsView.as_view(),
        name="community-notifications",
    ),
    path(
        "communities/<str:realm_id>/notifications/read",
        CommunityNotificationsView.as_view(),
        name="community-notifications-read",
    ),
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
