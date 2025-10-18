from django.conf.urls import include
from django.urls import re_path, path
from rest_framework import routers

from user import views

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
    re_path("me", views.UserAccountManagement.as_view(), name="user-management"),
    re_path("verification", views.CodeVerification.as_view(), name="user-verification"),
    re_path("contacts", views.UserContacts.as_view(), name="user-contacts"),
    path("search/<str:query>/", views.UserSearch.as_view(), name="user-search"),
]
