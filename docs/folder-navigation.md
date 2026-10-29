# Folder navigation

## Scope

Make the current folder's ancestry clickable and expose real standard user
folders in the sidebar. Keep the existing operation engine, data-only protocol,
tab IDs, history, bookmarks and Emacs commands unchanged.

## Interaction contract

- Use the address region for a compact, horizontally scrollable breadcrumb by
  default, alongside an accessible `Edit location` button. Each segment owns its
  absolute destination, exposes that destination as a tooltip, and uses the
  existing navigation path. Use GFile parent/basename operations rather than
  splitting separators, including for filesystem roots and Windows paths.
- `Edit location` and Ctrl+L reveal and focus the existing `Location` entry and
  select its current text. Enter navigates normally and returns to breadcrumbs
  after success. Failed input remains editable with the existing error feedback.
  Escape restores the current path, returns to breadcrumbs and focuses the file
  list. Clicking a segment must remain safe when navigation rebuilds that segment.
- Rebuild ancestry after successful navigation, tab switches, history traversal
  and refresh. Deep and long Unicode paths must not expand the main window or
  displace the filter, toolbar or file list. Preserve the supported 1000x700,
  1280x800 and 1600x900 viewports.
- Between Home and Bookmarks, show Desktop, Documents, Downloads, Pictures, Music
  and Videos only when GLib supplies an existing directory. Use
  `g_get_user_special_dir` with the established enum IDs (0, 1, 2, 4, 3, 7), never
  guessed English directory paths. Omit destinations GFile-equal to Home or an
  earlier shortcut. This is lexical GFile identity, not symbolic-link resolution.
  A shortcut navigates through the existing busy guard and history; it does not
  create folders, change bookmarks or imply drive/cloud support.
- Preserve context layout and ownership. Prefer widget-owned navigation data;
  if allocation sizes change, update and validate all extracted native fixtures.
  Do not read beyond a fixture's allocation or reuse occupied context offsets.

Primary API references: [GLib special directories](https://docs.gtk.org/glib/func.get_user_special_dir.html),
[directory IDs](https://docs.gtk.org/glib/enum.UserDirectory.html), and
[GFile parents](https://docs.gtk.org/gio/method.File.get_parent.html).
The quoted native DSL is not top-level Elisp; use scoped definition extraction
and bounded syntactic caller searches, without claiming complete semantic indexing.

## Acceptance

Retain all 420 existing Python assertions and the 28 design checks. Adapt only
the design suite's address visibility assumptions to the explicit address mode.
Add real pointer/keyboard checks for breadcrumb navigation, address modes,
configured/absent/deduplicated standard folders, history/tabs, long paths and a
file workflow. Prove a feature check rejects the baseline executable.

Run the 220-check Linux baseline, the 28-check design suite and actual Windows
CI for the final native change. Keep Windows ACL Apply/Cancel, external Explorer
transfer and macOS runtime limitations explicit. Record source-bound execution
evidence; verify corrupted evidence fails. Obtain a separate Astra ultra audit,
then publish and launch the resulting application with a real screenshot.

The new objective has nine criteria in
`tools/ai/folder-navigation-progress.org`. Earlier objectives remain separate;
their receipts must not be presented as evidence for changed sources.
