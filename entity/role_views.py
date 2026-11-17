from django.db import transaction
from django.db.models import Q
from django.db.models.deletion import ProtectedError
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from community.models import Realm
from community.role_policy import (
    assert_can_delegate_role, assert_can_edit_role, member_preset,
    feature_availability, require_realm_actor, require_role_configuration, validate_permission_codes, effective_role_permissions,
    notify_role_configuration_changed,
)
from entity.models import Role
from entity.permissions import Permission


def role_payload(role, actor=None, realm=None):
    editable = False
    permissions_editable = False
    assignable = False
    disabled_reason = None
    if actor is not None and realm is not None:
        try:
            assert_can_edit_role(actor, realm, role)
            editable = True
        except PermissionDenied:
            pass
        try:
            assert_can_edit_role(actor, realm, role, Permission.REALM_PERMISSIONS_MANAGE)
            permissions_editable = True
        except PermissionDenied:
            pass
        try:
            assert_can_delegate_role(actor, realm, None, role)
            assignable = True
        except (PermissionDenied, ValueError) as exc:
            disabled_reason = str(exc)
    return {
        "id": role.pk, "name": role.name, "code": role.code, "realm_id": role.realm_id,
        "is_default": role.is_default, "editable": editable, "permissions_editable": permissions_editable, "assignable": assignable,
        "disabled_reason": disabled_reason,
        "permissions": sorted(effective_role_permissions(role, realm)) if realm else [],
    }


def scoped_realm(request, *, lock=False):
    body = request.data if isinstance(request.data, dict) else {}
    query_realm = request.query_params.get("realm_id")
    if query_realm and body.get("realm_id") and str(body["realm_id"]) != query_realm:
        raise ValidationError({"realm_id": "Send one consistent realm scope."})
    realm_id = query_realm or body.get("realm_id")
    if not realm_id:
        raise ValidationError({"realm_id": "Choose a realm."})
    realms = Realm.objects.select_for_update() if lock else Realm.objects.all()
    realm = get_object_or_404(realms, pk=realm_id, is_active=True)
    require_realm_actor(getattr(request, "entity", None), realm)
    return realm


class RoleInput(serializers.Serializer):
    realm_id = serializers.CharField()
    name = serializers.CharField(max_length=150, required=False)
    permissions = serializers.ListField(child=serializers.CharField(), required=False)

    def to_internal_value(self, data):
        if not isinstance(data, dict) or set(data) - set(self.fields):
            raise ValidationError({"non_field_errors": ["Only realm_id, name and permissions may be supplied."]})
        return super().to_internal_value(data)


class RolesView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        realm = scoped_realm(request) if request.query_params.get("realm_id") else None
        roles = Role.objects.filter(realm=realm).prefetch_related("permissions").order_by("id") if realm else Role.objects.none()
        return Response({"status": True, "features": feature_availability(), "result": [role_payload(role, getattr(request, "entity", None), realm) for role in roles]})

    @transaction.atomic
    def post(self, request):
        realm = scoped_realm(request, lock=True)
        require_role_configuration(request.entity, realm)
        data = RoleInput(data=request.data)
        data.is_valid(raise_exception=True)
        if "name" not in data.validated_data:
            raise ValidationError({"name": "Enter a role name."})
        codes = data.validated_data["permissions"] if "permissions" in data.validated_data else member_preset(realm)
        entries = validate_permission_codes(request.entity, realm, codes)
        role = Role.objects.create(name=data.validated_data["name"], realm=realm)
        role.permissions.set(entries)
        transaction.on_commit(lambda: notify_role_configuration_changed(realm), robust=True)
        return Response({"status": True, "result": role_payload(role, request.entity, realm)}, status=201)


class RoleDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def put(self, request, role_id):
        realm = scoped_realm(request, lock=True)
        role = get_object_or_404(Role.objects.select_for_update().filter(realm=realm), pk=role_id)
        if not isinstance(request.data, dict):
            raise ValidationError("Send an object of role fields.")
        data = RoleInput(data={**request.data, "realm_id": realm.pk})
        data.is_valid(raise_exception=True)
        if 'name' in data.validated_data:
            assert_can_edit_role(request.entity, realm, role)
        if 'permissions' in data.validated_data:
            assert_can_edit_role(request.entity, realm, role, Permission.REALM_PERMISSIONS_MANAGE)
        if not {'name', 'permissions'} & set(data.validated_data):
            assert_can_edit_role(request.entity, realm, role)
        entries = None
        if "permissions" in data.validated_data:
            entries = validate_permission_codes(request.entity, realm, data.validated_data["permissions"])
        if "name" in data.validated_data:
            role.name = data.validated_data["name"]
            role.save(update_fields=["name"])
        if entries is not None:
            role.permissions.set(entries)
        transaction.on_commit(lambda: notify_role_configuration_changed(realm), robust=True)
        return Response({"status": True, "result": role_payload(role, request.entity, realm)})

    @transaction.atomic
    def delete(self, request, role_id):
        realm = scoped_realm(request, lock=True)
        role = get_object_or_404(Role.objects.select_for_update().filter(realm=realm), pk=role_id)
        assert_can_edit_role(request.entity, realm, role)
        if role.is_default:
            raise ValidationError("Default roles remain available for realm membership and ownership operations.")
        try:
            role.delete()
        except ProtectedError as exc:
            raise ValidationError("This role is still referenced by members or invitations.") from exc
        transaction.on_commit(lambda: notify_role_configuration_changed(realm), robust=True)
        return Response({"status": True})
