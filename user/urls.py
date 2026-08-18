from django.conf.urls import include
from django.urls import re_path, path
from rest_framework import routers

from user import views
from user.entity_switch_views import EntitySwitch, EntitySwitchBack

router = routers.DefaultRouter()

app_name = "user"

urlpatterns = [
    re_path("", include((router.urls, "user-routes"))),
    path(
        "auth/<str:username>/",
        views.UserAuthentication.as_view(),
        name="user-profile",
    ),
    path(
        "auth",
        views.UserAuthentication.as_view(),
        name="user-authentication",
    ),
    path(
        "tp_auth",
        views.ThirdPartyAuthentication.as_view(),
        name="user-tp-authentication",
    ),
    path(
        "me/export",
        views.AccountDataExport.as_view(),
        name="account-data-export",
    ),
    # Placed before the "me" re_path below, which is unanchored and so matches
    # ANY path containing "me". None of the current step names do, but a later
    # one ("deletion/remove") would be swallowed by it.
    path(
        "deletion/<str:step>",
        views.PublicAccountDeletion.as_view(),
        name="public-account-deletion",
    ),
    re_path("me", views.UserAccountManagement.as_view(), name="user-management"),
    # Before the unanchored "verification" pattern below, which would otherwise
    # swallow this path too - Django resolves in order.
    path(
        "verification/resend",
        views.ResendVerificationCode.as_view(),
        name="user-verification-resend",
    ),
    re_path("verification", views.CodeVerification.as_view(), name="user-verification"),
    re_path("contacts", views.UserContacts.as_view(), name="user-contacts"),
    path("search/<str:query>/", views.UserSearch.as_view(), name="user-search"),
    path(
        "policies",
        views.PolicyDocumentList.as_view(),
        name="policy-document-list",
    ),
    path(
        "policies/accept",
        views.PolicyConsentAccept.as_view(),
        name="policy-consent-accept",
    ),
    path("blocks", views.BlockedUserList.as_view(), name="blocked-user-list"),
    path("devices", views.DeviceSessionList.as_view(), name="device-session-list"),
    path("reports", views.ReportCreate.as_view(), name="report-create"),
    path("poke", views.PokeUser.as_view(), name="user-poke"),
    path("entity/switch", EntitySwitch.as_view(), name="entity-switch"),
    path("entity/switch-back", EntitySwitchBack.as_view(), name="entity-switch-back"),
]
