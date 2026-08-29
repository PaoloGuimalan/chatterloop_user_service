from django.urls import path

from . import views

app_name = "interests"

urlpatterns = [
    path("", views.InterestListView.as_view(), name="interest-list"),
    path("mine/top/", views.MyTopInterestsView.as_view(), name="my-top-interests"),
    path("trending/", views.TrendingInterestsView.as_view(), name="trending-interests"),
    path("popular/", views.PopularTopicsView.as_view(), name="popular-topics"),
    # The searchable topic directory. Declared BEFORE topics/<slug>/posts/ so
    # the bare list is never read as a slug, and taking its query in ?q= rather
    # than in the path (the shape the other v2 search endpoints use) because
    # the no-query case IS a real request here - it is the trending list.
    path("topics/", views.TopicListView.as_view(), name="topic-list"),
    path(
        "topics/<str:slug>/posts/",
        views.TopicPostsView.as_view(),
        name="topic-posts",
    ),
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
