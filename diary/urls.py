from django.conf.urls import include
from django.urls import re_path, path
from rest_framework import routers

from diary import views

router = routers.DefaultRouter()

app_name = "diary"

urlpatterns = [
    re_path("", include((router.urls, "diary-routes"))),
    re_path("total", views.DiaryTotalView.as_view(), name="newsfeed-default"),
]
