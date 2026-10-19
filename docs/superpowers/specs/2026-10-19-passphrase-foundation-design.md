# Passphrase foundation and privacy settings

Date: 2026-10-19
Status: Proposed for user review; implementation has not started.

## Intent and scope

Implement the reusable encryption-configuration foundation before connecting it
to Diary. Add Passphrase as the first top-level section inside Settings > Data &
Privacy in both the React webapp and Flutter app. Group named configuration rows
by purpose context, initially Diary. Users can add, name, rename, activate,
and deactivate configurations. Passphrase deletion is deferred. Each context can
have one active configuration or none. Switching from active A to inactive B requires successful
confirmation of both passphrases. Setup must work without any diary entries.

The backend stores public derivation metadata and an encrypted verification
payload. It never receives a passphrase or a usable encryption key. There is no
stored vault key, wrapped vault key, or passphrase hash.

The agreed future Diary behavior is recorded here for compatibility: add
Entry.is_encrypted with default False, allow the title field to hold an encrypted
package, and encrypt title and body separately. Those Diary changes, data
conversion, and diary unlock screens are outside this implementation stage.

## Existing code and boundaries

- Django entity/models.py owns reusable entity-related records. Account is
  user.Account, and its entity_id is the account's personal Entity.
- Authentication supplies both request.user and an active request.entity. These
  can differ when acting as a realm. Personal settings must not accidentally use
  a switched entity as their owner.
- Entity service can_act_as_entity supplies the existing authority check for
  future explicitly supported realm scopes.
- Web DataPrivacy.tsx and mobile data_privacy_view.dart currently start with
  Private profile, followed by export and deletion.
- There is no suitable existing per-account encryption-settings record.
- The existing bootstrap/request nonce cryptography uses server-known secrets
  and must not be reused for passphrase-derived encryption.

## Alternatives and selected design

Checking an encrypted diary entry couples setup to content and does not handle
an empty diary. Storing a passphrase or server-decryptable key conflicts with the
agreed privacy requirement. Select an independent entity-owned configuration
with encrypted verification material and an explicit lifecycle.

The model is reusable across entity/user/purpose scopes. The first settings
surface manages only the authenticated account's personal entity, initially with
purpose `diary` displayed as Diary. The purpose registry is extensible, but this
release must not advertise Notes or other unwired consumers. Future Diary
consumes this configuration explicitly. This does not
implement shared entity encryption or grant different users access to the same
encrypted content.

## Configuration model

Add EncryptionConfiguration to entity/models.py with a schema migration.

| Field | Meaning |
| --- | --- |
| id | UUID identifying this immutable configuration generation |
| entity | ForeignKey to entity.Entity; database column entity_id |
| user | ForeignKey to user.Account; database column user_id |
| purpose | Allowlisted context identifier; this settings release uses diary |
| name | User-chosen public label, 1-80 trimmed Unicode code points |
| is_active | Whether normal clients can select it for new encryption |
| revision | Monotonically increasing value on rename or lifecycle changes |
| format_version | Version of the verification/encryption contract |
| kdf_algorithm | Allowlisted Argon2id identifier |
| kdf_parameters | Bounded, validated derivation parameters |
| salt | Canonical base64 encoding of a random 16-byte salt |
| cipher_algorithm | Allowlisted AES-256-GCM identifier |
| verification_nonce | Base64 encoding of a fresh 12-byte nonce |
| verification_ciphertext | Base64 ciphertext including its 16-byte tag |
| verification_digest | SHA-256 digest of a random verification token |
| replaces | Optional protected lineage reference to a predecessor configuration |
| created_at | Creation timestamp |
| updated_at | Timestamp of the most recent label/lifecycle change |
| deactivated_at | Most recent deactivation timestamp; cleared on reactivation |

Permit multiple named rows, including never-activated and historical rows.
Enforce at most one active row per
(entity, user, purpose) with a conditional database uniqueness constraint.
Cryptographic parameters, identifiers, and verification material are immutable
after creation. Name and lifecycle fields may change through their controlled
services. A name is a server-readable reminder, never the passphrase itself;
the UI explicitly tells users not to put their secret in the name. Duplicate
labels are permitted, with creation dates distinguishing them.

Adding a passphrase creates a new inactive row and never silently changes the
active selection. Different rows have independent salts and verification tokens,
even if the user happens to choose the same passphrase text. Reactivation uses
the row's original salt and verification payload, not newly generated material.

Lifecycle services serialize changes by locking an existing owner row; locking
only a configuration is insufficient during two concurrent first setups.
GET returns a scope_version calculated from the complete ordered set of row IDs,
revisions, and active states for that entity/user/purpose. Mutations require the
expected_scope_version and verify it while holding the owner lock. This detects
concurrent additions, renames, and A-to-B-to-A activation changes that
an expected active ID alone would miss. A stale scope returns 409 with no writes.
Administrative inspection is read-only for cryptographic material; do not expose
an ordinary form or bulk action that overwrites active configurations.

## Client cryptography contract

Use matching web and Flutter modules, with shared test vectors checked by both.
Web uses an explicitly pinned browser-compatible Argon2id implementation plus
Web Crypto AES-GCM. Flutter uses its existing cryptography package, with costly
derivation kept off the UI thread. Do not fall back to a weaker derivation
algorithm if a platform fails.

Version 1 profile: Argon2id version 19, 65,536 KiB memory, three iterations,
parallelism one, output length 32 bytes. Verify this profile on supported mobile
devices before release; a profile change requires an explicit format/profile
version and corresponding test vectors. Do not accept arbitrary large costs
from server or database values.

Passphrases are exact UTF-8 text, without trimming, case conversion, or silent
Unicode normalization. Setup/replacement requires matching confirmation, at
least 15 Unicode code points, and no more than 1,024 UTF-8 bytes. Permit spaces,
pasting, and password managers. Do not impose arbitrary character-class rules.

Generate a random 32-byte verification token locally. Encrypt a small payload
containing that token and a fixed format marker. Authenticate the configuration
ID, personal entity ID, account ID, purpose, and format version using a specified
canonical byte representation. Save only the encrypted payload and the token's
SHA-256 digest. The digest is not a passphrase hash or an encryption key.

The token makes row-specific passphrase confirmation enforceable by the backend. A
constant publicly known check string alone verifies unlocking on the client but
does not give the server proof that the old configuration was unlocked.

To confirm locally, derive the key, authenticate/decrypt the verification
payload, and validate its marker and identity. To authorize a lifecycle mutation,
send recovered verification tokens in explicit row-bound proof fields:
`active_verification_proof` for the active row and
`target_verification_proof` for the selected row. The backend hashes each token
and compares it in constant time against the corresponding row's digest. It
does not run passphrase derivation or receive a passphrase/key. Proof for one
row cannot authorize another row. Do not retain a proof between dialogs or reuse
it as a remembered-unlock feature. Proofs are operation credentials and must be
protected and redacted accordingly.

The verification associated data excludes mutable names, revisions, and active
states, so changing a label or reactivating a row does not break decryption. Its
immutable configuration/owner/purpose/version binding must still be checked.

Verification failure can mean an incorrect passphrase or damaged configuration;
do not claim to distinguish these cryptographically. Show a generic unlock
failure. Transport errors, unsupported formats, and unavailable crypto runtime
have separate messages. A failure never starts new setup or overwrites data.

## API contract

Use authenticated routes under /api/entity/encryption-configurations/. Personal
settings resolve user and entity from the authenticated account on the server;
clients cannot supply another user_id or claim authority by posting entity_id.
The model remains ready for future explicitly authorized entity-scope routes.

- GET personal/: return authenticated owner context, supported crypto profile,
  and the purpose groups. Each group includes its label, named configuration
  rows, active_configuration_id or null, and scope_version. Distinguish no rows
  from existing rows with all disabled. Fetch failure is never setup absence.
  Include the encrypted verification material required for local unlock, but
  do not return the server-only verification digest or operation proofs.
- POST personal/: register a named, client-generated configuration UUID, purpose,
  public derivation metadata, encrypted verification material, and token digest.
  Create it inactive, even if another row is active. A retry with the same UUID
  and identical material may return the original successful result without
  creating a duplicate. Recheck identity/scope, not only UUID, for idempotency.
- PATCH personal/<uuid>/: rename only. Require expected_scope_version. Reject
  attempts to edit cryptographic material or is_active through this endpoint.
- POST personal/<uuid>/activate/: activate the selected inactive row. With no
  active row, require its target_verification_proof. If another row is active,
  require both that row's active_verification_proof and the selected row's
  target_verification_proof. Validate both before any state changes. Deactivate
  A and activate B atomically. No intermediate committed two-active state.
- POST personal/<uuid>/deactivate/: the selected row must be the active row;
  require its active_verification_proof. Leave the context with no active row.
  Do not promote another row automatically.
- All mutations require expected_scope_version. Wrong/missing proofs, invalid
  scope, and stale state leave all rows unchanged.
- Do not expose unauthenticated reads, generic writable model endpoints, direct
  flag updates, deletion, or endpoints accepting plaintext passphrases/encryption
  keys. Configuration deletion is not implemented in this release, even for
  unused rows. Admin inspection must not allow deleting configurations either.

Validate strict payload schemas, canonical base64, exact nonce/salt/digest
lengths, bounded ciphertext, allowlisted formats/algorithms, and bounded cost
parameters. Reject raw passphrase/key/password fields instead of accepting and
ignoring them. Apply account/IP mutation throttles using a production-appropriate
shared cache. Responses use Cache-Control: no-store.

The backend cannot prove that an arbitrary client encrypted its verification
payload correctly. Supported clients perform a successful local round trip
before registration. Do not describe schema validation as cryptographic proof.

## Lifecycle rules and future encrypted content

This preparation stage has no encrypted-content consumers. Configuration actions
change no diary or note records. At most one row is active per context, but zero
is a valid persistent state. Presence of rows and presence of an active row are
different facts and must remain distinct in the API and UI.

An active row selects the default for future encryption; it is not the only row
that can decrypt older data. Activating B does not re-encrypt A's content, and
disabling every row does not decrypt or erase existing content. Future encrypted
records must retain their exact configuration ID and require that configuration's
passphrase when opened, even if the row is now inactive. When all rows are
disabled, future encrypted writes must require activation; never silently fall
back to plaintext. Current unmodified legacy Diary behavior is not changed in
this preparation stage.

Retain inactive configurations and their verification metadata so referenced
entries can be unlocked later. No row deletion API, action, or consumer-usage
query is needed in this release. Any future deletion feature must introduce an
authoritative reference check and prohibit deleting configurations still needed
by encrypted content. Disabling is allowed for referenced rows because their
material remains available for reading existing records.

Replacing an active passphrase means adding a new named inactive row, then
activating it after confirming both old and new passphrases. This changes only
the default for new encryption. It must not be presented as changing the secret
of existing encrypted entries. A future distinct Change passphrase for existing
content operation requires explicit migration coordination.

Future re-encryption must pause conflicting writes, retain old and pending
configuration references, resume interrupted batches safely, and activate the
new default only when migration completes. Deactivation does not erase copied
keys or revoke offline decryption. These properties cannot be provided by the
is_active flag alone.

## Future Diary unlock experience (not implemented in this stage)

Preserve the user's original requirement to prompt when opening the Diary base
page. If a configuration is active, ask for that configuration's passphrase and
verify it locally. Use its temporary key to reveal titles and body previews of
entries referencing that configuration. If none is active, offer the available
configurations to select and unlock, without silently activating any of them.

Entries referencing another configuration remain locked. Show blurred placeholder
lines for their titles/previews, a lock indicator, and the message: "This entry
uses a different passphrase. Unlock to view." Do not decrypt hidden content for
this display or insert real titles/previews into the DOM, accessibility tree,
tooltips, navigation state, or persistent caches underneath a CSS blur.

Selecting a locked entry opens an entry-triggered unlock dialog showing its
configuration's public name. Retrieve that exact configuration's original salt
and derivation settings using the ID stored in the encrypted packages. Ask for
its passphrase, verify the configuration payload, and decrypt/authenticate the
entry's title and content before displaying them. Keep a separate temporary key
per unlocked configuration for this Diary visit, so opening another entry using
the same configuration does not prompt again. Never try the active key against
an unrelated configuration and present the resulting failure as damaged data.

Locked means the correct configuration key is not available in the current
session. Inactive does not mean the entry must remain blurred after that key is
successfully unlocked. Unlocking an inactive configuration does not activate it
or change the default for new entries. Legacy is_encrypted=False entries keep
their existing readable format. Leaving Diary, identity change, logout, and
mobile backgrounding clear accessible keys and decrypted view state.

The chosen behavior preserves historical configurations and asks for additional
passphrases when needed; changing the active default does not automatically
migrate old entries. A future explicit migration can consolidate entries if the
user supplies all required old and destination passphrases. Missing historical
configurations, unsupported formats, and damaged encrypted entries have specific
unavailable/error states, never plaintext fallback or destructive reset.

## Settings experience

Add a Passphrase section before Private profile on both clients, using the
existing settings styles rather than introducing a separate settings category.

Group rows under purpose headings, initially Diary. Each group has Add passphrase,
an explicit None active state when appropriate, and a list showing name, creation
date, an Active or Inactive badge, and row actions. Inactive rows have Activate;
the active row has Disable. Rows also have Rename; no Delete action is shown.
Use action buttons and status badges instead of independent checkboxes, which would imply
multiple selections and obscure the confirmation required for a state change.

An empty group explains that a passphrase is used to unlock encrypted features
and that existing content is not changed. The add dialog asks for Name,
Passphrase, and Confirm passphrase, with visibility controls, matching validation,
and an acknowledgement that there is no recovery in this design. Save creates
an inactive row. Do not automatically switch the active configuration on add.

Activation dialogs name the current and selected rows. With active A and target
B, first confirm A's passphrase, then B's passphrase; submit both proofs with the
observed scope_version. With no active row, ask only for B's passphrase. Disabling
asks for the active row's passphrase. Clear
successful proof/key state when the dialog closes; a later action prompts again.
Rename changes only a public label and does not require a passphrase.

Show distinct statuses for No passphrases, All disabled, and a named Active row.
Explain that disabling does not change existing encrypted data. Do not expose a
Change passphrase label that falsely implies existing content is re-encrypted.
Do not claim that Diary or any existing content is encrypted merely because a
configuration exists or is active. A stale scope asks the user to reload and
repeat proof entry; never silently apply an action to refreshed rows.

During derivation or submission, prevent duplicate actions and announce progress.
Preserve the ability to cancel before submission. Never retain passphrases,
decrypted verification tokens, or keys in Redux, browser storage, preferences,
secure-storage backups, analytics, URLs, or debug/error messages. Clear accessible
secret state on close, unmount, logout, identity change, and mobile backgrounding.
Managed runtimes cannot guarantee physical memory zeroization.

## Logging, export, and deletion

Redact verification_proof, active_verification_proof,
target_verification_proof, and any accidental secret fields recursively in API
failure logging. Client errors must not dump request objects containing proof
material. Avoid reflecting sensitive values in serializer errors.

Account deletion retires the account's active configurations alongside its
existing deletion flow. Export may include owned public configuration metadata
and encrypted verification payloads for portability, but not operation proofs
or transient keys. Do not modify Diary export semantics in this phase.

## Validation and acceptance

- Fresh migration creates the model and active-scope constraint; migration-state
  checks report no missing model changes.
- Backend tests cover authentication, account/entity isolation including switched
  contexts, purpose separation, invalid payloads/costs/lengths/names, multiple
  inactive rows, zero/one active states, concurrent creation/activation,
  idempotent registration, stale scope versions including A-to-B-to-A changes,
  missing/wrong/swapped row proofs, rename restrictions, absent deletion routes,
  inactive-history retention, no-store, and log redaction.
- Proof tests demonstrate that switching A to B requires both secrets, reactivating
  B with none active requires B's secret, and disabling A requires A's secret.
  Direct flag changes cannot bypass lifecycle services. Correctly proving
  one row never authorizes another row's action.
- Cross-platform fixtures cover exact passphrase encoding including Unicode and
  spaces, Argon2id output, AES-GCM verification, identity binding, wrong secret,
  and modified nonce/tag/ciphertext. New setup passes a local encryption/decryption
  round trip before any registration request.
- Web tests/build cover grouped named rows, empty/all-disabled/active states,
  add/rename/activate/disable dialogs, no Delete action, both-passphrase switching,
  reactivation with none active, failure without partial mutation, stale scopes, cancellation,
  inactive-row retention, and no secret persistence. Run the existing web build and
  applicable test harness.
- Flutter tests/analyzer cover the same state transitions and identity/background
  cleanup. Verify derivation does not freeze the settings screen.
- Check relevant pre-existing settings behavior still works. Inspect the settings
  layout on web and mobile using available runtime/browser surfaces.
- Verify no Diary/Notes model, serializer, composer, encryption flag, title type,
  or existing content has been changed by this preparation stage.

## Security limits and references

Strong passphrases are required because downloaded verification material permits
offline guessing. Server/UI rate limits do not prevent that. Malicious web code
or a compromised unlocked device can read entered secrets and plaintext; storing
keys only in memory does not remove this trust boundary. No shared-user access,
automatic recovery, forward secrecy, or encrypted-data migration is claimed.

- Argon2: https://www.rfc-editor.org/rfc/rfc9106.html
- AES-GCM: https://www.w3.org/TR/2017/REC-WebCryptoAPI-20170126/
- Flutter Argon2id: https://pub.dev/documentation/cryptography/latest/cryptography/Argon2id-class.html
- Existing source anchors: entity/models.py, entity/services/acting.py,
  user_service/logging_middleware.py, user/utils/account_deletion.py,
  webapp/src/app/tabs/settings/section/DataPrivacy.tsx, and
  chatterloop_app/lib/views/settings/data_privacy_view.dart.
