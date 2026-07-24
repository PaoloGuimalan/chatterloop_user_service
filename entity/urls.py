from django.urls import path

from entity import search_views, views

app_name = "entity"

urlpatterns = [
    path("me/modules", views.MyAllowedModules.as_view(), name="my-allowed-modules"),
    path("search/<str:query>/", views.EntitySearch.as_view(), name="entity-search"),
    # Search v2 section endpoints (redesigned Search page) - NEW routes; the
    # unified search/<query>/ above is pinned by post tagging and stays as-is.
    path(
        "search/v2/overview/<str:query>/",
        search_views.SearchOverviewV2.as_view(),
        name="entity-search-v2-overview",
    ),
    path(
        "search/v2/people/<str:query>/",
        search_views.SearchPeopleV2.as_view(),
        name="entity-search-v2-people",
    ),
    path(
        "search/v2/realms/<str:query>/",
        search_views.SearchRealmsV2.as_view(),
        name="entity-search-v2-realms",
    ),
]
