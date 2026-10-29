# Search, conflict transfers, external MOVE, ZIP64 and durable Undo

## Objective

Extend the existing NeLisp GTK/GIO application with the five requested daily-use
features. Keep version-1 clients and their conservative default behavior working.
Do not represent build-only Windows/macOS evidence as full interaction coverage.

## Recursive search

Provide a distinct recursive search action and Emacs commands; the current-folder
filter keeps its existing meaning. Search an absolute local root asynchronously,
match Unicode filenames, show full result paths, and open a result's containing
folder/select the result through existing navigation. Bound traversal/results,
do not follow directory symlinks or escape the root, surface inaccessible paths
and truncation, and support cancellation/close without stale results replacing a
newer search. Empty/invalid input must not start an unbounded filesystem scan.
Limits are 2000 matches, 100000 visited entries, depth 64 and 1 MiB result data.
Ancestor checks use paths and do not pin directory handles against hostile
concurrent symlink replacement.

## Explicit conflict policy

Existing protocol Copy/Move remains no-overwrite by default. Add explicit Replace,
Merge, Merge-and-replace and Skip choices through GUI and Emacs commands, with
Cancel as a safe choice. Merge preserves unrelated destination entries. Directory
versus file mismatches and symlink/reparse traversal must be refused, not followed.
Reject self/descendant aliases and freeze paths/policy before execution. Never
overwrite bytes in place: retain a validated same-filesystem backup of replaced
objects before publishing replacements. Failure/cancellation reports actual
partial outcomes and retained recovery paths. Undo reverses only this operation's
changes, restores replaced bytes and preserves unrelated destination contents.
Do not delete source trees for a partially completed move/merge.
Limits are 256 changed roots, 100000 traversal nodes, depth 64 and a 1 MiB
versioned recovery action list. Refuse oversized recovery plans before mutation.

## External MOVE drop

Use GTK asynchronous drop completion. Negotiate COPY/MOVE with real external GTK
senders; preserve default COPY and reject LINK/remote input. For external MOVE,
copy first and acknowledge MOVE exactly once only after the entire accepted drop
succeeds. The sender owns source cleanup: the receiver must not also delete those
source paths. Rejection, collision, cancellation, partial failure and window close
must never send a successful MOVE acknowledgement. Keep the drop/context alive
until its real completion. Test sender-visible completion/deletion, not just the
destination snapshot. Existing default COPY collision rejection is retained.
Default COPY retains the existing no-follow batch copier, including symlink
entries. Its GTask owns the external drop through completion and close. MOVE
continues to reject an entire manifest containing unsupported links.

## ZIP64

Support ZIP64 creation and extraction, including 64-bit sizes/offsets and extended
entry counts, through streamed libarchive I/O plus strict raw-header validation.
Validate EOCD/locator/extra fields/descriptors, checked signed-range arithmetic,
single-disk consistency, local/central agreement and overlap. Preserve encryption,
path traversal, symlink, name collision, CRC and resource checks. Explicitly
document bounded resource limits that still permit real ZIP64 sizes and counts.
Exercise forced small ZIP64, a real >4-GiB logical member, an archive with >65535
entries, malformed metadata and independent-reader interoperability. Do not fake
large-file support by merely removing rejection of the ZIP64 signature.

## Durable Undo and replacement recovery

Keep bounded data-only history under a private OS state/profile directory; allow
`SUMI_EXPLORER_UNDO_DIR` to isolate tests and deliberate profiles. Restore committed
groups and monotonic IDs after application/session restart. Use one writer per
journal, OS-held locks released on process death, atomic durable replacement and
strict bounded decoding. Multiple windows must retain independent valid journals;
do not silently overwrite another process's history. Preserve existing identity,
metadata/content fingerprints, parent witnesses and conservative refusal on
external changes. Never evaluate serialized Lisp or trust arbitrary backup paths.

Persist intent before destructive replacement and retain owned recovery backups
through restart. Ambiguous interrupted operations must be reported and retained,
not automatically deleted or falsely marked undoable. Publish completion only
after its committed history has been persisted; if ordinary non-destructive work
finishes but persistence fails, report the real outcome and Undo unavailability.
Replacement must refuse before mutation if durable recovery cannot be established.
Bound retention and clean up only validated owned obsolete backups.
Missing/corrupt established sequence state and prerelease profiles without the
establishment marker fail closed instead of silently recreating IDs. Transient
sequence-lock contention uses bounded retries; restore compares observed journal
IDs against a fresh locked counter snapshot after scanning.

Windows legacy output inverses retain and report their durable quarantines.
Windows/macOS opaque Trash inverses refuse before mutation when a filesystem
durability barrier is unavailable. Ordinary Trash and other supported inverses
retain their documented actual-outcome reporting.

## Integration boundaries and acceptance

The shared journal interface uses `journal_intent(store,text)` for a durable
old-history-plus-intent write, `journal_clear_intent(store)` for memory-only
staging, and `journal_flush(store)` for one atomic durable commit. Stage the new
group/history and cleared intent together before that single commit. Failed
commit blocks later mutation and preserves the old on-disk intent/backups. Undo
inverse mutations require their own durable intent and atomic completion too.
Lock a stable separate lock file, never the replaceable journal inode. Prefer
nonempty inactive history over a newer empty journal when restoring.

UndoStore extends from 96 to 136 bytes: profile handle96, journal handle104,
intent112, enabled/healthy120, diagnostic128. Synthetic 24-byte stores in two
advanced tests must grow as well. Keep `group_free` memory-only; obsolete backup
cleanup must follow successful durable eviction, never precede it.

Transfer op13 keeps ordinary 104-byte items and their original/current paths,
fingerprints, parent witnesses and link guards. Group24 is an owned UTF-8
string/string map; `sumi-transfer-v1` contains the versioned GKeyFile action list.
The existing op5 group24 is an ephemeral key-only Trash set and must not be read
as a string/string map. External MOVE records a source-restoring inverse before
acknowledgement. It must never inherit ordinary COPY Undo, which could delete the
only remaining copy after the sender cleans up. Validate the entire drop manifest
and finish with exactly one negotiated action only after full completion.

Preserve ctx736/UI160 and existing job/item/group layouts unless a documented
change updates every synthetic fixture. An appended Undo-store extension is
allowed with a complete allocation/caller audit. Main-thread GTK ownership and
worker-owned filesystem state remain separate. Native DSL definitions are quoted;
use bounded syntactic extraction, not broad index scans. Anvil numeric options
such as `budget-chars` are strings in the current tool schema.

Keep all 420 existing behavioral assertions and 28 design checks; fixture setup
may change where the feature intentionally changes behavior. In particular, the
old valid-ZIP64-rejection fixture must become a malformed-ZIP64 fixture, with the
same rejection assertions plus new positive coverage. Record every authorized
baseline adjustment. Existing navigation33 checks remain required.
The retained aggregate-name rejection fixture grows from 4000 to 80000 names
to exceed the new 32 MiB encoded metadata budget; its original rejection
assertions remain unchanged. The metadata C fixture defines GNU/POSIX features
before headers because it includes the platform bridge in the same translation unit.
Synthetic inverse fixtures allocate a complete store, initialize a private
journal, reserve real monotonic IDs and supply live item ordinals. GUI setup
waits for the process-owned mapped window; the pending-clipboard close fixture
targets that exact PID instead of another visible Explorer window.
The retained inverse-cleanup fixture injects a proven pre-detachment intent
refusal and empty-quarantine cleanup failure. It no longer models an ambiguous
native rename failure as safely retaining usable history. Its bypass control
executes the real rename and proves retained bytes; real post-mutation barrier
failures are covered by the durable Undo suite.
POSIX mode inverses keep the object descriptor open through the final mode
change and synchronization, including mode 0000. An unreadable owned object may
receive a temporary owner-read bit before its identity-checked descriptor is
opened; any failure after that first mutation attempt retains ambiguous intent.
Dedicated tests cover both mode directions, files/directories, and real
synchronization/intermediate-open failures. Disposable local-file test runtimes
disable GVFS FUSE mounts so teardown cannot traverse a disconnected mount.

Each feature has focused real-GUI/Emacs/filesystem acceptance and a baseline-red
control. Receipts bind exact inputs/binary/logs and reject malformed evidence.
Run the integrated Linux baseline, design/navigation suites, actual Windows CI,
and a separate gpt-6-astra ultra completion audit. Launch the completed application
and capture a real screenshot. The ledger defines eighteen criteria before code.
The integrated GUI suite also forces both Search/external-MOVE completion orders
during close. Close intent blocks new commands and prevents MOVE acknowledgement;
the context is released only after both owners finish. Its strict verifier
replaces the initial field-only S6.3 check without changing the denominator.

Primary references: [GTK asynchronous drop](https://docs.gtk.org/gtk4/class.DropTargetAsync.html),
[drop completion](https://docs.gtk.org/gdk4/method.Drop.finish.html), and
[libarchive ZIP writer](https://github.com/libarchive/libarchive/blob/master/libarchive/archive_write_set_format_zip.c).
