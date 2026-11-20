"""Configure actual realm-owned role grants without a separate override table."""
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from community.models import Realm
from community.role_policy import (assert_can_edit_role, feature_availability, realm_capabilities,
    require_role_configuration, validate_permission_codes, require_realm_actor, ADMINISTRATION, INTRINSIC,
    notify_role_configuration_changed, role_management_actor)
from entity.models import PermissionCatalogEntry, Role
from entity.permissions import Permission
from entity.role_views import role_payload
from entity.services.permission_catalog_cache import get_catalog
from entity.services.permission_applicability import effective_realm_kind, sync_applicable_role_grants
from entity.services.permission_resolver import has_active_permission


class RoleGrantsInput(serializers.Serializer):
    role_id = serializers.IntegerField(min_value=1)
    permissions = serializers.ListField(child=serializers.CharField())


class PermissionsInput(serializers.Serializer):
    roles = RoleGrantsInput(many=True)


class RealmPermissionsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, realm_id):
        realm = get_object_or_404(Realm, pk=realm_id, is_active=True)
        actor = role_management_actor(request, realm)
        require_role_configuration(actor, realm, Permission.REALM_PERMISSIONS_MANAGE)
        roles = realm.owned_roles.prefetch_related("permissions__realm_types").order_by("id")
        catalog = list(PermissionCatalogEntry.objects.filter(scope="realm", is_active=True,
            realm_types__code=effective_realm_kind(realm)).order_by("code"))
        details = {entry.code: {"code": entry.code, "name": entry.name or entry.code, "description": entry.description} for entry in catalog}
        tier = require_realm_actor(actor, realm)
        context = {'catalog': get_catalog()}
        return Response({"status": True, "result": {
            "features": feature_availability(), "capabilities": realm_capabilities(actor, realm),
            "roles": [role_payload(role, actor, realm, permission_details=details, permission_context=context) for role in roles],
            "catalog": [{**details[entry.code], "permission": entry.code,
                "grantable": entry.code not in INTRINSIC and (tier == 'owner' or entry.code not in ADMINISTRATION)
                    and has_active_permission(actor, entry.code, realm, context=context)} for entry in catalog],
        }})

    @transaction.atomic
    def put(self, request, realm_id):
        realm = get_object_or_404(Realm.objects.select_for_update(), pk=realm_id, is_active=True)
        actor = role_management_actor(request, realm)
        require_role_configuration(actor, realm, Permission.REALM_PERMISSIONS_MANAGE)
        serializer = PermissionsInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        roles = {role.pk: role for role in Role.objects.select_for_update().filter(realm=realm).order_by("pk")}
        changes = {}
        for row in serializer.validated_data["roles"]:
            role_id = row["role_id"]
            if role_id in changes or role_id not in roles:
                raise ValidationError({"roles": "Choose distinct roles belonging to this realm."})
            role = roles[role_id]
            assert_can_edit_role(actor, realm, role, Permission.REALM_PERMISSIONS_MANAGE)
            changes[role_id] = validate_permission_codes(actor, realm, row["permissions"])
        for role_id, entries in changes.items():
            sync_applicable_role_grants(roles[role_id], entries)
        if changes:
            transaction.on_commit(lambda: notify_role_configuration_changed(realm), robust=True)
        return Response({"status": True})
