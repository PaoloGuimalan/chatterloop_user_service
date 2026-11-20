"""Additive applicability metadata; existing grant and relationship IDs stay intact."""
from django.db import migrations, models


ALL_TYPES = ['channel', 'community', 'conference', 'group', 'page', 'server', 'voice']
REALM_TYPES_BY_CODE = {
    'realm.identity.act': ['page'],
    'realm.follower.remove': ['page'],
    'realm.channel.create': ['server'],
    'realm.post.submit': ['community'],
    'realm.post.publish': ['community'],
    'realm.post.review': ['community'],
    'realm.post.takedown': ['community'],
    'realm.post.archive': ['community'],
    'realm.invite.publish': ['community'],
    'realm.invite.review': ['community'],
    'realm.notifications.view': ['community'],
    'realm.request.approve': ['community', 'conference', 'server'],
    'realm.request.decline': ['community', 'conference', 'server'],
    'realm.invite.create': ['community', 'conference', 'group', 'page', 'server'],
    'realm.invite.revoke': ['community', 'conference', 'group', 'page', 'server'],
    'realm.media.update': ['community', 'group', 'page', 'server'],
    'realm.member.add': ['channel', 'group', 'server', 'voice'],
    'realm.update': ALL_TYPES,
    'realm.member.view': ALL_TYPES,
    'realm.member.remove': ALL_TYPES,
    'realm.member.role.update': ALL_TYPES,
    'realm.delete': ALL_TYPES,
    'realm.ownership.transfer': ALL_TYPES,
    'realm.roles.manage': ALL_TYPES,
    'realm.permissions.manage': ALL_TYPES,
}


def seed_realm_types(apps, schema_editor):
    catalog = apps.get_model('entity', 'PermissionCatalogEntry').objects.using(schema_editor.connection.alias)
    type_rows = apps.get_model('community', 'Type').objects.using(schema_editor.connection.alias)
    types = {code: type_rows.get_or_create(code=code)[0] for code in ALL_TYPES}
    for entry in catalog.order_by('pk'):
        kinds = REALM_TYPES_BY_CODE.get(entry.code, ALL_TYPES) if entry.scope == 'realm' else []
        entry.realm_types.set([types[kind] for kind in kinds])
    codes = list(catalog.filter(scope='realm').exclude(code__in=REALM_TYPES_BY_CODE).order_by('code').values_list('code', flat=True))
    if codes:
        print('Custom realm permissions backfilled to all kinds; review: ' + ', '.join(codes))



class Migration(migrations.Migration):
    dependencies = [('entity', '0028_permission_display_metadata'), ('community', '0015_realm_types')]
    operations = [
        migrations.AddField(model_name='permissioncatalogentry', name='realm_types',
                            field=models.ManyToManyField(blank=True, related_name='permissions', to='community.type')),
        migrations.RunPython(seed_realm_types, migrations.RunPython.noop),
    ]
