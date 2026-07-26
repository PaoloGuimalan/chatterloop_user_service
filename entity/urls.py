from django.urls import path

from entity import network_views, search_views, views

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
    # Network section endpoints (redesigned Contacts page) - NEW routes;
    # /api/user/contacts is pinned by mobile and stays as-is.
    path(
        "network/overview",
        network_views.NetworkOverview.as_view(),
        name="entity-network-overview",
    ),
    path(
        "network/connections",
        network_views.NetworkConnections.as_view(),
        name="entity-network-connections",
    ),
    path(
        "network/followers",
        network_views.NetworkFollowers.as_view(),
        name="entity-network-followers",
    ),
    path(
        "network/following",
        network_views.NetworkFollowing.as_view(),
        name="entity-network-following",
    ),
]
