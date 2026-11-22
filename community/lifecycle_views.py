from dataclasses import asdict

from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from community.access import active_realms, descendant_realms, effective_realm_access
from community.lifecycle import deactivate_realm, page_ownership_blockers, reactivate_realm, request_realm_deletion
from community.lifecycle_policy import lifecycle_capabilities, list_recoverable_realms
from community.models import Realm, Member
from community.role_policy import role_management_actor
from entity.services.acting import can_act_as_entity
from entity.services.permission_applicability import effective_realm_kind


class DeleteInput(serializers.Serializer):
    confirm_name = serializers.CharField(max_length=150, trim_whitespace=True)


class ReactivateInput(serializers.Serializer):
    actor_entity_id = serializers.CharField(max_length=40)


class RealmLifecycleView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, realm_id):
        realm = get_object_or_404(active_realms(Realm.objects.select_related('parent')), pk=realm_id)
        actor = role_management_actor(request, realm)
        caps = lifecycle_capabilities(request.user, actor, realm)
        if not caps['can_deactivate_realm'] and not caps['can_delete_realm']:
            raise PermissionDenied('You cannot manage this realm lifecycle.')
        blockers = page_ownership_blockers(realm)
        visible_blockers = list(blockers.values('id', 'name')) if realm.type == 'page' and can_act_as_entity(request.user, realm.entity) else []
        parent=realm.parent
        return_server_id=(str(parent.pk) if parent and parent.type=='server' and effective_realm_access(parent).allowed
            and (not parent.is_private or Member.objects.filter(realm=parent,entity=actor).exists()) else None)
        return Response({'status': True, 'result': dict(realm_id=str(realm.pk), name=realm.name,
            return_server_id=return_server_id,
            kind=effective_realm_kind(realm), capabilities=caps, child_count=descendant_realms(realm.pk).exclude(pk=realm.pk).count(),
            owned_realm_blockers=visible_blockers, owned_realm_blocker_count=blockers.count(),
            consequences={'preserves_data_on_deactivate': True, 'delete_is_permanent': True,
                          'includes_channels': realm.type == 'server'})})


class RealmDeactivateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, realm_id):
        realm = get_object_or_404(Realm, pk=realm_id)
        result = deactivate_realm(account=request.user, actor=role_management_actor(request, realm), realm_id=realm_id)
        return Response({'status': True, 'result': result})


class RealmDeleteView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, realm_id):
        body = DeleteInput(data=request.data)
        body.is_valid(raise_exception=True)
        realm = get_object_or_404(Realm, pk=realm_id)
        result = request_realm_deletion(account=request.user, actor=role_management_actor(request, realm),
                                       realm_id=realm_id, **body.validated_data)
        return Response({'status': True, 'result': result}, status=202)


class RealmReactivateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, realm_id):
        body = ReactivateInput(data=request.data)
        body.is_valid(raise_exception=True)
        result = reactivate_realm(account=request.user, realm_id=realm_id, **body.validated_data)
        return Response({'status': True, 'result': result})


class DeactivatedRealmsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        result = list_recoverable_realms(request.user, cursor=request.query_params.get('cursor'),
                                         limit=request.query_params.get('limit', 20))
        return Response({'status': True, 'result': asdict(result)})
