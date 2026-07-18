from django.urls import path

from entity import views

app_name = "entity"

urlpatterns = [
    path("me/modules", views.MyAllowedModules.as_view(), name="my-allowed-modules"),
]
