# Multiple selection and batch operations

This increment extends the existing version-1 data channel without changing the
six-field `state.ini` / `response.ini` schema or existing single-item commands.
Keep all current 30 client tests, two real Emacs/native workflows, 12 operation
checks and seven DND checks. Multiple incoming DND may remain unsupported; do not
silently change the existing rejected-directory-drop contract in this increment.

## Selection

Enable GTK multiple selection with Ctrl/Shift mouse/keyboard behavior. A legacy
`select` request first clears selection and selects exactly its one target.
New `select-many` requests use the existing Explorer keys plus `count=N` and
`path0` through `path(N-1)`; `path` is empty. Allow zero to clear selection, up to
256 distinct absolute local child paths. Validate the entire list before changing
selection; missing/invalid/duplicate paths must not partially change it.

Publish one atomic `selection.ini` snapshot with exactly one `[Selection]` group:
`version=1`, `status=ok|error`, `directory`, `count=N`, `message`, and indexed
`path0` through `path(N-1)`. Paths are GLib-escaped strings, not delimiter-separated
text, ordered by displayed row order. The snapshot is independently consistent;
the client must not combine it with a separate state read to construct identities.
On selection/serialization limit failure publish a compact error snapshot (zero
paths), never silently truncate or return stale successful selection. Legacy
`state.selected` contains the first selected row or empty. Right-clicking a member
preserves the selection; right-clicking another row selects that row alone.
Open/Properties use the first selected row; Rename requires exactly one row.

`sumi-explorer-selected-files` reads the validated snapshot when present. Only a
missing sidecar may fall back to legacy single selection; malformed/unsupported
snapshots must raise an error. `sumi-explorer-select-files` publishes `select-many`.
Every sidecar/request is bounded to 1 MiB, valid UTF-8 scalars, exact schema, and
256 paths. Reject remote names, duplicate/unknown keys, invalid escapes and bounds.

## Batch requests and results

New commands are `copy-many`, `move-many`, and `trash-many`. They carry existing
Explorer keys (`version`, `request`, `command`, empty `path`), `count=N` (1..256)
and indexed paths. Copy/move also require `destination`, an existing absolute
local directory; each exact new destination is directory plus source basename.
Trash has no destination. Reject duplicate and ancestor/descendant source lists,
target-name conflicts, missing/unsupported sources, invalid destination or any
already existing target before mutations begin. Reuse physical alias/tree checks
and source-symlink semantics from the one-item engine. Reject ambiguous input
conservatively; never follow source symlink entries or overwrite/merge a target.

One owned GTask processes the frozen batch serially off the GTK thread, shares
one cancellable, and preserves the existing close/result ownership contract.
Preflight the complete batch before any mutation. Keep traversal bounded over
the whole batch, not an unlimited per-item reset. At the first execution error
or cancellation stop, retain completed results, and mark untouched items skipped.
Do not roll back completed moves/trash/copies. Preserve the actual result when
cancellation arrives after completion. Report possible partial new destinations.

Before publishing a positive batch request's final durable `response.ini`, write
an atomic `batch-result.ini` with one `[Batch]` group (`version=1`, `request`,
`count=N`) and exactly N `[Item0]` .. `[Item(N-1)]` groups. Each item has `path`,
`destination` (empty for trash), `status=ok|error|skipped`, and `message`.
Response status is ok only if every item completed successfully. A rejected
preflight identifies the failing item and marks all other items skipped; malformed
manifests need only the standard error response. GUI batches must never overwrite
this protocol result file; show a truthful count/error summary in the UI.
Repeated source identities in a structured result are permitted only for rejected
preflight (exactly one error, all other items skipped, no successes), so a truthful
rejection of a duplicate-path manifest remains readable. Selection snapshots and
outgoing manifests still require distinct identities.

Before mutation, reserve the complete encoded result identities/index overhead
plus a bounded message allowance for every item within 1 MiB. Item messages are
limited to 256 Unicode scalar values; identities/destinations are never truncated.
An otherwise valid request whose result cannot fit is rejected before mutation
with only the standard error response. A result-publication failure must not
acknowledge success with a stale/missing result. Report that persistence failure
and actual completed-operation counts without implying rollback or mutation
failure when filesystem operations succeeded.

Client APIs: `sumi-explorer-begin-batch(command paths &optional directory)` accepts
`copy`, `move`, or `trash` and returns a pending ID. Existing await/cancel work on
that same ID. `sumi-explorer-batch-result(&optional expected-request)` strictly
validates the structured result and optionally requires the matching request ID.
Interactive `sumi-explorer-copy-files`, `sumi-explorer-move-files`, and
`sumi-explorer-trash-files` use a frozen GUI selection; Trash confirms those exact
paths before submission. Explicit single-path APIs remain compatible.

## GUI and acceptance

With multiple rows selected, Copy/Move treat the destination field as an existing
directory, and Trash confirms the complete frozen list. Existing single-row GUI
Copy/Move keeps exact-new-path semantics so existing workflows remain valid.
Outbound GFileList drag exports every selected identity. Keep unsupported incoming
formats explicit. Results stay independently readable after UI changes.

Demonstrate new acceptance red on the retained current executable, then green on
the implementation. Check Ctrl/Shift selection, Unicode/LF/semicolon/backslash
identity round trips, all-or-nothing selection validation, batch preflight with no
mutation, success, mid-batch error/cancel with per-item results, frozen confirmed
GUI Trash, outbound multiple-file DND and real Emacs/GUI workflows. Keep Linux-only
runtime evidence separate from Windows/macOS object emission and unverified native
integration. Separate Astra design and completion audits are required.
