"""Coordinated Django/Node/Go cutover; permission IDs and grant rows stay intact."""
from django.db import migrations, models


# Frozen metadata only. Never import live permission definitions into migrations.
METADATA = {
    'messages.send': ('Send messages', 'Send messages in conversations you can access.'),
    'messages.react': ('React to messages', 'Add reactions to messages in conversations you can access.'),
    'conversations.create': ('Create conversations', 'Start new conversations with eligible participants.'),
    'posts.create': ('Create posts', 'Create posts, subject to destination access and community posting permissions.'),
    'comments.create': ('Create comments', 'Comment on accessible posts where commenting is allowed.'),
    'contacts.request.create': ('Send contact requests', 'Send requests to connect with other users.'),
    'poke.create': ('Send pokes', 'Send pokes to eligible users.'),
    'ai.prompt.assist': ('Use AI assistance', 'Request AI assistance for supported features.'),
    'webrtc.call.join': ('Join calls', 'Join calls in conversations you can access.'),
    'realm.server.create': ('Create servers', 'Create new server realms.'),
    'messages.read': ('Read messages through the API', 'Allow an API token to read accessible conversation messages; conversation membership is still required.'),
    'notifications.read': ('Read notifications through the API', 'Allow an API token to read notifications belonging to the acting entity.'),
    'events.subscribe': ('Subscribe to API events', 'Allow an API token to subscribe to events the acting entity is authorized to receive.'),
    'realm.identity.act': ('Act as the realm', 'Switch to the realm identity and continue acting on its behalf while this permission remains granted.'),
    'realm.update': ('Edit realm details', 'Update the realm details you are authorized to manage.'),
    'realm.invite.create': ('Create invitations', 'Create invitations; publishing without review also requires Publish invitations without approval.'),
    'realm.invite.revoke': ('Revoke invitations', 'Revoke outstanding invitations in this realm.'),
    'realm.request.approve': ('Approve join requests', 'Approve requests to join this realm.'),
    'realm.request.decline': ('Decline join requests', 'Decline requests to join this realm.'),
    'realm.member.view': ('View members', 'View the member list in realm management.'),
    'realm.member.add': ('Add members', 'Authorize member addition in features that check this permission; existing group and server invitation rules still apply.'),
    'realm.member.remove': ('Remove members', 'Remove eligible members, subject to owner and protected-role restrictions.'),
    'realm.member.role.update': ('Assign member roles', 'Assign eligible realm roles without exceeding your own delegation authority.'),
    'realm.follower.remove': ('Remove followers', 'Remove followers from this realm.'),
    'realm.channel.create': ('Create channels', 'Create channels in this realm.'),
    'realm.media.update': ('Update realm photos', 'Change the realm profile picture or cover photo.'),
    'realm.delete': ('Delete the realm', 'Delete this realm. This is an intrinsic owner-only permission.'),
    'realm.ownership.transfer': ('Transfer ownership', 'Transfer realm ownership. This is an intrinsic owner-only permission.'),
    'realm.post.submit': ('Submit community posts', 'Submit posts to the community; posts require review unless Publish posts without approval is also granted.'),
    'realm.post.publish': ('Publish posts without approval', 'Publish community posts without review; Submit community posts must also be granted.'),
    'realm.post.review': ('Review community posts', 'Approve or reject community post submissions, subject to self-review restrictions.'),
    'realm.post.takedown': ('Take down community posts', 'Hide a published community post and provide a reason to its author.'),
    'realm.post.archive': ('Archive community-authored posts', 'Archive or restore posts authored by the community itself; this does not grant access to archive member-authored posts.'),
    'realm.invite.publish': ('Publish invitations without approval', 'Send invitations without review; Create invitations must also be granted.'),
    'realm.invite.review': ('Review invitations', 'Approve or reject invitations awaiting community review.'),
    'realm.roles.manage': ('Manage roles', 'Create, rename or delete eligible realm roles within your delegation authority; the Owner role remains protected.'),
    'realm.permissions.manage': ('Manage role permissions', 'Configure eligible role permissions without granting permissions beyond your own authority.'),
    'realm.notifications.view': ('View community notifications', 'View the community management notification inbox and its activities.'),
    'module.diary.access': ('Use the diary', 'Access the diary module for this entity type.'),
    'module.notes.access': ('Use notes', 'Access the notes module for this entity type.'),
    'module.poke.access': ('Use pokes', 'Access the poke module for this entity type.'),
    'module.contacts.access': ('Use contacts', 'Access the contacts module for this entity type.'),
    'module.page_dashboard.access': ('Use the page dashboard', 'Access the dashboard when acting as a page.'),
    'module.page_members.access': ('Manage page members', 'Access page member management, subject to realm permissions.'),
    'module.page_invites.access': ('Manage page invitations', 'Access page invitation management, subject to realm permissions.'),
}


def seed_metadata(apps, schema_editor):
    alias = schema_editor.connection.alias
    catalog = apps.get_model('entity', 'PermissionCatalogEntry').objects.using(alias)
    for code, (name, description) in METADATA.items():
        # Update in place, including inactive records. Never create or enable a grant.
        catalog.filter(code=code).update(name=name, description=description)
    custom = list(catalog.exclude(code__in=METADATA).order_by('code').values_list('code', flat=True))
    if custom:
        print('Custom permission metadata preserved (review names manually): ' + ', '.join(custom))


def format_default_role_names(apps, schema_editor):
    roles = apps.get_model('entity', 'Role').objects.using(schema_editor.connection.alias)
    for code in ('owner', 'admin', 'moderator', 'member'):
        # Preserve explicitly renamed defaults and every custom role.
        roles.filter(is_default=True, code=code, name=code).update(name=code.capitalize())


class Migration(migrations.Migration):
    dependencies = [('entity', '0027_community_permission_catalog')]
    operations = [
        migrations.RenameField(model_name='permissioncatalogentry', old_name='codename', new_name='code'),
        migrations.AddField(model_name='permissioncatalogentry', name='name',
                            field=models.CharField(blank=True, default='', max_length=150)),
        migrations.RunPython(seed_metadata, migrations.RunPython.noop),
        migrations.RunPython(format_default_role_names, migrations.RunPython.noop),
    ]
