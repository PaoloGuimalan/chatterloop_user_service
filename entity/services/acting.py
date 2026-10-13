"""Current account authority, distinct from the active entity's capabilities."""
from django.apps import apps


def can_act_as_entity(account, entity) -> bool:
    personal_id = getattr(account, "entity_id", None)
    if personal_id is None or entity is None:
        return False
    if str(personal_id) == str(entity.id):
        return True
    if getattr(entity, "type", None) != "realm":
        return False
    Member = apps.get_model("community", "Member")
    return Member.objects.filter(entity_id=personal_id, realm__entity_id=entity.id,
                                 realm__is_active=True, role__in=("owner", "admin")).exists()


def is_switchable_realm(realm) -> bool:
    return bool(realm.is_active and realm.type in {"page", "server", "group", "conference", "channel", "voice"})
