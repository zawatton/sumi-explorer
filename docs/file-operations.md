# File operations increment

This contract defines one-item operations on the shared native/Emacs command
channel. [The batch extension](file-batches.md) adds multiple selection and batch
operations while retaining this one-item behavior. Platform Shell/Finder
extension menus remain separate acceptance items in the overall ledger.

## Request and completion contract

Protocol version 1 keeps its six-field state/response schema. Existing requests
are unchanged. New commands are `copy`, `move`, `rename`, `trash`, and `mkdir`.
`path` is an absolute local source path (the new directory for `mkdir`).
`destination` is an additional GLib-escaped string containing the exact new
absolute path for `copy`, `move`, and `rename`. No operation overwrites or merges
an existing destination. Rename stays in the same parent directory. Trash uses
GIO trash and never falls back to permanent deletion.

A mutation consumes one positive monotonic request ID, but publishes its durable
`response.ini` only after completion. UI events may update `state.ini` while it
runs. No subsequent request is consumed until the current operation finishes.
GUI operations use the same engine and no protocol response ID. The window stays
alive until its operation completes, including after a close request.

An atomic `cancel.ini` contains `[Explorer]`, `version=1`, and `request=ID`.
It targets only that positive protocol operation ID, is consumed once, and
cannot cancel a later operation. GUI cancellation stays in-process through the
Cancel button; the client rejects cancellation when it has no pending request.
A cancellation request is cooperative:
the final response reports the actual result, which may already have completed.
Emacs offers nonblocking begin, await/resume, and cancel APIs; a timeout preserves
the pending request and its session. Cancellation is not reported as success.

## Filesystem and ownership contract

Run blocking GIO work away from the GTK thread, with one owned cancellable and
one owned task. Worker threads must never call GTK or touch live UI state.
Completion runs on the main context and alone publishes the final response.
Keep the context alive until completion. Do not replace existing destinations.
Copy directory trees without following symlink entries, reject source-to-self
and descendant copies (including existing destination-parent aliases), and bound
traversal to depth 64 and 100,000 visited entries per traversal phase.
Failures/cancellation may leave a newly created partial destination;
report that possibility with its path, preserve the original source, and do not
perform speculative recursive rollback. Native move is attempted without a
copy/delete fallback, so cross-filesystem move can fail without deleting source.

Provide native controls for Copy, Move, Rename, New Folder, Trash, and Cancel.
Destination/name entry and Trash confirmation must precede a GUI mutation.
Retain existing row activation, Properties, and external file COPY behavior.

## Acceptance

Check real native operations on disposable fixtures: Unicode/nested directory
copy, symlinks, collisions preserving bytes, same-parent rename, move, mkdir,
trash with isolated XDG data, cancellation/recovery, and GUI operations. Exercise
the same operations from real Emacs and retain all 14 existing client tests plus
the existing native integration test. A pre-change executable must fail the new
mutation acceptance. Keep Windows/macOS runtime status explicitly unverified.
