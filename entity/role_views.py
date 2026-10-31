from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from entity.models import Role


class RolesView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        roles = Role.objects.prefetch_related("permissions").order_by("id")
        return Response({"status": True, "result": [
            {"id": role.pk, "name": role.name, "is_default": role.is_default,
             "permissions": [entry.codename for entry in role.permissions.all() if entry.is_active]}
            for role in roles
        ]})
