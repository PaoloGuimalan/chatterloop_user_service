from django.contrib.auth.backends import BaseBackend
from .models import Account
from dotenv import load_dotenv
import os
import jwt

load_dotenv()


class AutheticationBackend(BaseBackend):
    JWT_TOKEN = os.getenv("JWT_TOKEN")

    def authenticate(self, request):
        try:
            token = request.headers.get("x-access-token")

            if not token:
                return None

            decoded_header = jwt.decode(token, self.JWT_TOKEN, algorithms=["HS256"])
            decoded_id = decoded_header["id"]

            user = Account.objects.get(id=decoded_id)
            return (user, True)
        except Account.DoesNotExist:
            return None
        except:
            return None

    def get_user(self, user_id):
        try:
            return Account.objects.get(pk=user_id)
        except Account.DoesNotExist:
            return None

    def authenticate_header(self, request):
        return "Bearer"
