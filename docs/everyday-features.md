# Everyday file management increment

## Goal and limits

Add ordinary file-manager workflows while preserving the existing bounded,
no-overwrite file engine and durable protocol completion. This increment covers
folder tabs, independent windows, per-tab navigation, folder filtering, sorting,
hidden visibility, clipboard operations, context actions, shortcuts, bookmarks
and inbound multi-item directory copy. The Windows/macOS runtime criteria in
the existing feature ledger remain unverified. This is not complete Windows
Shell compatibility. The subsequent [advanced increment](advanced-features.md)
adds bounded ZIP operations, session Undo and Windows Shell/Security entry points;
its [Windows evidence](windows-validation.md) records the narrower native checks.
Recycle-bin browsing and filesystem indexing remain outside these increments.

## Stage 1 workspace contract

- One process owns one window, one command session and one file-operation task.
  New windows launch the current executable using an argument vector and an
  independent session, without a shell or inherited session ownership. A child
  can outlive its parent. A failed launch reports an error.
  A successful command acknowledgement confirms OS process spawning, not child
  GUI readiness; tests separately verify the second window and its lifetime.
- Up to 64 tabs have stable positive integer IDs. Each tab owns its directory,
  selection and bounded history (128 entries). IDs increase without reuse up to
  signed-int64 maximum; exhaustion fails explicitly. All same-window workspace,
  navigation and presentation actions are blocked while a task or asynchronous
  clipboard read owns the UI. Closing the last tab is rejected; closing
  the window retains existing cooperative shutdown behavior.
- Existing `state.ini`, `selection.ini`, durable `response.ini`, mutation,
  cancellation and per-item-result contracts remain compatible.
- Add commands `new-tab`, `new-window`, `close-tab`, `activate-tab`, `back`,
  `forward` and `home`, retaining the exact version-1 request schema with `path`.
  `new-tab`/`new-window` take an absolute local directory (empty means current
  directory); `activate-tab` takes a positive decimal ID; the others require an
  empty path. Unknown, malformed and unavailable commands fail explicitly.
- `workspace.ini` is an independently published bounded UTF-8 snapshot:

```ini
[Workspace]
version=1
active=1
count=1

[Tab0]
id=1
directory=/absolute/local/directory
back=0
forward=0
```

- Groups are exactly `Workspace`, then `Tab0` through `Tab(count-1)`. Workspace
  keys are exactly `version`, `active`, `count`; tab keys exactly `id`,
  `directory`, `back`, `forward`. Boolean values are `0` or `1`. IDs are unique,
  active identifies a listed tab, paths are absolute local scalar Unicode, and
  count is 1..64. Snapshot publication must not overwrite durable completion.
- Success serialization is preflighted against 1 MiB. An error snapshot contains
  only a `WorkspaceError` group with exactly `version=1` and `message` (at most
  256 scalar Unicode characters). It never contains successful workspace data.
  Publication failure returns an error and invalidates the prior snapshot where
  possible; an I/O error is not evidence that a requested action succeeded.
  The snapshot is the last successfully published independently consistent
  workspace, and cannot guarantee freshness after a filesystem I/O failure.
- Snapshot errors are explicit. Emacs exposes `sumi-explorer-tabs`,
  `sumi-explorer-new-tab`, `sumi-explorer-new-window`,
  `sumi-explorer-close-tab`, `sumi-explorer-activate-tab`,
  `sumi-explorer-back`, `sumi-explorer-forward` and `sumi-explorer-home`.
- Navigation validates the destination before changing history or the active
  directory. Going back/forward does not append another history entry. Fresh
  navigation after Back discards only the forward branch. Tab selection is
  restored by file identity and vanished selections are discarded explicitly.
  A new tab becomes active with an empty selection and one history entry.
  Closing the active tab selects the next tab if present, otherwise the previous
  one. Refresh replaces the listing without appending history.
- GUI controls and Ctrl+T, Ctrl+N, Ctrl+W, Ctrl+Tab, Ctrl+Shift+Tab, Alt+Left,
  Alt+Right, Alt+Up, Ctrl+L and F5 expose the corresponding workspace actions.
  Editing controls retain their normal text shortcuts.

## Presentation and convenient locations contract

- Presentation belongs to each tab. Commands retain the exact version-1 schema:
  `filter` takes literal filename text (at most 256 scalar characters), `sort`
  takes `name|size|date|type`, `order` takes `asc|desc`, `hidden` takes `show|hide`,
  and `view` takes `list|details`. Filtering is case-folded literal matching in
  the current directory, not recursive or indexed search. Invalid values fail
  before changing settings. Directories sort first; equal keys use deterministic
  identity tie-breaking. Rows retain their full names and themed file icons;
  details show type, size and modification time.
- Sorting preserves selected file identities. Filtering/hiding discards newly
  invisible selections before they can become operation targets. Failed listing
  refresh does not commit a partially changed presentation.
- `view.ini` is an independent snapshot containing one exact `View` group:
  `version=1`, `directory`, `filter`, `sort`, `order`, `hidden` (`0|1`) and `view`.
  It uses the same 1 MiB cap and explicit error behavior as workspace snapshots,
  with an exact `ViewError` group (`version`, `message`) on error.
- Emacs APIs are `sumi-explorer-view-settings`, `sumi-explorer-filter`,
  `sumi-explorer-sort`, `sumi-explorer-order`, `sumi-explorer-show-hidden` and
  `sumi-explorer-set-view`. They return standard acknowledgements; independent
  snapshot reads are never described as durable per-request results.
- `bookmark-add`, `bookmark-remove` and `bookmark-open` take absolute local
  directories; an empty add/remove path means the active directory. Open must
  identify a saved bookmark. A Home/bookmark sidebar exposes the same navigation.
- Persist up to 64 unique paths in the platform user-config directory under
  `sumi-explorer/bookmarks.ini`, with a versioned bounded UTF-8 key-file schema.
  Independent windows serialize edits with an exclusive `bookmarks.ini.lock`, reload under the
  lock and atomically replace the file. A competing/stale lock fails explicitly;
  it is never silently stolen. Corrupt data is reported rather than overwritten.
  Tests isolate configuration and verify competing writers do not lose edits.
- Session `bookmarks.ini` contains an exact `Bookmarks` group with `version=1`
  and `count=0..64`, then `Bookmark0..Bookmark(count-1)`, each with exactly `path`.
  Error snapshots contain only `BookmarksError` (`version`, `message`). Emacs APIs
  are `sumi-explorer-bookmarks`, `sumi-explorer-bookmark-add`,
  `sumi-explorer-bookmark-remove` and `sumi-explorer-bookmark-open`.

## Clipboard, context actions and transfer contract

- Commands `clipboard-copy` and `clipboard-cut` take an empty path and freeze the
  visible GUI selection. `paste` takes an absolute destination directory or an
  empty path for the active directory. These use existing 256-item/1 MiB limits,
  global physical preflight, no-overwrite behavior and per-item outcomes.
- Write a typed system GDK file-list provider. Internal Cut is authorized only
  by the still-owned provider/generation, never by arbitrary external file-list
  contents or untrusted cut markers. External paste is COPY-only. This does not
  promise cross-process or platform-native external Cut compatibility.
- Before asynchronous clipboard reads, reserve busy/close ownership, retain the
  context, originating tab and frozen destination. No navigation or normal close
  may free them. Clipboard-read failure is a final error; successful reads start
  the shared engine and only its actual completion acknowledges a mutation.
  Cancellation is cooperative: on GTK/X11, an unresponsive external selection
  owner can delay the read callback and cancellation completion. Keep the request
  pending and the context alive, without filesystem changes or a false final
  acknowledgement, until the backend returns. There is no fixed cancellation
  latency guarantee for an external clipboard owner.
- After an internal Cut, remove only successfully moved entries from the same
  still-owned clipboard generation. Preserve failed/skipped entries. Never clear
  or overwrite a newer clipboard that replaced it while processing.
- Emacs APIs `sumi-explorer-clipboard-copy`, `sumi-explorer-clipboard-cut` and
  `sumi-explorer-paste` expose the same commands and resumable completion.
- Context menus expose open/new tab/new window, copy/cut/paste, rename, new
  folder, trash and Properties. Blank-space actions operate on the directory.
  Rename is single-target; prompts and Trash confirmation freeze exact targets.
  Popups freeze their directory and selected identities; a change invalidates
  the popup, and queued actions revalidate those identities before operating.
- Keyboard shortcuts include Ctrl+C/X/V/A, F2, Delete and Ctrl+Shift+N in the
  file view. Editable widgets retain their normal text shortcuts. No permanent
  deletion fallback is introduced.
- Inbound DND accepts multiple absolute local file/directory/symlink entries
  only through the shared bounded COPY preflight and worker engine. Do not follow
  symlink entries or bypass traversal limits. Reject remote paths and MOVE;
  retain all drop data/context until actual completion and report partial output.
  Actual external data transfer tests must verify bytes and source retention.
  The synchronous GTK drop acceptance means accepted/queued, not a completed
  copy. Owned paths and job context survive through engine completion; the GUI
  and operation results report the actual outcome. The drag source does not
  receive an asynchronous filesystem-completion result.

## Validation discipline

Keep all six existing test files and their prior cases, except the former DND
directory-rejection case, which is replaced by real directory-copy and collision
coverage to implement this increment's explicit directory-transfer requirement.
Add actual widget and
protocol tests with a pre-change negative control; do not substitute source
matching for behavior. Tests isolate configuration, data and session paths,
close all child windows/processes, and use the 8 GiB cap and machine-wide heavy
runner in the Notes workspace. The public helper remains usable without Notes.
