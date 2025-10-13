from django.apps import AppConfig
from user_service.services.mongodb import MongoDBClient


class AuthenticationConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "user"

    def ready(self):
        MongoDBClient.get_connection()
        print("MongoDB connection initialized at project startup")
