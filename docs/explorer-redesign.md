# Explorer interaction redesign

## Purpose and reference

Replace the accumulated prototype controls with a familiar desktop file-manager layout. Follow the information hierarchy of Windows 11 File Explorer without claiming native Windows styling or complete feature parity.

Primary references:

- [Microsoft: File Explorer in Windows](https://support.microsoft.com/en-au/windows/experience/fileexplorer/file-explorer-in-windows)
- [Microsoft: explore and navigate File Explorer](https://support.microsoft.com/en-us/accessibility/windows/use-a-screen-reader-to-explore-and-navigate-file-explorer-in-windows)
- [Microsoft: menus and context menus](https://learn.microsoft.com/en-us/windows/apps/develop/ui/controls/menus-and-context-menus)

## Layout contract

1. Put a compact tab strip at the top, with the active folder and a new-tab affordance. Tab actions must remain accessible from the overflow menu or the tab strip.
2. Place Back, Forward, Up and Refresh together beside an expanding address region. The [folder navigation increment](folder-navigation.md) uses clickable breadcrumbs by default and reveals the editable address with `Edit location` or Ctrl+L. Keep the current-folder filter separate and label its limited scope honestly; do not imply recursive search.
3. Put common file actions on a compact command bar: New folder, Cut, Copy, Paste, Rename, Trash, Undo. Use coherent symbolic icons, tooltips and accessible names. Group infrequent commands under Sort, View and More popovers rather than displaying every control continuously.
4. Put Home, existing OS-configured standard folders and real existing bookmarks in a left navigation pane. Distinguish navigation from view controls. Do not display fictional drives, cloud services, or unsupported actions.
5. Give the central file list the remaining width and height, with consistent row spacing and selection feedback. Keep the status/error and progress region unobtrusive at the bottom.
6. Remove the permanently visible destination entry and duplicated bottom operation buttons. Show operation-specific modal or popover input for exact-path Copy/Move, Rename, New folder and ZIP actions. Preserve confirmation, frozen selection, async cancellation and non-overwrite semantics.
7. All existing operations must remain available, including Properties, Permissions, native Shell menu, ZIP create/extract, Undo, bookmarks, presentation controls, new/close tab and new window. Keep Emacs and data-only protocol behavior unchanged.
8. Maintain the right-click file context menu and keyboard selection. Menus and dialogs must close predictably with Escape, identify their purpose and have a clear primary action. Preserve file-operation confirmations.
9. Validate at 1000x700, 1280x800 and 1600x900 under GTK. No essential action, status, dialog confirmation or cancellation may be clipped or pushed below the window. The left pane must not consume the file area's useful width. Use real toolkit layout, not coordinate-dependent application behavior.

## Implementation boundaries

The quoted NeLisp DSL in `explorer/sumi-explorer-native.el` is not a collection of top-level Elisp functions. The Anvil lexical context request for `sumi_explorer_activate` returned no bounded excerpt because it treated the whole 4,246-line DSL form as one candidate. Use explicitly scoped extraction of the enclosing DSL definitions for this work; do not repeat full-file index scans or claim semantic caller completeness.

Keep ZIP, Undo, transfer, identity, protocol and Windows platform providers intact. New widget pointers must use explicitly documented free context storage or a separate owned UI struct. Do not repurpose occupied offsets. Avoid dependence on a platform-specific icon font; use GTK symbolic icons and CSS. Honor the platform theme while giving navigation, command bars, content and status distinct visual grouping.

Input dialogs freeze the selected pathname strings and source directory. The existing operation engine establishes filesystem identities when an operation is accepted; this redesign does not add file-ID pinning at dialog-open or claim protection against same-path substitution during input. Existing operation and Undo identity checks remain in their original execution stages.

## Acceptance and evidence

- Retain every existing behavioral assertion; update coordinate assumptions to semantic or geometry-derived interactions where the new layout requires it. Record baseline counts and prove the new layout check fails against the old executable.
- The default viewport intentionally changes from 800x600 to 1000x700. Existing assertions of the default size must check the new dimensions without removing assertions. The supported minimum for this design is 1000x700; this change does not relax file-operation or safety expectations.
- Add focused GUI checks for layout, toolbar/menu availability, tabs/navigation, operation dialogs and a real file workflow. Preserve actual mouse/keyboard interaction rather than substituting protocol-only checks.
- Run the existing full Linux 220-check baseline once after integration, plus the new focused GUI suite. Run actual Windows CI for changed native sources. Record source-bound receipts honestly.
- A separate gpt-6-astra ultra audit reviews the final UI, diff, retained coverage and evidence. Publish an actual application screenshot and launch the redesigned application on the user's desktop.
- Continue to disclose untested Windows ACL Apply/Cancel, third-party Shell extension coverage and macOS runtime. A visual redesign does not establish those capabilities.

The progress ledger is `tools/ai/explorer-design-progress.org`; its nine criteria must not be reduced to hide failures.
