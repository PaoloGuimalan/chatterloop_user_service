from django.urls import path

from entity import encryption_views as views

urlpatterns = [
    path("personal/", views.PersonalConfigurationsView.as_view(), name="encryption-configurations-personal"),
    path("personal/<uuid:configuration_id>/", views.PersonalConfigurationDetailView.as_view(), name="encryption-configuration-detail"),
    path("personal/<uuid:configuration_id>/activate/", views.ActivateConfigurationView.as_view(), name="encryption-configuration-activate"),
    path("personal/<uuid:configuration_id>/deactivate/", views.DeactivateConfigurationView.as_view(), name="encryption-configuration-deactivate"),
]
