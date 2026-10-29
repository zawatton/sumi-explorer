param([switch]$Worker, [switch]$PrepareEmacs)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if (-not $IsWindows) { throw 'This harness requires native Windows PowerShell 7.' }
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root
$output = Join-Path $root 'target/windows-native'
New-Item -ItemType Directory -Force $output | Out-Null
$gnuEmacsUrl = 'https://ftp.gnu.org/gnu/emacs/windows/emacs-30/emacs-30.2.zip'
# Published by GNU in emacs-30.2-sha256sum.txt beside the portable archive.
$gnuEmacsArchiveHash = '414d3a1a21147af257ebd98bdd15976fdcb5ed0563f6de89f76d4a4b5dad9c72'
$gnuEmacsExecutableHash = '24848ae901463e3c56dac17031ddd45d58220723aa35102fc778458d17ff2f0b'
$gnuEmacsHome = Join-Path $root 'target/deps/gnu-emacs-30.2'
$gnuEmacsArchive = Join-Path $root 'target/deps/emacs-30.2.zip'
$gnuEmacs = Join-Path $gnuEmacsHome 'bin/emacs.exe'

if ($PrepareEmacs) {
    if ($Worker) { throw 'Dependency preparation must precede the bounded worker.' }
    New-Item -ItemType Directory -Force (Split-Path -Parent $gnuEmacsArchive) | Out-Null
    Invoke-WebRequest -Uri $gnuEmacsUrl -OutFile $gnuEmacsArchive -TimeoutSec 120 -MaximumRedirection 0
    $archiveHash = (Get-FileHash -LiteralPath $gnuEmacsArchive -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($archiveHash -cne $gnuEmacsArchiveHash) { throw 'Official GNU Emacs archive checksum mismatch; extraction refused.' }
    # This directory belongs only to this pinned portable dependency.
    if (Test-Path -LiteralPath $gnuEmacsHome) { Remove-Item -LiteralPath $gnuEmacsHome -Recurse -Force }
    Expand-Archive -LiteralPath $gnuEmacsArchive -DestinationPath $gnuEmacsHome
    $executableHash = (Get-FileHash -LiteralPath $gnuEmacs -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($executableHash -cne $gnuEmacsExecutableHash) { throw 'Extracted GNU Emacs executable checksum mismatch.' }
    @{
        url = $gnuEmacsUrl
        checksum_source = 'https://ftp.gnu.org/gnu/emacs/windows/emacs-30/emacs-30.2-sha256sum.txt'
        archive_sha256 = $archiveHash
        executable_sha256 = $executableHash
        executable = $gnuEmacs
        installation = 'Portable extraction under target/deps; no host installation or global PATH change.'
    } | ConvertTo-Json | Set-Content (Join-Path $output 'gnu-emacs-dependency.json')
    exit 0
}

if (-not $Worker) {
    # Create the worker suspended, assign its Job Object before resuming it,
    # and terminate only this owned job on timeout or controller exit.
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text;
public static class SumiBoundedJob {
    [StructLayout(LayoutKind.Sequential)] struct Basic {
        public long ProcessTime, JobTime;
        public uint Flags;
        public UIntPtr Minimum, Maximum;
        public uint ActiveProcesses;
        public UIntPtr Affinity;
        public uint Priority, Scheduling;
    }
    [StructLayout(LayoutKind.Sequential)] struct Counters {
        public ulong ReadOps, WriteOps, OtherOps, ReadBytes, WriteBytes, OtherBytes;
    }
    [StructLayout(LayoutKind.Sequential)] struct Limits {
        public Basic Basic;
        public Counters IO;
        public UIntPtr ProcessMemory, JobMemory, PeakProcessMemory, PeakJobMemory;
    }
    [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)] struct Startup {
        public uint Size;
        public string Reserved, Desktop, Title;
        public uint X, Y, Width, Height, CharsX, CharsY, Fill, Flags;
        public ushort Show, ReservedSize;
        public IntPtr ReservedBytes, Input, Output, Error;
    }
    [StructLayout(LayoutKind.Sequential)] struct ProcessInfo {
        public IntPtr Process, Thread;
        public uint ProcessId, ThreadId;
    }
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern IntPtr CreateJobObject(IntPtr attributes, string name);
    [DllImport("kernel32.dll", SetLastError=true)]
    static extern bool SetInformationJobObject(IntPtr job, int kind, IntPtr data, uint size);
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern bool CreateProcess(string app, StringBuilder command, IntPtr pa, IntPtr ta,
        bool inherit, uint flags, IntPtr environment, string directory, ref Startup start, out ProcessInfo info);
    [DllImport("kernel32.dll", SetLastError=true)]
    static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
    [DllImport("kernel32.dll", SetLastError=true)] static extern uint ResumeThread(IntPtr thread);
    [DllImport("kernel32.dll", SetLastError=true)] static extern uint WaitForSingleObject(IntPtr handle, uint timeout);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool GetExitCodeProcess(IntPtr process, out uint code);
    [DllImport("kernel32.dll")] static extern bool TerminateJobObject(IntPtr job, uint code);
    [DllImport("kernel32.dll")] static extern bool TerminateProcess(IntPtr process, uint code);
    [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
    public static int Run(string executable, string command, string directory) {
        IntPtr job = CreateJobObject(IntPtr.Zero, null);
        if (job == IntPtr.Zero) throw new Win32Exception();
        ProcessInfo child = new ProcessInfo();
        try {
            Limits limits = new Limits();
            // KILL_ON_JOB_CLOSE, PROCESS_MEMORY and JOB_MEMORY (private commit).
            limits.Basic.Flags = 0x2000 | 0x100 | 0x200;
            limits.ProcessMemory = new UIntPtr(8589934592UL);
            limits.JobMemory = new UIntPtr(8589934592UL);
            int size = Marshal.SizeOf<Limits>();
            IntPtr data = Marshal.AllocHGlobal(size);
            try {
                Marshal.StructureToPtr(limits, data, false);
                if (!SetInformationJobObject(job, 9, data, (uint)size)) throw new Win32Exception();
            } finally { Marshal.FreeHGlobal(data); }
            Startup start = new Startup(); start.Size = (uint)Marshal.SizeOf<Startup>();
            if (!CreateProcess(executable, new StringBuilder(command), IntPtr.Zero, IntPtr.Zero,
                               true, 4, IntPtr.Zero, directory, ref start, out child)) throw new Win32Exception();
            if (!AssignProcessToJobObject(job, child.Process)) {
                int error = Marshal.GetLastWin32Error();
                TerminateProcess(child.Process, 125);
                throw new Win32Exception(error, "Cannot enforce the required Windows Job Object memory cap");
            }
            if (ResumeThread(child.Thread) == 0xffffffff) throw new Win32Exception();
            uint wait = WaitForSingleObject(child.Process, 600000);
            if (wait == 0x102) { TerminateJobObject(job, 124); return 124; }
            if (wait != 0) throw new Win32Exception();
            uint code;
            if (!GetExitCodeProcess(child.Process, out code)) throw new Win32Exception();
            return (int)code;
        } finally {
            // Closing the job also reaps any compiler or GUI descendants.
            CloseHandle(job);
            if (child.Thread != IntPtr.Zero) CloseHandle(child.Thread);
            if (child.Process != IntPtr.Zero) CloseHandle(child.Process);
        }
    }
}
'@
    $memory = @{
        mechanism = 'Windows Job Object, assigned before the worker resumes'
        per_process_private_commit_bytes = 8589934592
        job_private_commit_bytes = 8589934592
        timeout_seconds = 600
        limitation = 'Private committed memory limit; not equivalent to Linux RLIMIT_AS address-space limit.'
    }
    $memory | ConvertTo-Json | Set-Content (Join-Path $output 'memory-limit.json')
    $hostExe = (Get-Process -Id $PID).Path
    $command = '"{0}" -NoLogo -NoProfile -File "{1}" -Worker' -f $hostExe, $PSCommandPath
    try { $code = [SumiBoundedJob]::Run($hostExe, $command, $root) }
    catch {
        $_ | Out-String | Set-Content (Join-Path $output 'controller-error.log')
        throw
    }
    "worker_exit=$code" | Set-Content (Join-Path $output 'controller-result.log')
    exit $code
}

Start-Transcript -Path (Join-Path $output 'worker.log') -Force | Out-Null
try {
    if (-not $env:SUMI_UCRT_BIN) { throw 'Set SUMI_UCRT_BIN to the native UCRT64 bin directory.' }
    $ucrt = (Resolve-Path -LiteralPath $env:SUMI_UCRT_BIN).Path
    $env:Path = "$ucrt;$env:Path"
    $env:CC = Join-Path $ucrt 'gcc.exe'
    $msysEmacs = Join-Path $ucrt 'emacs.exe'
    $emacs = (Resolve-Path -LiteralPath $gnuEmacs).Path
    if ((Get-FileHash -LiteralPath $emacs -Algorithm SHA256).Hash.ToLowerInvariant() -cne $gnuEmacsExecutableHash) {
        throw 'Prepared portable GNU Emacs checksum mismatch.'
    }
    $pkg = Join-Path $ucrt 'pkg-config.exe'
    $env:NELISP_REPO_ROOT = (Resolve-Path -LiteralPath 'target/deps/nelisp').Path
    $env:XDG_CONFIG_HOME = Join-Path $output 'config'
    $env:SUMI_EXPLORER_ROOT = Join-Path $output 'fixtures'
    [Environment]::SetEnvironmentVariable('SUMI_EXPLORER_SESSION', $null, 'Process')
    New-Item -ItemType Directory -Force $env:XDG_CONFIG_HOME, $env:SUMI_EXPLORER_ROOT | Out-Null
    $env:TEMP = $env:SUMI_EXPLORER_ROOT
    $env:TMP = $env:TEMP
    # All native consumers receive native Windows paths, never /ucrt64 or /d/.
    @{
        os = [Environment]::OSVersion.VersionString
        architecture = [Runtime.InteropServices.RuntimeInformation]::OSArchitecture.ToString()
        powershell = $PSVersionTable.PSVersion.ToString()
        ucrt_bin = $ucrt
        compiler = $env:CC
        emacs = $emacs
        msys_emacs = $msysEmacs
        nelisp_root = $env:NELISP_REPO_ROOT
        fixture_root = $env:SUMI_EXPLORER_ROOT
        repository = (& git rev-parse HEAD)
        nelisp_revision = (& git -C $env:NELISP_REPO_ROOT rev-parse HEAD)
    } | ConvertTo-Json | Set-Content (Join-Path $output 'environment.json')
    if ((& git -C $env:NELISP_REPO_ROOT rev-parse HEAD) -ne 'f3551799d210ed32f594383997843f0e1bf25e24') {
        throw 'Unexpected NeLisp compiler revision.'
    }
    & git status --short | Set-Content (Join-Path $output 'source-status.log')
    $sourcePaths = @(
        'explorer/platform.c', 'explorer/platform.h', 'explorer/build.el',
        'explorer/platform-zip.h', 'explorer/platform-zip.inc',
        'explorer/sumi-explorer-native.el', 'explorer/sumi-explorer.el',
        'tests/sumi-explorer-windows-test.el', 'tests/explorer-platform-windows-test.c',
        'tests/explorer-platform-metadata-test.c', 'tests/explorer-platform-zip-test.c',
        'tools/windows-native-check.ps1', '.github/workflows/windows-native.yml', '.gitattributes'
    )
    if ($sourcePaths.Count -ne 14 -or @($sourcePaths | Sort-Object -Unique).Count -ne 14) {
        throw 'Native source manifest must contain exactly 14 distinct paths.'
    }
    foreach ($sourcePath in $sourcePaths) {
        $sourceBytes = [System.IO.File]::ReadAllBytes((Join-Path $root $sourcePath))
        if ([System.Array]::IndexOf($sourceBytes, [byte]13) -ge 0) {
            throw "Bound source contains CR bytes; LF checkout is required: $sourcePath"
        }
    }
    Get-FileHash -LiteralPath $sourcePaths -Algorithm SHA256 |
        Export-Csv (Join-Path $output 'source-sha256.csv') -NoTypeInformation
    & $env:CC --version | Set-Content (Join-Path $output 'gcc-version.log')
    & $env:CC -dumpmachine | Set-Content (Join-Path $output 'gcc-target.log')
    & $pkg --modversion gtk4 libarchive | Set-Content (Join-Path $output 'library-versions.log')
    if ($LASTEXITCODE -ne 0) { throw 'Required native libraries unavailable.' }
    $pacman = Join-Path (Split-Path -Parent (Split-Path -Parent $ucrt)) 'usr/bin/pacman.exe'
    & $pacman -Q | Set-Content (Join-Path $output 'packages.log')
    if ($LASTEXITCODE -ne 0) { throw 'Cannot record MSYS2 package versions.' }
    Get-FileHash -LiteralPath $msysEmacs, $emacs -Algorithm SHA256 |
        Export-Csv (Join-Path $output 'emacs-executables-sha256.csv') -NoTypeInformation
    $unicodeProbe = Join-Path $output 'unicode-file-probe.el'
    @'
;;; unicode-file-probe.el --- Native filename calibration -*- lexical-binding: t; -*-
(require 'json)
(let* ((root (or (getenv "SUMI_UNICODE_PROBE_ROOT") (error "Missing owned probe root")))
       (receipt (or (getenv "SUMI_UNICODE_PROBE_RECEIPT") (error "Missing probe receipt")))
       (directory (expand-file-name "\u65e5\u672c\u8a9e directory" root))
       (file (expand-file-name "\u65e5\u672c\u8a9e file.bin" directory))
       (bytes (unibyte-string 0 65 127 128 255 10))
       (stage "mkdir") made passed failure cleanup-failure)
  (unwind-protect
      (condition-case err
          (progn
            (make-directory directory t)
            (setq made (file-directory-p directory) stage "write-open")
            (with-temp-buffer
              (set-buffer-multibyte nil)
              (insert bytes)
              (let ((coding-system-for-write 'no-conversion))
                (write-region (point-min) (point-max) file nil 'silent)))
            (setq stage "read-open")
            (with-temp-buffer
              (set-buffer-multibyte nil)
              (insert-file-contents-literally file)
              (unless (equal (buffer-string) bytes) (error "Unicode fixture bytes differ")))
            (setq passed t stage "complete"))
        (error (setq failure err)))
    (condition-case err (when made (delete-directory directory t))
      (error (setq cleanup-failure err passed nil))))
  (let ((coding-system-for-write 'utf-8-unix))
    (with-temp-file receipt
      (insert (json-encode
               `((emacs_version . ,emacs-version)
                 (system_type . ,(symbol-name system-type))
                 (configuration . ,system-configuration)
                 (directory_created . ,(if made t :json-false))
                 (passed . ,(if passed t :json-false))
                 (stage . ,stage)
                 (open_failure . ,(if (and (memq (car-safe failure) '(file-error file-missing))
                                          (stringp (cadr failure))
                                          (string-match-p "Opening \\(?:input\\|output\\) file" (cadr failure)))
                                     t :json-false))
                 (error . ,(if failure (prin1-to-string failure) ""))
                 (cleanup_error . ,(if cleanup-failure (prin1-to-string cleanup-failure) "")))))))
  (kill-emacs (if passed 0 1)))
'@ | Set-Content -Encoding utf8 $unicodeProbe
    foreach ($candidate in @(@('msys2', $msysEmacs), @('gnu', $emacs))) {
        $name, $executable = $candidate
        $receipt = Join-Path $output "$name-unicode-probe.json"
        Remove-Item -LiteralPath $receipt -Force -ErrorAction SilentlyContinue
        $env:SUMI_UNICODE_PROBE_ROOT = Join-Path $env:SUMI_EXPLORER_ROOT "$name-unicode-probe"
        $env:SUMI_UNICODE_PROBE_RECEIPT = $receipt
        & $executable -Q --batch -l $unicodeProbe 2>&1 | Set-Content (Join-Path $output "$name-unicode-probe.log")
        $exitCode = $LASTEXITCODE
        $probe = Get-Content -Raw -LiteralPath $receipt | ConvertFrom-Json
        $native = $probe.system_type -ceq 'windows-nt' -and $probe.configuration.Contains('x86_64')
        $positive = $native -and $exitCode -eq 0 -and $probe.directory_created -eq $true -and
            $probe.passed -eq $true -and $probe.stage -ceq 'complete' -and $probe.cleanup_error -ceq ''
        if ($name -ceq 'gnu') {
            if (-not $positive) { throw 'Pinned GNU Emacs failed actual Japanese mkdir/write/read calibration.' }
            'PASS pinned GNU Emacs actual Japanese directory and exact-byte filename write/read' |
                Set-Content (Join-Path $output 'unicode-calibration.log')
        } elseif ($positive) {
            "MSYS2 Emacs $($probe.emacs_version) Unicode probe passed; documented package defect was not reproduced." |
                Set-Content (Join-Path $output 'msys2-unicode-calibration.log')
        } else {
            $knownOpenFailure = $native -and $exitCode -eq 1 -and $probe.directory_created -eq $true -and
                $probe.passed -eq $false -and $probe.open_failure -eq $true -and
                $probe.stage -cin @('write-open', 'read-open') -and $probe.cleanup_error -ceq ''
            if (-not $knownOpenFailure) { throw 'MSYS2 Unicode probe failed outside the calibrated file-open defect; inspect its receipt.' }
            "REPRODUCED MSYS2 Emacs $($probe.emacs_version) Japanese file-open defect: $($probe.error); https://github.com/msys2/MINGW-packages/issues/31733" |
                Set-Content (Join-Path $output 'msys2-unicode-calibration.log')
        }
    }
    [Environment]::SetEnvironmentVariable('SUMI_UNICODE_PROBE_ROOT', $null, 'Process')
    [Environment]::SetEnvironmentVariable('SUMI_UNICODE_PROBE_RECEIPT', $null, 'Process')
    & $emacs -Q --batch --eval '(progn (princ (format "%s\n%s\n%s\n" emacs-version system-type system-configuration)) (unless (and (equal emacs-version "30.2") (eq system-type (quote windows-nt)) (string-match-p "x86_64" system-configuration)) (kill-emacs 1)))' 2>&1 |
        Set-Content (Join-Path $output 'emacs-target.log')
    if ($LASTEXITCODE -ne 0) { throw 'Pinned native x86_64 Windows GNU Emacs 30.2 is required.' }
    & $emacs -Q --batch -l (Join-Path $root 'explorer/build.el') 2>&1 |
        Set-Content (Join-Path $output 'build.log')
    if ($LASTEXITCODE -ne 0) { throw 'Standalone build failed; inspect build.log.' }
    Get-FileHash target/sumi-explorer.exe -Algorithm SHA256 |
        Export-Csv (Join-Path $output 'binary-sha256.csv') -NoTypeInformation
    & (Join-Path $ucrt 'objdump.exe') -p target/sumi-explorer.exe |
        Set-Content (Join-Path $output 'binary-imports.log')

    # Ask the same native Emacs parser used by build.el to split pkg-config
    # flags. This preserves quoted native paths if the runtime has spaces.
    $flagForm = '(progn (require (quote json)) (require (quote subr-x)) (let ((flags nil)) (dolist (option (quote ("--cflags" "--libs"))) (with-temp-buffer (unless (zerop (process-file "pkg-config" nil t nil option "gtk4" "libarchive")) (error "pkg-config failed")) (push (split-string-and-unquote (string-trim (buffer-string))) flags))) (princ (json-encode (vconcat (mapcar (function vconcat) (nreverse flags)))))))'
    $flagJson = & $emacs -Q --batch --eval $flagForm
    if ($LASTEXITCODE -ne 0) { throw 'Cannot parse native compiler flags.' }
    $flags = $flagJson | ConvertFrom-Json -NoEnumerate
    $cflags = @($flags[0]); $libs = @($flags[1])
    $flags | ConvertTo-Json -Depth 4 | Set-Content (Join-Path $output 'compiler-flags.json')
    function Build-NativeTest([string]$name, [string]$include) {
        $binary = Join-Path $output "$name.exe"
        $arguments = @('-std=c11', '-Wall', '-Wextra', '-Werror', "-I$include", '-Iexplorer') +
            $cflags + @('tests/explorer-platform-windows-test.c', '-o', $binary) +
            $libs + @('-lshell32', '-lole32', '-luuid', '-luser32', '-ladvapi32')
        & $env:CC @arguments 2>&1 | Set-Content (Join-Path $output "$name-build.log")
        if ($LASTEXITCODE -ne 0) { throw "Native test compilation failed: $name" }
        return $binary
    }
    $test = Build-NativeTest 'platform-test' (Join-Path $root 'explorer')
    & $test 2>&1 | Tee-Object -FilePath (Join-Path $output 'platform-test.log')
    if ($LASTEXITCODE -ne 0) { throw 'Native platform runtime tests failed.' }

    # Generate behavioral negative controls outside the tracked tree. Each
    # executable contains the actual adapter with exactly one known defect.
    $source = Get-Content -LiteralPath explorer/platform.c -Raw
    $mutations = @(
        @('broken-forwarding', 'return (message == WM_DRAWITEM || message == WM_MEASUREITEM) ? TRUE : 0;', 'return 0;'),
        @('broken-error-success', 'if (validate(paths, count, error) < 0) return -1;', 'if (validate(paths, count, error) < 0) return 1;')
    )
    foreach ($mutation in $mutations) {
        $name, $before, $after = $mutation
        if (-not $source.Contains($before)) { throw "Negative control mutation no longer applies: $name" }
        $directory = Join-Path $output $name
        New-Item -ItemType Directory -Force $directory | Out-Null
        $source.Replace($before, $after) | Set-Content -LiteralPath (Join-Path $directory 'platform.c') -Encoding utf8
        $mutant = Build-NativeTest $name $directory
        & $mutant --unit-only 2>&1 | Set-Content (Join-Path $output "$name.log")
        $exitCode = $LASTEXITCODE
        $text = Get-Content -Raw (Join-Path $output "$name.log")
        $expected = if ($name -eq 'broken-forwarding') { 'FAIL IContextMenu2 WM_DRAWITEM handled return' }
                    else { 'FAIL relative path rejected' }
        if ($exitCode -ne 1 -or -not $text.Contains($expected)) {
            throw "Negative control did not fail its actual behavior assertion: $name exit=$exitCode"
        }
        "PASS negative control rejected $name" | Add-Content (Join-Path $output 'negative-controls.log')
    }

    $before = 'invoke.hwnd = parent;'
    if (-not $source.Contains($before)) { throw 'Owner lifetime negative control no longer applies.' }
    $directory = Join-Path $output 'broken-owner-lifetime'
    New-Item -ItemType Directory -Force $directory | Out-Null
    $source.Replace($before, 'invoke.hwnd = owner;') |
        Set-Content -LiteralPath (Join-Path $directory 'platform.c') -Encoding utf8
    $mutant = Build-NativeTest 'broken-owner-lifetime' $directory
    & $mutant 2>&1 | Set-Content (Join-Path $output 'broken-owner-lifetime.log')
    $exitCode = $LASTEXITCODE
    $text = Get-Content -Raw (Join-Path $output 'broken-owner-lifetime.log')
    if ($exitCode -ne 1 -or -not $text.Contains('FAIL Shell verb uses persistent GTK caller ownership') -or
        -not $text.Contains('FAIL modeless verb window survives menu-owner cleanup')) {
        throw "Owner lifetime negative control did not fail both behavior assertions: exit=$exitCode"
    }
    'PASS negative control rejected broken-owner-lifetime' | Add-Content (Join-Path $output 'negative-controls.log')

    $before = 'char *native = g_file_get_path(file);'
    if ([regex]::Matches($source, [regex]::Escape($before)).Count -ne 1) {
        throw 'Native path negative control requires exactly one shared conversion site.'
    }
    $directory = Join-Path $output 'broken-native-path'
    New-Item -ItemType Directory -Force $directory | Out-Null
    $source.Replace($before, 'char *native = g_strdup(path);') |
        Set-Content -LiteralPath (Join-Path $directory 'platform.c') -Encoding utf8
    $mutant = Build-NativeTest 'broken-native-path' $directory
    & $mutant 2>&1 | Set-Content (Join-Path $output 'broken-native-path.log')
    $exitCode = $LASTEXITCODE
    $text = Get-Content -Raw (Join-Path $output 'broken-native-path.log')
    $required = @(
        'PASS real COM multi-selection menu cancellation',
        'PASS real COM Copy menu invocation',
        'PASS Copy clipboard CF_HDROP contains both frozen Unicode filesystem targets',
        'FAIL real COM forward-slash Unicode menu cancellation',
        'FAIL real COM forward-slash Unicode Copy invocation',
        'FAIL forward-slash Copy CF_HDROP contains both original Unicode filesystem targets'
    )
    if ($exitCode -ne 1 -or @($required | Where-Object { -not $text.Contains($_) }).Count) {
        throw "Native path control did not preserve native passes and fail all forward-path behaviors: exit=$exitCode"
    }
    'PASS negative control rejected broken-native-path' | Add-Content (Join-Path $output 'negative-controls.log')

    function Build-MetadataTest([string]$name, [string[]]$extra) {
        $binary = Join-Path $output "$name.exe"
        $arguments = @('-std=c11', '-Wall', '-Wextra', '-Werror', '-Iexplorer') + $cflags + $extra +
            @('tests/explorer-platform-metadata-test.c', '-o', $binary) +
            $libs + @('-lshell32', '-lole32', '-luuid', '-luser32', '-ladvapi32')
        & $env:CC @arguments 2>&1 | Set-Content (Join-Path $output "$name-build.log")
        if ($LASTEXITCODE -ne 0) { throw "Metadata test compilation failed: $name" }
        return $binary
    }
    $metadata = Build-MetadataTest 'metadata-test' @()
    $runtimeFailures = [System.Collections.Generic.List[string]]::new()
    $metadataPassed = $true
    & $metadata --handle-probe 2>&1 | Set-Content (Join-Path $output 'metadata-handle-probe.log')
    if ($LASTEXITCODE -ne 0) {
        $metadataPassed = $false
        $runtimeFailures.Add('Bounded own-process handle diagnostics failed; inspect metadata-handle-probe.log.')
    }
    & $metadata 2>&1 | Set-Content (Join-Path $output 'metadata-test.log')
    if ($LASTEXITCODE -ne 0) {
        $metadataPassed = $false
        $runtimeFailures.Add('Actual Windows NTFS metadata acceptance failed; inspect metadata-test.log.')
    }
    $controls = @(
        @('metadata-omit-change-time', '-DSUMI_TEST_OMIT_CHANGE_TIME',
          'PASS DACL control calibrated: all serialized fields except ChangeTime equal',
          'FAIL DACL-only modification changes stamp'),
        @('metadata-omit-file-id', '-DSUMI_TEST_OMIT_FILE_ID',
          'PASS identity control calibrated: all fields except file ID equal',
          'FAIL same-byte replacement changes file identity stamp')
    )
    foreach ($control in $controls) {
        $name, $define, $calibrated, $expected = $control
        $mutant = Build-MetadataTest $name @($define)
        & $mutant 2>&1 | Set-Content (Join-Path $output "$name.log")
        $exitCode = $LASTEXITCODE
        $text = Get-Content -Raw (Join-Path $output "$name.log")
        if ($exitCode -ne 1 -or -not $text.Contains($calibrated) -or -not $text.Contains($expected)) {
            $metadataPassed = $false
            $runtimeFailures.Add("Metadata omission control did not fail its calibrated behavior assertion: $name exit=$exitCode")
        } else {
            "PASS negative control rejected $name" | Add-Content (Join-Path $output 'negative-controls.log')
        }
    }

    $zip = Join-Path $output 'platform-zip-test.exe'
    $zipArguments = @('-std=c11', '-Wall', '-Wextra', '-Werror', '-Iexplorer') + $cflags +
        @('tests/explorer-platform-zip-test.c', '-o', $zip) +
        $libs + @('-lshell32', '-lole32', '-luuid', '-luser32', '-ladvapi32')
    & $env:CC @zipArguments 2>&1 | Set-Content (Join-Path $output 'platform-zip-build.log')
    if ($LASTEXITCODE -ne 0) { throw 'Native ZIP OS I/O test compilation failed.' }
    & $zip 2>&1 | Set-Content (Join-Path $output 'platform-zip-test.log')
    $zipExit = $LASTEXITCODE
    $zipText = Get-Content -Raw (Join-Path $output 'platform-zip-test.log')
    $zipPassed = $zipExit -eq 0 -and $zipText -match 'RESULT Windows ZIP checks=[1-9][0-9]* failures=0 '
    if (-not $zipPassed) { $runtimeFailures.Add("Native Windows ZIP OS I/O acceptance failed: exit=$zipExit") }
    & $zip --omit-file-id 2>&1 | Set-Content (Join-Path $output 'platform-zip-omit-file-id.log')
    $exitCode = $LASTEXITCODE
    $zipControl = Get-Content -Raw (Join-Path $output 'platform-zip-omit-file-id.log')
    if ($exitCode -ne 1 -or
        -not $zipControl.Contains('PASS replacement control calibrated: all etag fields except native file ID equal') -or
        -not $zipControl.Contains('FAIL same-byte equal-time replacement changes native ZIP file identity')) {
        $runtimeFailures.Add("Native ZIP file-ID omission did not fail its calibrated actual replacement assertion: exit=$exitCode")
        $zipPassed = $false
    } else {
        'PASS negative control rejected zip-omit-file-id' | Add-Content (Join-Path $output 'negative-controls.log')
    }

    # Each named acceptance test starts a fresh native Emacs and owns a fresh
    # host/session through the test fixture. Only an actual ERT pass counts.
    $env:SUMI_EXPLORER_TEST_BIN = (Resolve-Path -LiteralPath 'target/sumi-explorer.exe').Path
    $ertTests = @(
        'sumi-explorer-windows-zip-and-top-undo',
        'sumi-explorer-windows-copy-undo-and-replacement-refusal',
        'sumi-explorer-windows-readonly-refusal-and-chmod-rejection',
        'sumi-explorer-windows-native-shell-menu-cancel'
    )
    $ertRunner = Join-Path $output 'run-one-windows-ert.el'
    @'
;;; run-one-windows-ert.el --- Exact native acceptance result -*- lexical-binding: t; -*-
(require 'ert)
(require 'json)
(let* ((name (intern (or (getenv "SUMI_WINDOWS_ERT_NAME") (error "Missing test name"))))
       (receipt (or (getenv "SUMI_WINDOWS_ERT_RECEIPT") (error "Missing receipt path")))
       (selector (list 'member name))
       (selected (mapcar #'ert-test-name (ert-select-tests selector t))))
  (unless (and (eq system-type 'windows-nt) (equal selected (list name)))
    (error "Expected exactly one named Windows test: %S" selected))
  (ert-run-tests-batch selector)
  (let* ((result (ert-test-most-recent-result (ert-get-test name)))
         (passed (ert-test-passed-p result))
         (coding-system-for-write 'utf-8-unix))
    (with-temp-file receipt
      (insert (json-encode `((name . ,(symbol-name name))
                             (selected . 1)
                             (passed . ,(if passed 1 0))
                             (result_type . ,(symbol-name (type-of result)))
                             (system_type . ,(symbol-name system-type))
                             (emacs_version . ,emacs-version)))))
    (kill-emacs (if passed 0 1))))
'@ | Set-Content -Encoding utf8 $ertRunner
    $ertResults = @()
    foreach ($name in $ertTests) {
        $log = Join-Path $output "$name.log"
        $receipt = Join-Path $output "$name.json"
        Remove-Item -LiteralPath $receipt -Force -ErrorAction SilentlyContinue
        $env:SUMI_WINDOWS_ERT_NAME = $name
        $env:SUMI_WINDOWS_ERT_RECEIPT = $receipt
        & $emacs -Q --batch -L (Join-Path $root 'explorer') `
            -l (Join-Path $root 'tests/sumi-explorer-windows-test.el') -l $ertRunner 2>&1 |
            Set-Content $log
        $exitCode = $LASTEXITCODE
        $passed = $false
        try {
            $result = Get-Content -Raw -LiteralPath $receipt | ConvertFrom-Json
            $passed = $exitCode -eq 0 -and $result.name -ceq $name -and
                $result.selected -eq 1 -and $result.passed -eq 1 -and
                $result.result_type -ceq 'ert-test-passed' -and $result.system_type -ceq 'windows-nt'
        } catch {
            $_ | Out-String | Add-Content $log
        }
        $ertResults += [pscustomobject]@{ name = $name; exit = $exitCode; passed = $passed }
        if (-not $passed) { $runtimeFailures.Add("Native Windows ERT did not pass: $name exit=$exitCode") }
    }
    $ertResults | ConvertTo-Json -Depth 3 | Set-Content (Join-Path $output 'windows-ert-results.json')
    $ertPassed = @($ertResults | Where-Object { $_.passed }).Count
    "expected=4 passed=$ertPassed failed=$($ertTests.Count - $ertPassed) skipped_accepted=0" |
        Set-Content (Join-Path $output 'windows-ert-summary.log')
    [Environment]::SetEnvironmentVariable('SUMI_WINDOWS_ERT_NAME', $null, 'Process')
    [Environment]::SetEnvironmentVariable('SUMI_WINDOWS_ERT_RECEIPT', $null, 'Process')

    # A console-subsystem app can have a console as MainWindowHandle. Select
    # GTK's actual PID-owned visible top-level window and close that HWND.
    Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
public static class SumiOwnedWindows {
    public sealed class Window {
        public long Handle;
        public string ClassName, Title;
        public bool Visible;
    }
    delegate bool Visitor(IntPtr window, IntPtr argument);
    [DllImport("user32.dll")] static extern bool EnumWindows(Visitor callback, IntPtr argument);
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr window, out uint process);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetClassName(IntPtr window, StringBuilder text, int capacity);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetWindowText(IntPtr window, StringBuilder text, int capacity);
    [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr window);
    [DllImport("user32.dll", SetLastError=true)] static extern bool PostMessage(IntPtr window, uint message, UIntPtr w, IntPtr l);
    public static Window[] Snapshot(uint process) {
        var windows = new List<Window>();
        EnumWindows((handle, argument) => {
            uint owner; GetWindowThreadProcessId(handle, out owner);
            if (owner == process) {
                var name = new StringBuilder(256); var title = new StringBuilder(1024);
                GetClassName(handle, name, name.Capacity); GetWindowText(handle, title, title.Capacity);
                windows.Add(new Window { Handle=handle.ToInt64(), ClassName=name.ToString(),
                    Title=title.ToString(), Visible=IsWindowVisible(handle) });
            }
            return true;
        }, IntPtr.Zero);
        return windows.ToArray();
    }
    public static bool Close(uint process, long handle) {
        foreach (var window in Snapshot(process))
            if (window.Handle == handle && window.Visible && window.ClassName == "gdkSurfaceToplevel")
                return PostMessage(new IntPtr(handle), 0x0010, UIntPtr.Zero, IntPtr.Zero);
        return false;
    }
}
'@
    $app = Start-Process -FilePath (Join-Path $root 'target/sumi-explorer.exe') -PassThru `
        -RedirectStandardOutput (Join-Path $output 'app-stdout.log') `
        -RedirectStandardError (Join-Path $output 'app-stderr.log')
    try {
        $deadline = [DateTime]::UtcNow.AddSeconds(15)
        $gtk = @()
        do {
            Start-Sleep -Milliseconds 100
            $app.Refresh()
            $windows = @([SumiOwnedWindows]::Snapshot($app.Id))
            $gtk = @($windows | Where-Object { $_.Visible -and $_.ClassName -eq 'gdkSurfaceToplevel' })
        } while (-not $app.HasExited -and $gtk.Count -eq 0 -and [DateTime]::UtcNow -lt $deadline)
        $windows | ConvertTo-Json -Depth 3 | Set-Content (Join-Path $output 'app-owned-windows.json')
        if ($app.HasExited -or $gtk.Count -ne 1) { throw "Expected one standalone GTK window; observed $($gtk.Count)." }
        "PASS standalone native GTK window pid=$($app.Id) handle=$($gtk[0].Handle) class=$($gtk[0].ClassName)" |
            Set-Content (Join-Path $output 'app-window.log')
        if (-not [SumiOwnedWindows]::Close($app.Id, $gtk[0].Handle) -or -not $app.WaitForExit(10000)) {
            throw 'App did not close its owned GTK window normally.'
        }
        "app_exit=$($app.ExitCode)" | Add-Content (Join-Path $output 'app-window.log')
        if ($app.ExitCode -ne 0) { throw "App closed with exit=$($app.ExitCode)" }
    } catch {
        $_ | Out-String | Set-Content (Join-Path $output 'app-error.log')
        $runtimeFailures.Add("Standalone GTK window smoke failed: $_")
    } finally {
        if (-not $app.HasExited) { $app.Kill($true); $app.WaitForExit() }
        $app.Dispose()
    }
    $summary = @(
        'PASS Windows native build, Shell ABI runtime tests and Shell behavioral negative controls'
        "Windows ERT expected=4 passed=$ertPassed failed=$($ertTests.Count - $ertPassed); only actual passes accepted"
        'NOTRUN Security page visual observation/cancellation, ACL Apply, third-party extension compatibility'
        'Shell invocation acknowledgement is not ACL Apply or completion evidence for arbitrary external verbs.'
    )
    if ($metadataPassed) {
        $summary += 'PASS local NTFS metadata acceptance, injected failures, and calibrated ChangeTime/file-identity omission controls'
    }
    if ($zipPassed) { $summary += 'PASS native ZIP OS I/O, owned cleanup, provider calibration and file-ID omission control' }
    $summary += @($runtimeFailures | ForEach-Object { "FAIL $_" })
    $summary | Set-Content (Join-Path $output 'result.log')
    if ($runtimeFailures.Count) { throw "$($runtimeFailures.Count) runtime gates failed; inspect result.log and individual logs." }
} finally {
    Stop-Transcript | Out-Null
}
