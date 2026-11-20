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
    notify_role_configuration_changed, role_management_actor,
)
from entity.models import PermissionCatalogEntry, Role
from entity.permissions import Permission
from entity.services.permission_applicability import effective_realm_kind, sync_applicable_role_grants
from entity.services.permission_catalog_cache import get_catalog


def permission_metadata(realm):
    return {entry.code: {"code": entry.code, "name": entry.name or entry.code,
                         "description": entry.description}
            for entry in PermissionCatalogEntry.objects.filter(scope="realm", is_active=True, realm_types__code=effective_realm_kind(realm))}


def role_payload(role, actor=None, realm=None, *, permission_details=None, permission_context=None):
    context = permission_context if permission_context is not None else {'catalog': get_catalog()}
    editable = False
    permissions_editable = False
    assignable = False
    disabled_reason = None
    if actor is not None and realm is not None:
        try:
            assert_can_edit_role(actor, realm, role, fresh=False, context=context)
            editable = True
        except PermissionDenied:
            pass
        try:
            assert_can_edit_role(actor, realm, role, Permission.REALM_PERMISSIONS_MANAGE, fresh=False, context=context)
            permissions_editable = True
        except PermissionDenied:
            pass
        try:
            assert_can_delegate_role(actor, realm, None, role, fresh=False, context=context)
            assignable = True
        except (PermissionDenied, ValueError) as exc:
            disabled_reason = str(exc)
    codes = sorted(effective_role_permissions(role, realm)) if realm else []
    if permission_details is None:
        permission_details = permission_metadata(realm)
    return {
        "id": role.pk, "name": role.name, "code": role.code, "realm_id": role.realm_id,
        "is_default": role.is_default, "editable": editable, "permissions_editable": permissions_editable, "assignable": assignable,
        "disabled_reason": disabled_reason,
        "permissions": codes,
        "permission_details": [permission_details[code] for code in codes if code in permission_details],
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
    require_realm_actor(role_management_actor(request, realm), realm)
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
        roles = Role.objects.filter(realm=realm).prefetch_related("permissions__realm_types").order_by("id") if realm else Role.objects.none()
        details = permission_metadata(realm) if realm else {}
        context = {'catalog': get_catalog()}
        return Response({"status": True, "features": feature_availability(), "result": [role_payload(role, role_management_actor(request, realm), realm, permission_details=details, permission_context=context) for role in roles]})

    @transaction.atomic
    def post(self, request):
        realm = scoped_realm(request, lock=True)
        actor = role_management_actor(request, realm)
        require_role_configuration(actor, realm)
        data = RoleInput(data=request.data)
        data.is_valid(raise_exception=True)
        if "name" not in data.validated_data:
            raise ValidationError({"name": "Enter a role name."})
        codes = data.validated_data["permissions"] if "permissions" in data.validated_data else member_preset(realm)
        entries = validate_permission_codes(actor, realm, codes)
        role = Role.objects.create(name=data.validated_data["name"], realm=realm)
        sync_applicable_role_grants(role, entries)
        transaction.on_commit(lambda: notify_role_configuration_changed(realm), robust=True)
        return Response({"status": True, "result": role_payload(role, actor, realm)}, status=201)


class RoleDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def put(self, request, role_id):
        realm = scoped_realm(request, lock=True)
        actor = role_management_actor(request, realm)
        role = get_object_or_404(Role.objects.select_for_update().filter(realm=realm), pk=role_id)
        if not isinstance(request.data, dict):
            raise ValidationError("Send an object of role fields.")
        data = RoleInput(data={**request.data, "realm_id": realm.pk})
        data.is_valid(raise_exception=True)
        if 'name' in data.validated_data:
            assert_can_edit_role(actor, realm, role)
        if 'permissions' in data.validated_data:
            assert_can_edit_role(actor, realm, role, Permission.REALM_PERMISSIONS_MANAGE)
        if not {'name', 'permissions'} & set(data.validated_data):
            assert_can_edit_role(actor, realm, role)
        entries = None
        if "permissions" in data.validated_data:
            entries = validate_permission_codes(actor, realm, data.validated_data["permissions"])
        if "name" in data.validated_data:
            role.name = data.validated_data["name"]
            role.save(update_fields=["name"])
        if entries is not None:
            sync_applicable_role_grants(role, entries)
        transaction.on_commit(lambda: notify_role_configuration_changed(realm), robust=True)
        return Response({"status": True, "result": role_payload(role, actor, realm)})

    @transaction.atomic
    def delete(self, request, role_id):
        realm = scoped_realm(request, lock=True)
        actor = role_management_actor(request, realm)
        role = get_object_or_404(Role.objects.select_for_update().filter(realm=realm), pk=role_id)
        assert_can_edit_role(actor, realm, role)
        if role.is_default:
            raise ValidationError("Default roles remain available for realm membership and ownership operations.")
        try:
            role.delete()
        except ProtectedError as exc:
            raise ValidationError("This role is still referenced by members or invitations.") from exc
        transaction.on_commit(lambda: notify_role_configuration_changed(realm), robust=True)
        return Response({"status": True})
