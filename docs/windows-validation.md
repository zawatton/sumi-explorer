# Windows x86_64 validation plan

Status: **not run on Windows**. This is a proposed developer setup and manual
acceptance checklist, not a tested PowerShell script or a support certification.
Existing x86_64 COFF object emission proves neither Windows linking nor runtime
behavior. Linux results cannot mark these checks PASS. See the
[everyday contract](everyday-features.md) for feature semantics and development
status; this document does not add Windows Shell extensions or native ACL tools.

## Prepare an explicit native toolchain

Use a Windows x86_64 machine, an existing Git installation and native Windows
GNU Emacs. GNU provides Windows installers/archives; preserve the supplied
directory layout and record the exact build used. Confirm `system-type` is
`windows-nt` and `system-configuration` starts with `x86_64` using `M-:` in Emacs.
WSL/Cygwin Emacs does not establish this target.
[GNU Windows downloads](https://www.gnu.org/software/emacs/download.html#windows),
[GNU Windows FAQ](https://www.gnu.org/software/emacs/manual/html_node/efaq-w32/Downloading.html).

Install/update MSYS2 yourself following its official instructions. Use its
**UCRT64** environment for the x86_64 UCRT compiler/libraries, rather than mixing
MSYS, MINGW64 and UCRT64 artifacts. Complete the update/restart steps requested
by the installer/package manager before adding packages.
[MSYS2 environments](https://www.msys2.org/docs/environments/),
[updating MSYS2](https://www.msys2.org/docs/updating/).

In an MSYS2 terminal, review and run this package installation yourself:

```sh
pacman -S --needed mingw-w64-ucrt-x86_64-gtk4 mingw-w64-ucrt-x86_64-toolchain mingw-w64-ucrt-x86_64-pkgconf base-devel
```

GTK documents the UCRT64 GTK4/toolchain packages; MSYS2's `pkgconf` provides the
`pkg-config` interface used by this build. No package installation or global
configuration change is performed by this repository.
[GTK Windows setup](https://www.gtk.org/docs/installations/windows),
[MSYS2 pkgconf package](https://packages.msys2.org/packages/mingw-w64-ucrt-x86_64-pkgconf),
[MSYS2 pkg-config behavior](https://www.msys2.org/docs/pkgconfig/).

## Process-local setup, build and DLL discovery

Open a fresh PowerShell console in the parent directory where you want a new
checkout. The first two commands below clone the project and enter its root.
If it is already cloned, enter that root instead and skip those two commands;
also skip the compiler clone if present. Retain the README's compiler revision:

```powershell
git clone https://github.com/zawatton/sumi-explorer.git
Set-Location sumi-explorer
New-Item -ItemType Directory -Force target/deps | Out-Null
git clone https://github.com/zawatton/nelisp.git target/deps/nelisp
git -C target/deps/nelisp checkout f3551799d210ed32f594383997843f0e1bf25e24
```

Supply your actual MSYS2 root and native `emacs.exe`; no installation location
is assumed. These proposed commands change only this console's environment and
its children. Do not use `setx` or modify user/system PATH for this check.
[PowerShell environment scopes](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_environment_variables).

```powershell
$ErrorActionPreference = 'Stop'
if (-not $env:MSYS2_ROOT) { $env:MSYS2_ROOT = Read-Host 'Existing MSYS2 root directory' }
$msys = (Resolve-Path -LiteralPath $env:MSYS2_ROOT).Path
$ucrt = Join-Path $msys 'ucrt64/bin'
$emacs = (Resolve-Path -LiteralPath (Read-Host 'Native GNU Emacs emacs.exe path')).Path
$env:Path = "$ucrt;$env:Path"
$env:CC = Join-Path $ucrt 'gcc.exe'
$env:NELISP_REPO_ROOT = (Resolve-Path -LiteralPath 'target/deps/nelisp').Path
New-Item -ItemType Directory -Force target/windows-validation/config | Out-Null
New-Item -ItemType Directory -Force target/windows-validation/fixtures | Out-Null
$env:XDG_CONFIG_HOME = (Resolve-Path -LiteralPath 'target/windows-validation/config').Path
$env:SUMI_EXPLORER_ROOT = (Resolve-Path -LiteralPath 'target/windows-validation/fixtures').Path
[Environment]::SetEnvironmentVariable('SUMI_EXPLORER_SESSION', $null, 'Process')
& $emacs --version
& $env:CC --version
& (Join-Path $ucrt 'pkg-config.exe') --modversion gtk4
& (Join-Path $ucrt 'pkg-config.exe') --libs gtk4
& $emacs -Q --batch -l explorer/build.el *> target/windows-validation/build.log
if ($LASTEXITCODE -ne 0) { throw 'Build failed; retain build.log' }
Get-ChildItem -LiteralPath $ucrt -Filter '*gtk-4*.dll'
& (Join-Path $ucrt 'objdump.exe') -p target/sumi-explorer.exe |
    Select-String 'DLL Name'
& ./target/sumi-explorer.exe
```

Set the absolute isolated config directory and clear inherited session ownership
**before starting any test app or native Emacs that may load GLib**. GLib honors
`XDG_CONFIG_HOME` on Windows and caches the result, so changing it after the first
configuration lookup is insufficient. These settings keep bookmarks in ignored
test output and start the GUI on disposable fixtures; they do not change HOME or
global settings. Keep this environment for every child-window/Emacs check below.
[GLib user configuration directory](https://docs.gtk.org/glib/func.get_user_config_dir.html).

`pkg-config --libs` must identify the intended UCRT64 libraries. The process PATH
above makes that environment's tools and DLLs discoverable; do not copy random
DLLs into system directories. Record loader errors and inspect imported DLL
names and their actual files in the selected runtime. A listed GTK DLL alone is
not proof that all transitive dependencies, themes or runtime data load.
[MSYS2 environment prefixes](https://www.msys2.org/docs/environments/),
[Windows DLL search order](https://learn.microsoft.com/en-us/windows/win32/dlls/dynamic-link-library-search-order).

If using an MSYS2 shell instead, convert paths passed to native tools explicitly
with `cygpath -m`; MSYS2 documents automatic argument/environment conversion.
Record the exact shell and converted paths instead of assuming `/ucrt64` is a
native Windows path. The native Emacs build must use `windows-nt`, not a Unix
Emacs that happens to emit a COFF object.
[MSYS2 filesystem paths](https://www.msys2.org/docs/filesystem-paths/).

## Manual acceptance on disposable fixtures

Create a dedicated local test directory containing nested folders and files
with Japanese text, spaces, semicolons and mixed case. Windows filename rules
differ from Linux: do not treat unavailable LF/backslash filename fixtures as
Windows PASS evidence. Use isolated test configuration/session directories, and
freeze real absolute Windows identities before mutations. Do not use personal
documents or replace file associations to exercise Open.

Leave each item **NOT RUN** until observed on this Windows machine; record FAIL
and the exact error if unavailable. Features still under development are not
assumed to exist merely because their client command is defined.

- Launch with the prepared runtime; verify readable names, icons, navigation,
  selection, filter, sorting, hidden visibility and list/details. Compare source
  identities and resulting bytes, not only displayed labels.
- Create/switch/close tabs; check per-tab history and selection, forward-branch
  removal after Back, vanished-selection handling and rejection of last-tab
  closure. Exercise Ctrl+T/W, Ctrl+Tab, Alt+Left/Right/Up, Ctrl+L and F5 while
  ensuring editable controls retain normal text shortcuts.
- Open a second window with Ctrl+N/context action. Close its parent and verify
  the child remains usable with an independent session; close both deliberately.
  A successful spawn acknowledgement alone is insufficient.
- Check right-click selection preservation, Open and application Properties,
  Rename's single-target rule, new-folder creation, and frozen Trash confirmation.
  Declining Trash must leave files intact. Verify actual GIO trash behavior; an
  unsupported trash operation must error without permanent-delete fallback.
- Copy/Paste between tabs/windows and with Windows Explorer. For owned internal
  Cut, verify successful moves remove only completed entries and preserve source
  bytes on preflight failure. External clipboard data is COPY-only; do not mark
  arbitrary external Cut or cross-process internal-Cut interoperability supported.
  Replace clipboard contents during an operation and verify newer contents remain.
- Drag multiple nested directories/files from a real external application into
  Sumi using COPY, then check full destination bytes, symlinks where supported,
  source retention and collision rejection. Exercise outbound multiple-file drag
  to a real receiver. MOVE/remote inputs remain rejected. Planned multi-entry
  inbound support must be tested rather than inferred from single-file DND.
- Start a long copy/Paste, request cancellation and normal close, and inspect the
  actual final result/partial destination. No timeout may replay the operation;
  normal close must retain task ownership until completion.

From native GNU Emacs launched with the same process-local runtime environment,
set the repository directory and evaluate the following on disposable paths:

```elisp
(add-to-list 'load-path (expand-file-name "explorer" default-directory))
(require 'sumi-explorer)
(sumi-explorer-start fixture-directory)
(sumi-explorer-new-tab another-fixture-directory)
(sumi-explorer-tabs)
(sumi-explorer-view-settings)
(sumi-explorer-select-files fixture-paths)
(sumi-explorer-clipboard-copy)
(sumi-explorer-paste destination-directory)
(sumi-explorer-bookmark-add)
(sumi-explorer-bookmarks)
(sumi-explorer-stop)
```

Also exercise explicit copy/move/rename/Trash, batch results with their matching
request ID, timeout/C-g followed by `sumi-explorer-await`, and visible host errors.
Independent snapshots are last publications, not durable per-request success.
The Linux Xvfb/xdotool/Nautilus harness is not a native Windows test harness.

## Evidence to retain before changing platform status

Record Windows edition/build and x86_64 architecture; repository commit and
dirty-source hashes; public compiler URL/revision; native Emacs version,
`system-type` and `system-configuration`; GCC version/target, GTK/pkg-config
versions, effective UCRT64 runtime location and imported DLL list. Retain build
and launch logs, binary SHA256 (`Get-FileHash target/sumi-explorer.exe -Algorithm SHA256`),
fixture/result hashes, screenshots and per-item NOT RUN/PASS/FAIL outcomes under
`target/windows-validation/`. Sanitize personal paths before sharing diagnostics.
Record memory limits actually enforced; Linux `ulimit` is not evidence of a
Windows process cap. No Windows acceptance or validation record is created by
writing or reviewing this checklist.
