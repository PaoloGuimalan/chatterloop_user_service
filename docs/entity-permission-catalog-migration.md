# Entity permission catalog migration

Migrations: `entity.0022_entitypermission_catalog`, `entity.0023_role_permissions`,
`entity.0024_catalog_entity_types`, and `community.0011_role_references`.

The catalog table becomes `entity_permission_catalog`. The override table becomes
`entity_permission`; its string `permission` column becomes numeric
`permission_id`, a required foreign key to the catalog primary key. Catalog
deletion is protected while overrides reference it. Existing catalog IDs are
preserved.

Role definitions live in `entity_role`, with built-in name choices owner/admin/
moderator/member. Names are not unique. Each record has its own `permissions`
many-to-many relation through `entity_role_permissions`. Grants are evaluated by
role ID, never combined by name. Original grant IDs and timestamps are retained.

`community_member.role_id` and `community_invite.role_id` reference exact role
definitions. These were the only other persisted entity-role references found;
conversation/model author-role fields in other services are unrelated.

The catalog owns `entity_types`, a many-to-many field backed by
`entity_permission_catalog_entity_types` and the `entity_type` lookup table.
The old `entity_entitytypedefaultpermission` table/model is replaced. Existing
type-assignment IDs, timestamps and permission links are preserved. The scalar
`scope` field still chooses global, realm or entity-type resolution; assigning
types does not change a permission's scope or activation status. Both catalog
and role admin forms expose their many-to-many selections directly.

## Existing data

- Existing catalog IDs, codenames, scopes and activation status are unchanged.
- Codenames found only in overrides are inserted into the catalog as active
  entries. Scope is inferred from the existing overrides: `global` for NULL
  realms, `realm` otherwise. No role or entity-type defaults are added.
- A missing codename used both globally and within realms cannot have one scope
  inferred. Migration stops before changes and lists the conflicting codes.
  Define their intended scope and reconcile those legacy rows before retrying.
- Every override retains its ID, entity, realm, grant/deny effect, creator,
  reason, creation time and expiry. Only the permission reference changes.
- Reverse migration restores codename strings. Recovered catalog entries remain
  because other records may have started referencing them.
- Legacy per-role grants are grouped into one original definition per name.
  All four built-ins are explicitly marked `is_default`; a missing built-in gets
  an empty permission set, not new grants. New same-named role definitions are
  not defaults unless explicitly configured. Only the default marker is unique
  per name; ordinary role names may repeat.
- Automatic joins/creation use the marked member/owner defaults. Explicit role
  assignments and manage invites use exact IDs. Existing memberships and invites
  keep their IDs and metadata and are linked to the migrated definitions.
  Legacy custom role names with no grants get zero-grant definitions.
- Rolling back role normalization stops if duplicate definitions share the same
  name and permission, which the old schema cannot represent without losing
  grant records. Reconcile those new definitions before attempting downgrade.

## API compatibility

Role/member/invite responses retain `role` / `my_role` names for display and tier
checks, and add `role_id` / `my_role_id`. New role writes use `new_role_id` for
member updates and `role_id` for invites. Legacy name-only writes resolve only
an explicitly marked default, never an arbitrary duplicate.

`GET /api/entity/roles` returns authenticated clients a list of exact role IDs,
names, default markers and active permission codenames. Web/mobile selectors
retain same-named rows and send their IDs. Owner/admin structural protections
remain separate from permission grants.

## Coordinated deployment

This changes the shared SQL schema. Old service versions cannot run against the
new schema, and new versions cannot run against the old schema.

1. Take the normal database backup and drain/stop the Node server, Django user
   service and Go developer API processes that access permissions.
2. With the updated user service, run `python manage.py migrate` using the normal
   deployment settings. The command has not been run against production here.
3. Deploy/restart the updated versions of all three backends before restoring
   traffic. Catalog/entity-type caches retain their format. The role matrix uses
   `permcache:role_matrix:v2`, keyed by exact role IDs; writes invalidate the old
   v1 key too.
4. Verify a grant, a deny, an expired override and a realm-specific override.

Rollback also requires stopped traffic: migrate community back to
`0010_community_membership_capability`, then entity back to
`0021_messages_encryption_purpose`, and restore all three previous service
versions before reopening traffic.

## Backend audit

- Django: FK models, table metadata, exact role-ID resolver/cache, role
  management admin, member/invite serializers, acting-as authorization and
  membership/invitation paths updated.
- Node: catalog queries, role/entity-type joins, override lookup, member role
  changes, ownership transfers, membership creation and notification recipients
  updated.
  Its platform defaults also match Django: recovered/custom global permissions
  require explicit grants rather than automatically going to every entity.
- Go developer API: explicit-grant lookup joins the new catalog table, checks
  active global entries, and retains token scope, deny and expiry checks.
  Provisioning and diagnostic SQL in its README/API documentation are updated.
- Web/mobile role-change and invitation callers send IDs and preserve display
  names. Other services were searched for old role columns and table names.
  Historical migrations retain their historical names.
- Three historical Mongo-to-Postgres import scripts under `migration_service`
  still target the obsolete `community_member.account_id` schema and are not
  runtime migration commands. They must be modernized before any future reuse;
  do not use them to perform this Django schema migration.

## Verification

From `user_service`:

```powershell
.venv/Scripts/python.exe manage.py test entity.tests.test_entity_permission_catalog entity.tests.test_permission_resolver entity.tests.test_permission_catalog_sync entity.tests.test_permission_sql_contract entity.tests.test_roles entity.tests.test_role_references entity.tests.test_catalog_entity_types --settings=entity.test_settings --noinput
.venv/Scripts/python.exe manage.py makemigrations entity community --check --dry-run --settings=entity.test_settings
```

These tests use an isolated SQLite database, including actual forward/backward
migrations and execution of the Node and Go override SQL (with only driver
placeholders and clock spelling adapted for SQLite). PostgreSQL-specific DDL
has not been executed locally; the Docker daemon is unavailable.

Latest verification results:

- Core schema/resolver/forms/migration/SQL contracts: 59 tests passed.
- Isolated community/invitation/profile runtime coverage: 88 tests passed.
- Web role coverage: 4 tests passed; TypeScript passed. Full web suite: 229/230
  passed; existing failure: `a direct guest profile visit still opens the public profile`.
- Node full suite: 274 passed, 1 skipped, 1 existing failure:
  `personal lists exclude management inbox rows, legacy summaries and read previews`.
- Mobile focused role/invite/roster coverage: 61 tests passed; targeted Flutter
  analysis reported no issues.
- Go authorization tests passed. Full Go suite retains three unrelated presence
  failures caused by expired fixed token timestamps:
  `TestSignFrameCarriesTheFieldNamesBothClientsRead`,
  `TestSignFrameUsesTheWireNameNotTheDocumentName`, and
  `TestSignFrameCarriesOfflineToo`.
- Migration state matches models (`makemigrations --check --dry-run`), syntax
  checks and whitespace checks passed. No production migration, commit or push
  was performed.
