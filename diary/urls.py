from django.conf.urls import include
from django.urls import re_path, path
from rest_framework import routers

from diary import views

router = routers.DefaultRouter()

app_name = "diary"

urlpatterns = [
    re_path("", include((router.urls, "diary-routes"))),
    path("entry/<str:entry_id>/", views.DiaryCRUDView.as_view(), name="diary-total"),
    path("entries/", views.DiaryListView.as_view(), name="diary-total"),
    path("total/<str:username>/", views.DiaryTotalView.as_view(), name="diary-total"),
    path("moods/", views.MoodListView.as_view(), name="diary-moods"),
]
