from django.urls import path

from . import views

app_name = "interests"

urlpatterns = [
    path("", views.InterestListView.as_view(), name="interest-list"),
    path("mine/top/", views.MyTopInterestsView.as_view(), name="my-top-interests"),
    path("trending/", views.TrendingInterestsView.as_view(), name="trending-interests"),
    path(
        "overrides/",
        views.EntityInterestOverrideListView.as_view(),
        name="interest-override-list",
    ),
    path(
        "overrides/create/",
        views.EntityInterestOverrideView.as_view(),
        name="interest-override-create",
    ),
    path(
        "overrides/<str:override_id>/",
        views.EntityInterestOverrideView.as_view(),
        name="interest-override-detail",
    ),
]
