"""Keep the Entity registry in sync with its source records.

An Entity is minted whenever an Account or Realm is created. Entities are never
deleted when their source is removed: interaction rows (and Mongo/Cassandra
records) reference the entity id, so we keep it as a tombstone.
"""

from django.db.models.signals import post_save
from django.dispatch import receiver

from community.models import Realm
from user.models import Account

from entity.services import entity_for_account, entity_for_realm


@receiver(post_save, sender=Account, dispatch_uid="entity_mint_for_account")
def mint_entity_for_account(sender, instance, created, **kwargs):
    if created:
        entity_for_account(instance)


@receiver(post_save, sender=Realm, dispatch_uid="entity_mint_for_realm")
def mint_entity_for_realm(sender, instance, created, **kwargs):
    if created:
        entity_for_realm(instance)
