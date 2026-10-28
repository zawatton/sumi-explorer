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

## Initial file-transfer scope

Incoming drops accept one local regular file and copy it asynchronously into
the current directory without overwrite. Multiple files, directories, symlinks
and MOVE are rejected in this initial implementation. Outbound drag exports the
selected local paths as a GdkFileList; the receiving application owns its copy
operation and may support directories. The incoming restriction is not a claim
about another application's capabilities. A failed copy can leave a partial
new destination file. The GUI Cancel button also cancels an active incoming
copy. Atomic destination publication remains future work. The separate operation
engine supports recursive and multiple copies through the operation bar and Emacs; it does
not expand the advertised incoming drag formats.
