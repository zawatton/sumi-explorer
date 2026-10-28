param([switch]$Worker)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if (-not $IsWindows) { throw 'This harness requires native Windows PowerShell 7.' }
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root
$output = Join-Path $root 'target/windows-native'
New-Item -ItemType Directory -Force $output | Out-Null

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
    $emacs = Join-Path $ucrt 'emacs.exe'
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
        nelisp_root = $env:NELISP_REPO_ROOT
        fixture_root = $env:SUMI_EXPLORER_ROOT
        repository = (& git rev-parse HEAD)
        nelisp_revision = (& git -C $env:NELISP_REPO_ROOT rev-parse HEAD)
    } | ConvertTo-Json | Set-Content (Join-Path $output 'environment.json')
    if ((& git -C $env:NELISP_REPO_ROOT rev-parse HEAD) -ne 'f3551799d210ed32f594383997843f0e1bf25e24') {
        throw 'Unexpected NeLisp compiler revision.'
    }
    & git status --short | Set-Content (Join-Path $output 'source-status.log')
    Get-FileHash explorer/platform.c, explorer/platform.h, explorer/build.el,
        explorer/sumi-explorer-native.el, tests/explorer-platform-windows-test.c,
        tests/explorer-platform-metadata-test.c,
        tools/windows-native-check.ps1 -Algorithm SHA256 |
        Export-Csv (Join-Path $output 'source-sha256.csv') -NoTypeInformation
    & $env:CC --version | Set-Content (Join-Path $output 'gcc-version.log')
    & $env:CC -dumpmachine | Set-Content (Join-Path $output 'gcc-target.log')
    & $pkg --modversion gtk4 libarchive | Set-Content (Join-Path $output 'library-versions.log')
    if ($LASTEXITCODE -ne 0) { throw 'Required native libraries unavailable.' }
    $pacman = Join-Path (Split-Path -Parent (Split-Path -Parent $ucrt)) 'usr/bin/pacman.exe'
    & $pacman -Q | Set-Content (Join-Path $output 'packages.log')
    if ($LASTEXITCODE -ne 0) { throw 'Cannot record MSYS2 package versions.' }
    & $emacs -Q --batch --eval '(progn (princ (format "%s\n%s\n%s\n" emacs-version system-type system-configuration)) (unless (and (eq system-type (quote windows-nt)) (string-match-p "x86_64" system-configuration)) (kill-emacs 1)))' 2>&1 |
        Set-Content (Join-Path $output 'emacs-target.log')
    if ($LASTEXITCODE -ne 0) { throw 'A native x86_64 Windows GNU Emacs is required.' }
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
            $libs + @('-lshell32', '-lole32', '-luuid', '-luser32')
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
    & $metadata --handle-probe 2>&1 | Set-Content (Join-Path $output 'metadata-handle-probe.log')
    if ($LASTEXITCODE -ne 0) { throw 'Bounded own-process handle diagnostics failed; inspect metadata-handle-probe.log.' }
    & $metadata 2>&1 | Set-Content (Join-Path $output 'metadata-test.log')
    if ($LASTEXITCODE -ne 0) { throw 'Actual Windows NTFS metadata acceptance failed; inspect metadata-test.log.' }
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
            throw "Metadata omission control did not fail its calibrated behavior assertion: $name exit=$exitCode"
        }
        "PASS negative control rejected $name" | Add-Content (Join-Path $output 'negative-controls.log')
    }

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
    } finally {
        if (-not $app.HasExited) { $app.Kill($true); $app.WaitForExit() }
        $app.Dispose()
    }
    @(
        'PASS Windows native build, ABI runtime tests, behavioral negative controls and GTK window smoke'
        'PASS local NTFS metadata acceptance, injected failures, and calibrated ChangeTime/file-identity omission controls'
        'NOTRUN Security page visual observation/cancellation, ACL Apply, third-party extension compatibility'
        'NOTRUN Windows ZIP/Undo end-to-end acceptance; these require their own protocol/GUI tests'
        'Shell invocation acknowledgement is not ACL Apply or completion evidence for arbitrary external verbs.'
    ) | Set-Content (Join-Path $output 'result.log')
} finally {
    Stop-Transcript | Out-Null
}
