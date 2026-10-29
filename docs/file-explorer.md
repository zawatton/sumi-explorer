# Sumi file explorer

## Objective

Build a NeLisp file manager with a Sumi desktop GUI on Windows, macOS and
Linux. Support ordinary mouse operation, file properties, file drag and drop,
and commands from Emacs. Reuse operating-system file and application services.

This standalone project was extracted from Sumi. It contains the explorer's
NeLisp AOT GTK4/GIO application and optional GNU Emacs command client. The public
NeLisp compiler is an external build dependency; other Sumi applications and
historical renderer backends are not required.

## Ownership and interfaces

- The application owns navigation, selection, commands and operation results.
- The standalone explorer owns its desktop host and presentation; no separate
  Sumi checkout is required. GTK4/GIO provide portable widgets,
  file enumeration, application launching and file-transfer data formats.
- A platform provider owns native context-menu extensions, native properties
  and platform-specific file operations. A portable Properties dialog must be
  described as application properties, not as the native Explorer/Finder dialog.
- Emacs uses the same command boundary as the GUI. It may navigate the window,
  retrieve selection and request supported file operations. It must not execute
  received Lisp or construct shell commands from file names.

The GUI uses a location field, parent/refresh controls, a file list and an
operation bar. A sidebar and dual-pane view are optional. Preserve file names as
data, including Japanese text, spaces, quotes and newlines. Distinguish display
names from file identities. Errors must reach the caller and the visible UI.

## File-operation contract

Never overwrite an existing destination without an explicit user decision.
Validate before mutation; report partial completion accurately. Cancellation,
recursive directory operations, trash and symlink behavior need explicit
contracts and must not be inferred from a successful single-file copy.

Drag-and-drop support means real file data exchanged with another application,
not just moving a row inside this application. Start with copy semantics;
advertise move only after completion and source-deletion behavior are verified.
Disable unsupported actions rather than reporting simulated success.

Native menu integration is distinct from a portable right-click menu. Windows
Shell extensions, macOS Finder services and Linux desktop services have separate
providers and platform acceptance evidence. GTK support on three operating
systems does not establish that this application runs on all three.

## Implementation and validation

Keep new explorer code under `explorer/`, tests under `tests/`, and build outputs
under ignored `target/`. Keep this standalone application independent of other
Sumi applications. Use the public NeLisp AOT compiler; do not substitute a Python,
JavaScript or Rust implementation of the application.

`tools/ai/file-explorer-progress.org` measures the complete objective. Each
stage includes an end-to-end check. Platform checks stay pending until the
corresponding real platform has been exercised. Host Emacs tests, source
inspection and an offscreen screenshot do not establish standalone or native
platform compatibility.

First validate a small vertical slice: enumerate a real temporary directory,
show it in a native Sumi window, navigate and select with mouse/keyboard, and
observe that same location/selection through the Emacs command boundary.
Then add portable Properties and genuine file drag and drop before native
platform providers. Record a control that fails without the new implementation.

## Local command protocol

The launcher creates a private session directory and supplies its path through
`SUMI_EXPLORER_SESSION`. `SUMI_EXPLORER_ROOT` selects the initial directory.
There is one client and at most one outstanding request. Files are UTF-8 GLib
key-file data with one `[Explorer]` group. Escape values using GLib string
escapes (`\\`, `\n`, `\r`, `\t`, `\s`); never evaluate their contents.

The client atomically publishes `request.ini` with keys `version=1`, a positive
decimal `request` identifier in the signed 64-bit range, `command`, and `path`.
Navigation commands
are `open`, `select`, `refresh`, `properties`, `up` and `quit`. Mutations add
`copy`, `move`, `rename`, `mkdir` and `trash`, with an optional `destination`
field as specified in [the operation contract](file-operations.md). The host consumes
each request once and atomically publishes `state.ini` with keys `version=1`,
`request`, `status` (`ok` or `error`), `directory`, `selected`, and `message`.
After each consumed positive-id command finishes, the host also atomically
writes `response.ini` with the same schema. This is the durable result of that
command. GUI selection and copy events update only `state.ini`; they must never
replace a matching error response with an unrelated successful UI event. The
client waits for `response.ini` acknowledgements and reads `state.ini` for the
current location and selection. `response.ini` need not exist before the first
request.
The initial state uses request zero; state identifiers are in the range
0 through 9223372036854775807. Legacy `selected` contains the first selected
row. An independently consistent `selection.ini` carries the full multiple
selection; `select-many` and copy/move/trash batch commands extend version 1 as
specified in [the batch contract](file-batches.md). A timeout must not
silently retry or replace an outstanding request.

The native host also publishes state after mouse navigation or selection,
retaining the most recently acknowledged request identifier. The client may
read the current selection without submitting a request. File names containing
line breaks must round-trip as data. Native menu extensions are not yet provided.

The client keeps a timed-out request pending and never resends it. Call
`sumi-explorer-await` again to collect its late result. For long mutations,
`sumi-explorer-begin-operation` publishes without blocking and
`sumi-explorer-cancel` requests cancellation of that positive request ID.
Cancellation is cooperative; the final acknowledgement remains authoritative.
An explicit force-stop may interrupt a copy and is not equivalent to normal
shutdown.

Normal client shutdown requests graceful quit and waits for the owned process.
An active asynchronous copy must not be killed by an ordinary stop timeout.
Keep the session while the process is finishing; explicit forced disposal is a
separate action. Startup failure cleanup may forcibly stop its not-ready child.

## Everyday workflows

The [everyday-workflow contract](everyday-features.md) extends the application
with folder tabs, independent windows, per-tab history/presentation, clipboard
operations, bookmarks and context actions. Native GUI acceptance and all six
Emacs/native workflows passed on Linux with the recorded source inputs. Track
the increment in the [15-criterion usability ledger](../tools/ai/explorer-usability-progress.org).
Windows/macOS runtime support remains unverified.

Workspace IDs are stable positive signed-int64 values, with at most 64 tabs and
128 history entries per tab. The last tab cannot close. Independent windows own
independent sessions and may outlive their parent; a successful new-window
acknowledgement establishes process spawning rather than child readiness.
Same-window navigation/presentation is blocked while asynchronous work owns
the context. Filters are literal case-folded filename text of at most 256 scalar
characters; sorting, hidden visibility and list/details settings belong to each
tab. Sorting retains identities; hiding/filtering drops invisible selections.

`workspace.ini`, `view.ini` and session `bookmarks.ini` are independent last
successfully published snapshots, with bounded exact success/error schemas.
They are not durable results for an earlier request and cannot guarantee
freshness after an I/O failure. See the everyday contract for exact keys and
the README for Emacs examples.

Clipboard Copy/Cut freezes visible selection. Paste takes an absolute directory
or the active directory, shares global preflight/no-overwrite behavior, and
acknowledges only actual engine completion. External clipboard lists are
COPY-only; internal Cut requires the still-owned provider/generation and does
not promise cross-process external Cut. Partial results preserve completed work
and never overwrite a newer clipboard generation.
An unresponsive external selection owner may delay GTK/X11 read cancellation;
the request/context remain pending and alive until the real callback. No fixed
cancellation latency is promised for an external owner.

Bookmarks hold up to 64 unique absolute local directories in the platform
user-config directory. Independent windows serialize edits with exclusive
locking, reload and atomic replacement. Lock contention/stale locks and corrupt
data fail explicitly; locks are not stolen and corrupt config is not replaced.
Context actions and file-view shortcuts retain normal text-editing shortcuts,
single-target Rename, and confirmation of frozen Trash targets. The subsequent
[advanced increment](advanced-features.md) adds bounded ZIP operations, durable
Undo and Windows Shell/Security entry points. Indexing, permanent deletion and
complete native desktop integration remain outside these increments.

## File transfer

Incoming local COPY and MOVE use asynchronous drop completion and bounded
preflight. MOVE copies first, durably records a source-restoring inverse, then
acknowledges MOVE so that the sender can clean up. Failure/cancellation/close
finishes without a successful MOVE acknowledgement. The complete manifest must
be valid; remote input is rejected. Owned paths/context survive actual completion.
Default drops refuse existing targets; explicit conflict transfers follow
[the productivity expansion](productivity-expansion.md). A failed copy may leave
an explicitly reported partial new destination.
Outbound drag exports selected local paths as a GdkFileList; the receiving
application owns its operation.
