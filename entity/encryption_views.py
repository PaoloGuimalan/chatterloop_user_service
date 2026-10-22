from rest_framework.parsers import JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from entity.encryption_serializers import (
    ActivateConfigurationSerializer, CreateConfigurationSerializer,
    DeactivateConfigurationSerializer, RenameConfigurationSerializer,
)
from entity.encryption_throttles import AccountMutationThrottle, IPMutationThrottle
from entity.services import encryption


class EncryptionView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser]

    def get_throttles(self):
        return [] if self.request.method in ("GET", "HEAD", "OPTIONS") else [AccountMutationThrottle(), IPMutationThrottle()]

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response

    def handle_exception(self, exc):
        if isinstance(exc, encryption.EncryptionError):
            return Response({"status": False, "code": exc.code, "message": exc.message}, status=exc.status)
        response = super().handle_exception(exc)
        # DRF also handles Django PermissionDenied/Http404. Normalize the
        # converted response too, without returning the authentication detail.
        detail = response.data.get("detail") if isinstance(response.data, dict) else None
        code = getattr(exc, "default_code", getattr(detail, "code", "request_failed"))
        message = "Invalid configuration request." if response.status_code == 400 else "Configuration request could not be completed."
        response.data = {"status": False, "code": str(code), "message": message}
        return response

    def mutate(self, serializer_class, service, configuration_id=None):
        serializer = serializer_class(data=self.request.data)
        serializer.is_valid(raise_exception=True)
        args = [self.request.user]
        if configuration_id is not None:
            args.append(configuration_id)
        result = service(*args, serializer.validated_data)
        status = 200
        if isinstance(result, tuple):
            result, created = result
            status = 201 if created else 200
        return Response({"status": True, "result": result}, status=status)


class PersonalConfigurationsView(EncryptionView):
    def get(self, request):
        return Response({"status": True, "result": encryption.get_personal_configurations(request.user, request.query_params.get("purpose"))})

    def post(self, request):
        return self.mutate(CreateConfigurationSerializer, encryption.register_configuration)


class PersonalConfigurationDetailView(EncryptionView):
    def patch(self, request, configuration_id):
        return self.mutate(RenameConfigurationSerializer, encryption.rename_configuration, configuration_id)


class ActivateConfigurationView(EncryptionView):
    def post(self, request, configuration_id):
        return self.mutate(ActivateConfigurationSerializer, encryption.activate_configuration, configuration_id)


class DeactivateConfigurationView(EncryptionView):
    def post(self, request, configuration_id):
        return self.mutate(DeactivateConfigurationSerializer, encryption.deactivate_configuration, configuration_id)
