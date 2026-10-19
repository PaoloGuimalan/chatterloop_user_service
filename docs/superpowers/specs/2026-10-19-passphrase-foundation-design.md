# Passphrase foundation and privacy settings

Date: 2026-10-19
Status: Proposed for user review; implementation has not started.

## Intent and scope

Implement the reusable encryption-configuration foundation before connecting it
to Diary. Add Passphrase as the first top-level section inside Settings > Data &
Privacy in both the React webapp and Flutter app. A user can set up a passphrase,
confirm the current passphrase, and replace it after successfully confirming the
old one. Setup must work without any diary entries.

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
surface manages only the authenticated account's personal entity and purpose
`personal`. Future Diary consumes this configuration explicitly. This does not
implement shared entity encryption or grant different users access to the same
encrypted content.

## Configuration model

Add EncryptionConfiguration to entity/models.py with a schema migration.

| Field | Meaning |
| --- | --- |
| id | UUID identifying this immutable configuration generation |
| entity | ForeignKey to entity.Entity; database column entity_id |
| user | ForeignKey to user.Account; database column user_id |
| purpose | Bounded context string; this settings release uses personal |
| is_active | Whether normal clients can select it for new encryption |
| format_version | Version of the verification/encryption contract |
| kdf_algorithm | Allowlisted Argon2id identifier |
| kdf_parameters | Bounded, validated derivation parameters |
| salt | Canonical base64 encoding of a random 16-byte salt |
| cipher_algorithm | Allowlisted AES-256-GCM identifier |
| verification_nonce | Base64 encoding of a fresh 12-byte nonce |
| verification_ciphertext | Base64 ciphertext including its 16-byte tag |
| verification_digest | SHA-256 digest of a random verification token |
| replaces | Nullable reference to the previous configuration generation |
| created_at | Creation timestamp |
| deactivated_at | Timestamp when the active generation was retired |

Permit multiple historical rows. Enforce at most one active row per
(entity, user, purpose) with a conditional database uniqueness constraint.
Cryptographic parameters, identifiers, and verification material are immutable
after creation. Replacement creates a new generation rather than updating old
salt/settings in place.

Lifecycle services serialize changes by locking an existing owner row; locking
only a configuration is insufficient during two concurrent first setups.
Stale expected configuration IDs and concurrent setup/replacement return 409.
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

The token makes old-passphrase confirmation enforceable by the backend. A
constant publicly known check string alone verifies unlocking on the client but
does not give the server proof that the old configuration was unlocked.

To confirm locally, derive the key, authenticate/decrypt the verification
payload, and validate its marker and identity. To replace, send the recovered
verification token as `verification_proof`, bound to the expected current
configuration ID. The backend hashes it and compares in constant time. It does
not run passphrase derivation or receive a passphrase/key. A retired token
cannot authorize a later generation. The proof is an operation credential and
must be protected and redacted accordingly.

Verification failure can mean an incorrect passphrase or damaged configuration;
do not claim to distinguish these cryptographically. Show a generic unlock
failure. Transport errors, unsupported formats, and unavailable crypto runtime
have separate messages. A failure never starts new setup or overwrites data.

## API contract

Use authenticated routes under /api/entity/encryption-configurations/. Personal
settings resolve user and entity from the authenticated account on the server;
clients cannot supply another user_id or claim authority by posting entity_id.
The model remains ready for future explicitly authorized entity-scope routes.

- GET personal/: return owner context, the supported profile, and active
  configuration or null. Configuration absence is the source of setup status.
  Fetch failure must never be interpreted as absence.
- POST personal/: register a client-generated configuration UUID, public
  derivation metadata, encrypted verification material, and token digest. Reject
  setup with an existing active generation. A retry with the same UUID and
  identical material may return the original successful result.
- POST personal/<uuid>/replace/: require expected current configuration UUID,
  verification_proof, and the complete new generation. Verify scope and proof
  before changing any state. Create the replacement, retire the previous record,
  and activate the new record in one transaction. Preserve previous records.
- Do not expose unauthenticated reads, generic writable model endpoints, deletion,
  or deactivation without old-passphrase proof.

Validate strict payload schemas, canonical base64, exact nonce/salt/digest
lengths, bounded ciphertext, allowlisted formats/algorithms, and bounded cost
parameters. Reject raw passphrase/key/password fields instead of accepting and
ignoring them. Apply account/IP mutation throttles using a production-appropriate
shared cache. Responses use Cache-Control: no-store.

The backend cannot prove that an arbitrary client encrypted its verification
payload correctly. Supported clients perform a successful local round trip
before registration. Do not describe schema validation as cryptographic proof.

## Replacement and future encrypted content

This preparation stage has no encrypted-content consumers. Replacement therefore
only rotates the configuration after old-passphrase confirmation; it changes no
diary or note records.

Add an explicit replacement-readiness service boundary. A future content
consumer must register usage detection and migration coordination before it
begins encrypting records. Once referenced encrypted content exists, simple
replacement must return a migration-required conflict rather than retiring a
configuration and stranding content. Existing plaintext Diary entries do not
block this preparation-stage replacement.

Future re-encryption must pause conflicting writes, retain old and pending
configuration references, resume interrupted batches safely, and activate the
new default only when migration completes. Deactivation does not erase copied
keys or revoke offline decryption. These properties cannot be provided by the
is_active flag alone.

## Settings experience

Add a Passphrase section before Private profile on both clients, using the
existing settings styles rather than introducing a separate settings category.

Not configured: explain that a passphrase is used to unlock encrypted features,
offer Set up passphrase, and state that existing content is not changed.
Setup form has new/confirmation fields, visibility controls, matching validation,
and a clear acknowledgement that there is no passphrase recovery in this design.

Configured: show Configured, with Confirm passphrase and Change passphrase.
Confirmation happens locally and stores no unlocked key persistently. Changing
requires successful current-passphrase confirmation before enabling the new and
confirmation fields. Reconfirm against the current server generation immediately
before mutation; a stale configuration asks the user to reload and retry.

Do not expose a Disable button in this first release; the lifecycle model exists
for future controlled deactivation. Do not claim that Diary or any existing
content is encrypted merely because configuration setup is complete.

During derivation or submission, prevent duplicate actions and announce progress.
Preserve the ability to cancel before submission. Never retain passphrases,
decrypted verification tokens, or keys in Redux, browser storage, preferences,
secure-storage backups, analytics, URLs, or debug/error messages. Clear accessible
secret state on close, unmount, logout, identity change, and mobile backgrounding.
Managed runtimes cannot guarantee physical memory zeroization.

## Logging, export, and deletion

Redact verification_proof and any accidental secret fields recursively in API
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
  contexts, invalid payloads/costs/lengths, concurrent setup, idempotent setup,
  stale replacement, missing/wrong/replayed old proof, correct replacement,
  historical retention, no-store responses, and log redaction.
- Server-verified proof tests demonstrate that a logged-in client cannot replace
  an active configuration merely by bypassing the old-passphrase UI.
- Cross-platform fixtures cover exact passphrase encoding including Unicode and
  spaces, Argon2id output, AES-GCM verification, identity binding, wrong secret,
  and modified nonce/tag/ciphertext. New setup passes a local encryption/decryption
  round trip before any registration request.
- Web tests/build cover absent/configured/error/loading states, old-passphrase
  gating, successful replacement, stale generations, cancellation, and no secret
  persistence. Run the existing web build and applicable test harness.
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
