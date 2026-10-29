# Windows x86_64 validation evidence

Status: **automated native feature acceptance passed**.
[Run 40806815295](https://github.com/zawatton/sumi-explorer/actions/runs/40806815295)
tested published main commit `b9ea315cb5e743177d7fdf7533b07db06b7b918f` on the `windows-2022` runner.
The [historical receipt](windows-validation.json) binds the final accepted run
and its exact source bytes; this is narrower than complete Windows compatibility.

| Check | Observed result |
| --- | --- |
| Official GNU Emacs 30.2, native x86_64 Windows | PASS: Japanese directory creation and exact-byte Japanese filename write/read. |
| MSYS2 UCRT64 Emacs 31.1-2 comparison | Directory creation succeeded; Japanese filename opening failed, reproducing the documented package defect. |
| Native build and owned GTK window close | PASS. |
| Shell C ABI/runtime suite | PASS: 59 assertions, including native and forward-slash Unicode paths, real COM Copy/Cancel, exact `CF_HDROP`, forwarding and persistent invocation-owner lifetime. |
| Local NTFS metadata suite | PASS: 229 assertions, including identity/ChangeTime changes, access denial, cleanup and strict handle accounting. |
| ZIP OS I/O suite | PASS: 63 assertions, including distinct multi-chunk bytes, native path/handle identity, source locks, cancellation, seek and handle-owned private snapshot cleanup. |
| Behavioral negative controls | PASS: seven controls reject broken forwarding, false success, temporary invocation ownership, raw forward paths, omitted ChangeTime and omitted file IDs in metadata/ZIP. |
| Native Emacs application ERT | PASS: all four cases (62 assertions): Unicode 131104-byte ZIP create/extract/top Undo and junction ancestor rejection; copy Undo/replacement refusal; readonly refusal/unsupported chmod; actual Shell-menu cancellation. |

ZIP cleanup tests rename the actual snapshot handle, place a distinct replacement
at its old pathname, and verify that closing deletes only the owned object.
FAT/ReFS fallback tests inject selected provider results over real native queries;
they do not certify physical FAT/exFAT/ReFS or network-volume support.

[MSYS2 issue 31733](https://github.com/msys2/MINGW-packages/issues/31733)
describes the Fortify/Unicode-open interaction. The same probe passes with
official GNU Emacs. If a later MSYS2 package fixes it, the driver records its
positive result rather than requiring failure. See the
[everyday contract](everyday-features.md) and
[advanced contract](advanced-features.md) for feature boundaries.

Native Security-page Apply/Cancel, third-party extensions, external Windows
Explorer transfer, packaging and comprehensive GUI interaction remain **NOT RUN**.
In particular, editor invocation is not proof of an applied ACL change.

## Public automated validation

The [Windows native workflow](https://github.com/zawatton/sumi-explorer/actions/workflows/windows-native.yml)
has path-scoped push/pull-request triggers and manual dispatch, read-only
repository permissions and official actions pinned by commit. It uses a Windows
x86_64 runner and the public NeLisp compiler at
`f3551799d210ed32f594383997843f0e1bf25e24`. MSYS2 UCRT64 GCC, GTK4,
libarchive and pkgconf still build and run the application; MSYS2 Emacs remains
installed for the comparison probe.

The preparation step downloads GNU's dependency-bundled portable
[`emacs-30.2.zip`](https://ftp.gnu.org/gnu/emacs/windows/emacs-30/emacs-30.2.zip)
into `target/deps` and checks its published SHA-256 **before extraction**:

```text
414d3a1a21147af257ebd98bdd15976fdcb5ed0563f6de89f76d4a4b5dad9c72
```

[GNU's checksum file](https://ftp.gnu.org/gnu/emacs/windows/emacs-30/emacs-30.2-sha256sum.txt)
is the checksum authority. Extraction stays under `target/deps/gnu-emacs-30.2`;
the executable hash is also pinned and its version/architecture checked. There
is no host installation or global PATH change. GNU Emacs is launched by explicit
path; the compiler and application DLL search still use the process-local UCRT64
environment.

The bounded worker runs the MSYS2 Unicode probe first and requires the official
GNU probe to pass before building. Official GNU Emacs runs `explorer/build.el`,
parses native compiler flags, and runs each of the four Windows ERT cases in a
fresh Emacs process against `SUMI_EXPLORER_TEST_BIN`. Only an actual ERT pass
counts; skips cannot satisfy acceptance. Metadata runtime failures remain
failures while later application checks still run, preserving independent
outcomes in the overall failed result.

An owned Windows Job Object limits per-process and whole-job private committed
memory to 8 GiB, with a 600-second worker timeout and owned-descendant cleanup.
This is not equivalent to Linux address-space limits. Dependency preparation
has a bounded download outside the worker; the workflow job timeout is 25 minutes.
Native paths are converted explicitly, and configuration/session/temporary files
stay in isolated test output.

The repository requires LF text checkout through `.gitattributes`. Before hashing,
the driver checks all 14 bound text inputs for CR bytes and rejects a mismatched
checkout. The manifest includes that attribute policy and the workflow itself;
its hashes are actual Windows file bytes, without source normalization.
A local `core.autocrlf=true` control produced CRLF for the old 13 inputs and exact
LF bytes for all 14 inputs after the policy. The accepted Windows run passed the
same check, and all 14 raw file hashes match the published sources exactly.

`python3 tools/verify-windows-validation.py` validates the published receipt's
exact schema, source set, binary/log hash formats and recorded check counts.
It needs no Windows machine or compiler. Like the Linux receipt verifier, this
checks historical source binding; editable JSON does not independently prove
execution, rerun tests or certify the pending manual checks.

Uploaded `windows-native-<run>-<attempt>` artifacts retain source/executable
hashes, dependency provenance, package/compiler/library versions, both Unicode
probe receipts, native suites and seven negative-control logs, four ERT receipts
and counts, and the actual GTK close result. Preserve failures as well as
successes. The manual setup below is an alternative; its whole command sequence
is not itself certified by the CI results.

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
pacman -S --needed mingw-w64-ucrt-x86_64-gtk4 mingw-w64-ucrt-x86_64-libarchive mingw-w64-ucrt-x86_64-toolchain mingw-w64-ucrt-x86_64-pkgconf mingw-w64-ucrt-x86_64-emacs base-devel
```

GTK documents the UCRT64 GTK4/toolchain packages; MSYS2's `pkgconf` provides the
`pkg-config` interface used by this build. The hosted workflow installs these
packages in its own runner; a local manual run requires an existing MSYS2 setup.
The driver does not change global PATH.
[GTK Windows setup](https://www.gtk.org/docs/installations/windows),
[MSYS2 pkgconf package](https://packages.msys2.org/packages/mingw-w64-ucrt-x86_64-pkgconf),
[MSYS2 libarchive package](https://packages.msys2.org/packages/mingw-w64-ucrt-x86_64-libarchive),
[MSYS2 pkg-config behavior](https://www.msys2.org/docs/pkgconfig/).

## Process-local setup, build and DLL discovery

Open a fresh PowerShell 7 console in the parent directory where you want a new
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

Supply your actual MSYS2 root; no installation location is assumed. The
preparation command obtains the pinned GNU build above. These proposed commands
change only this console's environment and its children. Do not use `setx` or modify user/system PATH for this check.
[PowerShell environment scopes](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_environment_variables).

```powershell
$ErrorActionPreference = 'Stop'
if (-not $env:MSYS2_ROOT) { $env:MSYS2_ROOT = Read-Host 'Existing MSYS2 root directory' }
$msys = (Resolve-Path -LiteralPath $env:MSYS2_ROOT).Path
$ucrt = Join-Path $msys 'ucrt64/bin'
./tools/windows-native-check.ps1 -PrepareEmacs
if ($LASTEXITCODE -ne 0) { throw 'GNU Emacs preparation failed' }
$emacs = (Resolve-Path -LiteralPath 'target/deps/gnu-emacs-30.2/bin/emacs.exe').Path
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
& (Join-Path $ucrt 'pkg-config.exe') --modversion gtk4 libarchive
& (Join-Path $ucrt 'pkg-config.exe') --cflags gtk4 libarchive
& (Join-Path $ucrt 'pkg-config.exe') --libs gtk4 libarchive
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

The automated results above cover their exact owned fixtures only. Leave each
additional manual item **NOT RUN** until observed on this Windows machine; record
FAIL and the exact error if unavailable. A defined client command alone does not
establish runtime behavior.

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
- Once the advanced GUI entries are integrated, create/extract a ZIP from frozen
  Unicode/spaced targets into exact unused output paths. Compare extracted bytes;
  reject traversal/collisions and preserve sources. Verify timeout/cancellation
  reports actual partial output without a retry or overwrite.
- Exercise session Undo on supported app-managed results, then change a copied
  object externally and verify refusal. Stale group IDs must not undo another
  group. Trash Undo requires a verified backend handle to the original object;
  otherwise report unavailable while preserving ordinary GIO Trash behavior.
- Invoke the separately named Windows Shell menu on one and multiple same-parent
  targets. Verify cancellation, Unicode invocation and installed dynamic or
  owner-drawn handlers; reject mixed-parent inputs explicitly. Automated Unicode
  Copy/Cancel and exact `CF_HDROP` pass on owned fixtures; installed third-party
  handler compatibility remains a separate manual check. External Shell actions
  must not appear as verified app Undo entries.
- Open Permissions on one frozen target and verify the actual Security page.
  Manually test both Apply and Cancel on disposable objects and inspect resulting
  ACLs; invocation acknowledgement proves neither page presence nor an ACL
  change. POSIX octal `chmod` is explicitly unsupported on Windows. The actual
  OS editor handles security policy; do not replace associations or elevate
  privileges just to make a fixture pass.

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
(sumi-explorer-zip-create exact-new-archive fixture-paths 30)
(sumi-explorer-zip-extract exact-new-archive exact-new-directory 30)
(sumi-explorer-undo-history)
(sumi-explorer-undo nil 30)
(sumi-explorer-shell-menu fixture-paths)
(sumi-explorer-permissions single-fixture-path)
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
Windows process cap. The automated driver retains its actual Job Object limits.
Writing or reviewing additional checklist items does not run them.

Before updating status, rerun all four application cases on the corrected
integrated build and record the actual run, source hashes and receipts. Native
Security-page observation and ACL Apply/Cancel, installed third-party Shell
extensions, and comprehensive external DND/clipboard interoperability remain
outside the current automated evidence. macOS runtime behavior also requires its
own validation. None of those manual or platform checks is marked PASS here.
