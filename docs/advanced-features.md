# Archive, undo and native platform increment

## Goal and boundaries

Implement ZIP creation/extraction, safe session undo, editable permissions, and
the real Windows Shell context menu. Keep file-manager policy and operations
in NeLisp; a small C ABI adapter may host Windows COM/Win32 interfaces which
cannot be expressed as ordinary imported C functions. No Rust or application
logic in Python. Windows-only UI execution and third-party handlers must not
be claimed from Linux tests or object emission.

## Required behavior

Status: the native engine, Emacs command interfaces/strict readers and GUI
actions are implemented, with source-bound Linux regression evidence. The
requirements below remain the acceptance contract, not an assertion that every
platform or GUI action has passed.

- ZIP creation accepts frozen selected local paths and an exact unused archive
  path. Extraction accepts one ZIP and an exact unused destination directory.
  Use libarchive directly, not command strings or Python subprocesses. Preflight
  the entire manifest before creating output. Reject absolute/drive/UNC paths,
  traversal, ambiguous separators, duplicate/conflicting names, links, special
  files, encryption and unsupported data before mutation. Bound entry count,
  nesting, total expanded bytes, per-entry bytes and encoded names; enforce
  streamed byte limits as well as declared sizes. No overwrite or merge.
  Preflight consumes all entry data, using an immutable private input snapshot
  for both passes. Compressed input is capped at 4 GiB + 16 MiB, expanded total
  at 4 GiB, each entry at 1 GiB, entry count at 65535, depth at 64 and encoded
  manifest names at 1 MiB. Reject NFC/casefold collisions, Windows device names,
  alternate streams and trailing-dot/space components on every platform.
  Only single-disk classic ZIP store/deflate is supported; reject ZIP64 and
  ambiguous raw/local/central metadata rather than accepting another variant.
  Creation requests use the exact indexed batch schema with frozen paths and
  an exact archive destination; extraction uses the exact single-operation
  schema and rejects indexed fields. Empty path never means mutable selection.
  Report partial new output on cancellation/write failure. Keep the existing
  serial busy/cancellable/durable-response ownership contract.
  Windows ZIP input uses a separate native handle provider so path preflight
  and open-stream identities share the same metadata domain. Synchronous reads
  advance one owned handle; source handles deny ordinary write/delete sharing.
  Snapshot files require protected current-user-only ACLs and are deleted by
  closing their owned handles. A renamed snapshot cannot cause cleanup to delete
  a foreign replacement at its old name. Full native identities are required;
  a legacy identity fallback is limited to explicit FAT-family capability
  failures. Unsupported providers fail before archive output. Injected fallback
  tests do not certify actual FAT/exFAT, network, or remote filesystem support.
- Undo is session-scoped and bounded to 32 groups. Record only successful app-managed
  outcomes, including completed items of a partially failed batch. Validate
  current identities and file/tree fingerprints before undo. Never overwrite,
  delete a modified copy, or move a changed/replaced object merely to undo an
  older operation. Preflight the whole group, reverse actual execution order,
  and consume each item only after its actual reversal. Partial failure retains
  only unresolved items. Removal inverses use a validated same-filesystem
  no-overwrite detach into a private quarantine before cleanup, so cancellation
  cannot leave a half-deleted tree represented as an untouched item. Report any
  retained cleanup path. Stable positive group IDs in bounded `undo.ini` are
  required by `undo`; stale IDs fail rather than undoing another operation. An
  unsupported or externally performed operation must never imply recoverability.
  Rebase older history only for validated exact-root transitions and bounded
  parent-witness relationships. Other ancestor relationships and shared-batch
  dependencies are conservatively refused; successful inverse execution does
  not establish that every older group remains usable. If revalidation fails,
  preserve the actual inverse outcome and report older Undo unavailable.
  On Windows the native identity/metadata stamp supports local NTFS only.
  Unavailable stamp/provider capability must preserve a real successful mutation
  and report Undo unavailable, never fabricate a recoverable entry or turn the
  completed mutation into a false failure.
  Include copy, native move, rename, new directory, app-managed POSIX chmod and
  ZIP outputs. Ordinary GIO Trash retains its existing behavior. Record Trash
  Undo only when the backend exposes a unique verified handle to the actual
  trashed object, matched to our operation, original identity and original path.
  A pathname/content copy is not proof of preserved hidden metadata. Do not
  enable copied recovery from an incomplete xattr/capability probe. Restore the
  actual object with no copy/delete fallback, to an absent original path with
  matching parent identity and unchanged object/tree fingerprint. Unsupported
  backends or post-Trash handle lookup failures preserve the real successful
  Trash outcome and explicitly report Undo unavailable. Never reinterpret a
  failed/ambiguous lookup as a different successful mutation or use an older
  unrelated Trash item. GIO backend availability is platform-dependent.
  Shell/native ACL
  changes are external and must not be misrepresented as recorded operations.
- Permission editing uses a real Windows Security property page on Windows.
  The acknowledgement means the native editor was invoked, not that the user
  applied an ACL change. Other platforms provide a portable single-target mode
  editor using no-follow validation and explicit permission errors; never chmod
  a symlink target silently. Cancellation changes nothing. Use the same frozen
  target rules for GUI and Emacs commands. Revalidate the target at Apply, not
  only when opening the prompt. Native invocation does not prove that the named
  Security page appeared, closed or applied a change.
- Windows Shell menu uses filesystem Shell objects and IContextMenu, including
  IContextMenu2/3 message forwarding for dynamic/owner-drawn extensions, proper
  command offsets, Unicode invocation and COM/resource cleanup. Preserve the
  existing portable context actions and expose a clearly named native-menu
  action. Non-Windows invocation returns an explicit unsupported error.
  Reserve modal ownership before native loops and retain targets, request,
  owner window and context until return. Defer competing actions/close; balance
  STA initialization and release hooks before their COM/owner resources.
  Extension compatibility depends on installed handlers/architecture/policy.
  Only actual Shell invocation is acknowledged; external verbs are not app
  mutations and cannot enter the app's undo history as verified outcomes.
- GUI context controls and keyboard shortcuts plus Emacs APIs expose these
  operations. New actions must preserve the actual 800x600 layout and ordinary
  text-editing shortcuts. Existing protocol schemas and all old assertions stay
  compatible. New snapshots/readers remain bounded and data-only.
  Public GUI labels are `Create ZIP`, `Extract ZIP`, `Undo`, `Permissions` and
  `Native Shell Menu`; permission and ZIP prompts retain frozen targets.

## Validation

Retain the original client57, native Emacs6, GUIall1, batches15,
operations12, DND7, workspace12 and everyday21 cases. Current totals are
client65, native Emacs7, advanced54 groups, platform6, metadata-stub4, ZIP-stub11 and four
warning-as-error compilation inputs; see the source-bound validation record.
Add focused executable tests
and a pre-change negative control for each new feature. Preserve count/schema
checks; no silent baseline increases or exclusions. Validate the Windows adapter
against actual SDK headers/toolchain, retaining Windows runtime/manual checks
as pending unless executed. Publish source-bound historical evidence only after
actual runs, and distinguish native invocation from completed ACL/file changes.

Identity checks and revalidation reduce detected races but are not an atomic
lock against unrelated external pathname writers. Do not claim race-proof undo.

## Advanced wire additions

`zip-create` uses the indexed request schema with `version`, `request`,
`command=zip-create`, empty `path`, `count`, ordered `path0..path(count-1)` and an exact
unused archive `destination`. `zip-extract` uses only the single-operation
keys `version`, `request`, `command`, archive `path` and exact new directory
`destination`. Inputs are frozen before publication; neither command resolves
an empty path against later GUI selection. Both await the actual engine result.

`undo.ini` contains exactly `Undo(version=1,count=0..32)` then ordered groups
`Undo0..Undo(count-1)`, newest first. Each group has exactly `id`, `operation`
and `count`; IDs are unique positive signed-int64, and count is 1..256.
Operation labels are `copy`, `move`, `rename`, `mkdir`, `trash`, `zip-create`,
`zip-extract` and `chmod`. `trash` in Undo denotes a verified backend handle,
not every ordinary GIO Trash request. Error snapshots have only
`UndoError(version=1,message)` with at most 256 scalar Unicode characters.
The snapshot is bounded to 1 MiB and is not a durable per-request result.

`undo` uses the standard four request keys and a positive top-group ID in
`path`; stale/non-top IDs fail without changing history. `shell-menu` uses an
indexed frozen-target manifest without destination. `permissions` takes a
single absolute target in `path`, acknowledging editor invocation only.
`chmod` uses the exact single-operation schema with an absolute target and
`destination` containing three/four octal digits for mode0000..0777; it is
unsupported on Windows, where Security edits are native/external. Existing
single/batch Trash wire schemas are unchanged; recovery capability is explicit
in the resulting history/warning and is never inferred from Trash success alone.

## Emacs interface and platform evidence

The Emacs APIs are `sumi-explorer-zip-create` (archive, optional files/timeout)
and `sumi-explorer-zip-extract` (archive, destination, optional timeout), also
available as interactive commands. `sumi-explorer-undo-history` returns header
fields plus newest-first group alists under `groups`. `sumi-explorer-undo`
accepts an optional group ID; nil reads the newest ID once and never substitutes
another on failure. `sumi-explorer-permissions` invokes one frozen target's
editor; `sumi-explorer-set-permissions` submits an explicit octal mode.
`sumi-explorer-shell-menu` accepts an optional frozen target list, falling back
to selection/current directory. Timeout and quit retain the one pending request
for `sumi-explorer-await`; no snapshot establishes durable command success.

Real Windows CI passed the native suites and four Emacs/application cases,
including a Unicode multi-chunk ZIP roundtrip, top Undo, replacement/readonly
refusal, junction ancestor protection and actual Shell-menu cancellation. See
[Windows validation](windows-validation.md) for source-bound receipts and scope.
Native ACL Apply/Cancel, third-party handler coverage and comprehensive desktop
interaction remain unverified. macOS application runtime remains unverified.
