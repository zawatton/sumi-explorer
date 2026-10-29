;;; sumi-explorer-windows-test.el --- Real Windows acceptance -*- lexical-binding: t; -*-

;; Load explicitly, then invoke ert-run-tests-batch-and-exit.  Loading this file
;; never starts tests.  Only owned-menu cancellation is automated; Security UI
;; remains a manual check.
(require 'ert)
(require 'json)
(require 'sumi-explorer)

(defvar sumi-explorer-windows-test--fixture-root nil)
(defvar sumi-explorer-windows-test--native-buffer nil)
(defconst sumi-explorer-windows-test--source-coding
  (list buffer-file-coding-system enable-multibyte-characters coding-system-for-read)
  "Source loading coding/buffer state for dependency diagnostics.")

(defun sumi-explorer-windows-test--name-diagnostic (name)
  "Describe NAME without emitting non-ASCII characters; cap at 256 characters."
  (let ((part (substring name 0 (min 256 (length name)))))
    (list :multibyte (multibyte-string-p name) :length (length name)
          :codepoints (mapconcat (lambda (c) (format "%X" c)) part ",")
          :utf8hex (mapconcat (lambda (c) (format "%02X" c))
                             (encode-coding-string part 'utf-8-unix) ""))))

(defun sumi-explorer-windows-test--diagnostic (phase path)
  "Emit bounded ASCII-safe expected-name and directory data for PHASE/PATH."
  (condition-case failure
      (progn
        (message "DIAG %s source-coding=%S coding=%S expected=%S exists=%S attributes=%S"
                 phase sumi-explorer-windows-test--source-coding
                 (mapcar (lambda (symbol) (cons symbol (and (boundp symbol) (symbol-value symbol))))
                         '(coding-system-for-write coding-system-for-read file-name-coding-system
                           default-file-name-coding-system locale-coding-system w32-unicode-filenames))
                 (sumi-explorer-windows-test--name-diagnostic path)
                 (file-exists-p path) (file-attributes path))
        (dolist (directory (delete-dups (list sumi-explorer-windows-test--fixture-root
                                            (file-name-directory path))))
          (when (and directory
                     (or (equal directory sumi-explorer-windows-test--fixture-root)
                         (file-in-directory-p directory sumi-explorer-windows-test--fixture-root)))
            (message "DIAG directory=%S entries=%S"
                     (sumi-explorer-windows-test--name-diagnostic directory)
                     (mapcar #'sumi-explorer-windows-test--name-diagnostic
                             (directory-files directory nil directory-files-no-dot-files-regexp t 16))))))
    (error (message "DIAG inspection-error=%S"
                    (sumi-explorer-windows-test--name-diagnostic (error-message-string failure))))))

(defun sumi-explorer-windows-test--dotnet-diagnostic ()
  "Enumerate only the ASCII fixture root through one bounded owned child."
  (let* ((root sumi-explorer-windows-test--fixture-root)
         (powershell (or (executable-find "powershell.exe") (executable-find "pwsh.exe")))
         (script (expand-file-name "fixture-name-diagnostic.ps1" root))
         (output (generate-new-buffer " *sumi-fixture-names*"))
         child)
    (unwind-protect
        (when (and powershell (cl-every (lambda (c) (< c 128)) root))
          (with-temp-file script
            (insert "$ErrorActionPreference='Stop'\ntry {\n"
                    "$entries=[IO.Directory]::GetFileSystemEntries($env:SUMI_WINDOWS_DIAGNOSTIC_ROOT)\n"
                    "foreach($entry in ($entries | Select-Object -First 16)) {\n"
                    "$name=[IO.Path]::GetFileName($entry)\n"
                    "$hex=($name.ToCharArray() | ForEach-Object { '{0:X4}' -f [int]$_ }) -join ','\n"
                    "[Console]::WriteLine('DIAG NET utf16='+$hex+' attributes='+[IO.File]::GetAttributes($entry))\n"
                    "}\n} catch { [Console]::WriteLine('DIAG NET error-type='+$_.Exception.GetType().FullName); exit 1 }\n"))
          (let ((process-environment (copy-sequence process-environment))
                (deadline (+ (float-time) 5)))
            (setenv "SUMI_WINDOWS_DIAGNOSTIC_ROOT" root)
            (setq child (make-process :name "sumi-fixture-names" :buffer output :noquery t
                                      :command (list powershell "-NoProfile" "-NonInteractive"
                                                     "-ExecutionPolicy" "Bypass" "-File" script)))
            (while (and (process-live-p child) (< (float-time) deadline))
              (accept-process-output child 0.02))
            (message "DIAG NET alive=%S exit=%S output=%s" (process-live-p child)
                     (process-exit-status child) (with-current-buffer output (buffer-string)))))
      (when (and child (process-live-p child)) (delete-process child))
      (kill-buffer output))))

(defun sumi-explorer-windows-test--bytes (path)
  "Read exact fixture bytes from PATH."
  (with-temp-buffer
    (set-buffer-multibyte nil)
    (insert-file-contents-literally path)
    (buffer-string)))

(defun sumi-explorer-windows-test--write (path bytes)
  "Write exact BYTES to the disposable PATH."
  (let ((coding-system-for-write 'no-conversion))
    (condition-case failure
        (progn
          (message "DIAG payload-multibyte=%S" (multibyte-string-p bytes))
          (write-region bytes nil path nil 'silent)
          (sumi-explorer-windows-test--diagnostic "after-write" path))
      (error
       (sumi-explorer-windows-test--diagnostic "write-failure" path)
       (signal (car failure) (cdr failure))))))

(defun sumi-explorer-windows-test--top-id (operation)
  "Require newest Undo group to describe OPERATION and return its ID."
  (let ((group (car (cdr (assoc "groups" (sumi-explorer-undo-history))))))
    (should (equal (cdr (assoc "operation" group)) operation))
    (should (cdr (assoc "id" group)))
    (cdr (assoc "id" group))))

(defun sumi-explorer-windows-test--native-output-diagnostic ()
  "Report only the owned host's final output on failure, within 8192 ASCII bytes."
  (condition-case nil
      (let* ((process sumi-explorer--owned-process)
             (buffer (and (processp process) (process-buffer process))))
        (if (and (eq process sumi-explorer--process)
                 (buffer-live-p buffer)
                 (eq buffer sumi-explorer-windows-test--native-buffer))
            (with-current-buffer buffer
              (let* ((tail (buffer-substring-no-properties (max (point-min) (- (point-max) 8192)) (point-max)))
                     (encoded (mapconcat
                               (lambda (character)
                                 (cond ((= character ?\n) "\\n")
                                       ((= character ?\r) "\\r")
                                       ((= character ?\t) "\\t")
                                       ((<= 32 character 126) (char-to-string character))
                                       (t (format "\\u{%X}" character)))) tail "")))
                ;; Escaping may expand Unicode; retain the ending within the
                ;; byte budget rather than emitting a larger diagnostic.
                (message "DIAG owned-native-tail escaped source-chars=%d: %s" (length tail)
                         (substring encoded (max 0 (- (length encoded) 8000))))))
          (message "DIAG owned-native-tail unavailable: no live test-owned output buffer")))
    (error (message "DIAG owned-native-tail unavailable: inspection failed"))))

(defmacro sumi-explorer-windows-test--session (&rest body)
  "Run BODY with one real Windows host and disposable ROOT directory."
  (declare (indent 0) (debug t))
  `(progn
     (should (eq system-type 'windows-nt))
     (let* ((binary (getenv "SUMI_EXPLORER_TEST_BIN"))
            (root (make-temp-file "sumi-windows-acceptance-" t))
            (sumi-explorer-windows-test--fixture-root root)
            (sumi-explorer-windows-test--native-buffer nil)
            (process-environment (copy-sequence process-environment))
            (sumi-explorer--session-directory nil)
            (sumi-explorer--owned-session nil)
            (sumi-explorer--process nil)
            (sumi-explorer--owned-process nil)
            (sumi-explorer--request-id 0)
            (sumi-explorer--pending nil)
            (sumi-explorer--quit-requested nil)
            (sumi-explorer--stop-timeout 15))
       (unwind-protect
           (condition-case failure
               (progn
             (should (and (stringp binary) (file-executable-p binary)))
             (dolist (name '("config" "data"))
               (make-directory (expand-file-name name root)))
             (sumi-explorer-windows-test--diagnostic "after-create-directories" root)
             (setenv "XDG_CONFIG_HOME" (expand-file-name "config" root))
             (setenv "XDG_DATA_HOME" (expand-file-name "data" root))
             (setenv "SUMI_EXPLORER_SESSION" nil)
             (sumi-explorer-start root binary)
             (setq sumi-explorer-windows-test--native-buffer
                   (generate-new-buffer " *sumi-windows-native-output*"))
             (set-process-buffer sumi-explorer--process sumi-explorer-windows-test--native-buffer)
             (should (process-live-p sumi-explorer--process))
             (should (file-equal-p root (cdr (assoc "directory" (sumi-explorer-state)))))
             ,@body
             (should-not sumi-explorer--pending)
             (let ((owned-process sumi-explorer--process)
                   (owned-session sumi-explorer--session-directory))
               (sumi-explorer-stop)
               (should-not (process-live-p owned-process))
               (should-not (file-exists-p owned-session))
               (should-not sumi-explorer--session-directory)))
             ((error quit)
              (let ((inhibit-quit t))
                (sumi-explorer-windows-test--native-output-diagnostic)
                (sumi-explorer-windows-test--diagnostic "before-cleanup" root)
                (condition-case nil (sumi-explorer-windows-test--dotnet-diagnostic) (error nil)))
              (signal (car failure) (cdr failure))))
         ;; Forced disposal is only failure cleanup for this owned test host.
         (sumi-explorer-stop t)
         (when (buffer-live-p sumi-explorer-windows-test--native-buffer)
           (kill-buffer sumi-explorer-windows-test--native-buffer))
         (delete-directory root t)))))

(defun sumi-explorer-windows-test--large-payload ()
  "Make 131104 binary bytes with unique counter-prefixed 32-byte blocks."
  (let (blocks)
    (dotimes (index 4097)
      (push (concat (unibyte-string (logand (ash index -24) 255)
                                   (logand (ash index -16) 255)
                                   (logand (ash index -8) 255) (logand index 255))
                    (substring (secure-hash 'sha256 (format "ZIP fixture block %d" index) nil nil t) 0 28))
            blocks))
    (apply #'concat (nreverse blocks))))

(defun sumi-explorer-windows-test--junction (action alias source)
  "Create/remove only owned ALIAS to SOURCE using bounded Windows ACTION.
Removal uses nonrecursive Directory.Delete, never traversing the junction."
  (let* ((powershell (or (executable-find "pwsh.exe") (executable-find "powershell.exe")))
         (script (expand-file-name "owned-junction.ps1" sumi-explorer-windows-test--fixture-root))
         (data-file (expand-file-name "owned-junction.json" sumi-explorer-windows-test--fixture-root))
         (phase-file (concat data-file ".phase"))
         (output (generate-new-buffer " *sumi-owned-junction*"))
         child)
    (unwind-protect
        (progn
          (unless powershell (error "PowerShell is required for the real junction fixture"))
          (unless (cl-every (lambda (character) (< character 128)) (concat script data-file))
            (error "Junction helper transport requires an ASCII fixture container"))
          (let ((coding-system-for-write 'utf-8-unix))
            (with-temp-file data-file
              (insert (json-encode `((action . ,action) (alias . ,alias) (source . ,source))))))
          (when (file-exists-p phase-file) (delete-file phase-file))
          (with-temp-file script
            (insert "param([string]$DataPath)\n"
                    "[IO.File]::WriteAllText($DataPath+'.phase','entered')\n"
                    "$ErrorActionPreference='Stop'\n"
                    "if(([IO.FileInfo]$DataPath).Length -gt 65536) { throw 'Oversized junction data' }\n"
                    "$utf8=[Text.UTF8Encoding]::new($false,$true)\n"
                    "[IO.File]::WriteAllText($DataPath+'.phase','before-json')\n"
                    "$data=ConvertFrom-Json -InputObject ([IO.File]::ReadAllText($DataPath,$utf8))\n"
                    "[IO.File]::WriteAllText($DataPath+'.phase','json-read')\n"
                    "$Action=[string]$data.action; $AliasDirectory=[string]$data.alias; $SourceDirectory=[string]$data.source\n"
                    "if($Action -notin @('Create','Remove')) { throw 'Invalid junction action' }\n"
                    "foreach($field in @('alias','source')) {\n"
                    "$hex=([string]$data.$field).ToCharArray() | Select-Object -First 256 | ForEach-Object { '{0:X4}' -f [int]$_ }\n"
                    "[Console]::WriteLine('DIAG junction '+$field+'_utf16='+($hex -join ','))\n}\n"
                    "if($Action -eq 'Create') {\n"
                    "[IO.File]::WriteAllText($DataPath+'.phase','before-create')\n"
                    "New-Item -ItemType Junction -Path $AliasDirectory -Target $SourceDirectory | Out-Null\n"
                    "[IO.File]::WriteAllText($DataPath+'.phase','after-create')\n}\n"
                    "[IO.File]::WriteAllText($DataPath+'.phase','before-getitem')\n"
                    "$item=Get-Item -LiteralPath $AliasDirectory -Force\n"
                    "[IO.File]::WriteAllText($DataPath+'.phase','after-getitem')\n"
                    "if($item.LinkType -ne 'Junction') { throw 'Owned alias is not a junction' }\n"
                    "if($Action -eq 'Remove') {\n"
                    "[IO.File]::WriteAllText($DataPath+'.phase','before-remove')\n"
                    "[IO.Directory]::Delete($AliasDirectory,$false)\n"
                    "[IO.File]::WriteAllText($DataPath+'.phase','after-remove')\n}\n"
                    "[IO.File]::WriteAllText($DataPath+'.phase','completed')\n"
                    "[Console]::WriteLine('Owned junction action completed: '+$Action)\n"))
          (setq child (make-process :name "sumi-owned-junction" :buffer output :noquery t
                                    :command (list powershell "-NoProfile" "-NonInteractive"
                                                   "-ExecutionPolicy" "Bypass" "-File" script
                                                   "-DataPath" data-file)))
          ;; Actual Windows evidence showed a still-running, silent legacy
          ;; PowerShell at eight seconds.  Prefer pwsh and allow one bounded
          ;; cold startup/module load, without retrying the filesystem action.
          (let ((deadline (+ (float-time) 20)))
            (while (and (process-live-p child) (< (float-time) deadline))
              (accept-process-output child 0.02)))
          (let* ((tail (with-current-buffer output
                         (buffer-substring-no-properties (max (point-min) (- (point-max) 2000)) (point-max))))
                 (encoded (mapconcat (lambda (character)
                                       (if (<= 32 character 126) (char-to-string character)
                                         (format "\\u{%X}" character))) tail "")))
            (message "DIAG junction executable=%s phase=%s status=%S alive=%S exit=%S output=%s"
                     (file-name-nondirectory powershell)
                     (condition-case nil
                         (with-temp-buffer (insert-file-contents phase-file nil 0 256) (buffer-string))
                       (error "not-started-or-unreadable"))
                     (process-status child) (and (process-live-p child) t)
                     (process-exit-status child) (substring encoded (max 0 (- (length encoded) 2000)))))
          (when (or (process-live-p child) (/= (process-exit-status child) 0))
            (error "Owned junction helper failed or timed out; see bounded output diagnostic")))
      (when (and child (process-live-p child)) (delete-process child))
      (kill-buffer output))))

(ert-deftest sumi-explorer-windows-zip-and-top-undo ()
  "Real Windows ZIP preserves nested Unicode/spaced fixtures and reverses outputs."
  (sumi-explorer-windows-test--session
    (let* ((source (expand-file-name "日本語 source" root))
           (nested (expand-file-name "nested folder" source))
           (first (expand-file-name "日本語 spaced.txt" source))
           (second (expand-file-name "binary payload.bin" nested))
           (large (expand-file-name "large 日本語 payload.bin" nested))
           (payload (sumi-explorer-windows-test--large-payload))
           (archive (expand-file-name "日本語 archive.zip" root))
           (destination (expand-file-name "new extracted directory" root)))
      (make-directory nested t)
      (sumi-explorer-windows-test--diagnostic "after-create-nested" nested)
      (sumi-explorer-windows-test--write first (encode-coding-string "日本語 fixture\n" 'utf-8-unix))
      (sumi-explorer-windows-test--write second (unibyte-string 0 1 127 128 255 13 10))
      (should (> (length payload) (* 2 65536)))
      (sumi-explorer-windows-test--write large payload)
      (sumi-explorer-zip-create archive (list source) 30)
      (should (file-regular-p archive))
      (let ((archive-id (sumi-explorer-windows-test--top-id "zip-create")))
        (sumi-explorer-zip-extract archive destination 30)
        (let* ((restored (expand-file-name (file-name-nondirectory source) destination))
               (restored-nested (expand-file-name (file-name-nondirectory nested) restored)))
          (should (equal payload (sumi-explorer-windows-test--bytes
                                  (expand-file-name (file-name-nondirectory large) restored-nested))))
          (should (equal (sumi-explorer-windows-test--bytes first)
                         (sumi-explorer-windows-test--bytes
                          (expand-file-name (file-name-nondirectory first) restored))))
          (should (equal (sumi-explorer-windows-test--bytes second)
                         (sumi-explorer-windows-test--bytes
                          (expand-file-name (file-name-nondirectory second) restored-nested)))))
        (let ((extraction-id (sumi-explorer-windows-test--top-id "zip-extract")))
          (sumi-explorer-undo extraction-id 30)
          (should-not (file-exists-p destination))
          (should-error (sumi-explorer-undo extraction-id 10)))
        (should (equal (sumi-explorer-windows-test--top-id "zip-create") archive-id))
        (sumi-explorer-undo archive-id 30)
        (should-not (file-exists-p archive)))
      (should (file-regular-p first))
      (should (equal payload (sumi-explorer-windows-test--bytes large)))
      (should (equal (sumi-explorer-windows-test--bytes second)
                     (unibyte-string 0 1 127 128 255 13 10)))
      (should-not (cdr (assoc "groups" (sumi-explorer-undo-history))))
      ;; Keep the junction outside ROOT: shared recursive fixture cleanup can
      ;; never traverse it, even if the explicit nonrecursive removal fails.
      (let* ((alias-root (make-temp-file "sumi-windows-junction-" t))
             (alias (expand-file-name "owned source alias" alias-root))
             (invalid-archive (expand-file-name "must not create.zip" alias)))
        (unwind-protect
            (progn
              (sumi-explorer-windows-test--junction "Create" alias source)
              (should (file-equal-p alias source))
              (let ((failure (should-error (sumi-explorer-zip-create invalid-archive (list source) 30))))
                (should (string-match-p (regexp-quote "Destination aliases the source or a source descendant")
                                        (error-message-string failure))))
              (should-not (file-exists-p invalid-archive))
              (should (equal payload (sumi-explorer-windows-test--bytes large)))
              (should-not (cdr (assoc "groups" (sumi-explorer-undo-history)))))
          (when (or (file-exists-p alias) (file-symlink-p alias))
            (sumi-explorer-windows-test--junction "Remove" alias source))
          ;; No recursive fallback: a failed unlink intentionally retains this
          ;; separate test-owned container rather than walking its target.
          (delete-directory alias-root))))))

(ert-deftest sumi-explorer-windows-copy-undo-and-replacement-refusal ()
  "Undo removes only its own output and refuses a same-byte replacement."
  (sumi-explorer-windows-test--session
    (let* ((source (expand-file-name "original 日本語.txt" root))
           (output (expand-file-name "copied output.txt" root))
           (replacement (expand-file-name "replacement object.txt" root))
           (bytes (unibyte-string 0 42 128 255 10)))
      (sumi-explorer-windows-test--write source bytes)
      (sumi-explorer-copy source output 20)
      (should-not (file-equal-p source output))
      (sumi-explorer-undo (sumi-explorer-windows-test--top-id "copy") 20)
      (should-not (file-exists-p output))
      (should (equal (sumi-explorer-windows-test--bytes source) bytes))
      (sumi-explorer-copy source output 20)
      (let ((id (sumi-explorer-windows-test--top-id "copy")))
        ;; Create the replacement before unlinking the original, preventing
        ;; immediate file-ID reuse from making this an ambiguous fixture.
        (sumi-explorer-windows-test--write replacement bytes)
        (should-not (file-equal-p output replacement))
        (delete-file output)
        (rename-file replacement output)
        (should-error (sumi-explorer-undo id 20))
        (should (equal (sumi-explorer-windows-test--top-id "copy") id))
        (should (equal (sumi-explorer-windows-test--bytes output) bytes))
        (should (equal (sumi-explorer-windows-test--bytes source) bytes))))))

(ert-deftest sumi-explorer-windows-readonly-refusal-and-chmod-rejection ()
  "Readonly-only changes invalidate Undo; POSIX chmod is unsupported on Windows."
  (sumi-explorer-windows-test--session
    (let* ((source (expand-file-name "original mode fixture.txt" root))
           (output (expand-file-name "readonly output.txt" root))
           (bytes (unibyte-string 70 105 120 116 117 114 101 10))
           original-mode)
      (sumi-explorer-windows-test--write source bytes)
      (sumi-explorer-copy source output 20)
      (setq original-mode (file-modes output))
      (should (/= 0 (logand original-mode #o222)))
      (unwind-protect
          (let ((id (sumi-explorer-windows-test--top-id "copy")))
            (set-file-modes output (logand original-mode (lognot #o222)))
            (should (= 0 (logand (file-modes output) #o222)))
            (should (equal (sumi-explorer-windows-test--bytes output) bytes))
            (should-error (sumi-explorer-undo id 20))
            (should (file-exists-p output))
            (should (= 0 (logand (file-modes output) #o222)))
            (should (equal (sumi-explorer-windows-test--top-id "copy") id))
            (let ((failure (should-error (sumi-explorer-set-permissions source "0640" 10))))
              (should (string-match-p "[Uu]nsupported\\|Windows" (error-message-string failure))))
            (should (equal (sumi-explorer-windows-test--bytes source) bytes))
            (should (equal (sumi-explorer-windows-test--bytes output) bytes)))
        ;; Restore the test-created readonly attribute only for fixture cleanup.
        (when (file-exists-p output) (set-file-modes output original-mode))))))

(defconst sumi-explorer-windows-test--cancel-helper
  "param([uint32]$NativePid, [string]$Receipt)
$ErrorActionPreference = 'Stop'
$deadline = [DateTime]::UtcNow.AddSeconds(9)
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Text;
public static class SumiOwnedMenuCancel {
    private delegate bool EnumCallback(IntPtr window, IntPtr data);
    [StructLayout(LayoutKind.Sequential)]
    private struct Rect { public int Left, Top, Right, Bottom; }
    [StructLayout(LayoutKind.Sequential)]
    private struct GuiThreadInfo {
        public uint Size, Flags;
        public IntPtr Active, Focus, Capture, MenuOwner, MoveSize, Caret;
        public Rect CaretRect;
    }
    [DllImport(\"user32.dll\")]
    private static extern bool EnumWindows(EnumCallback callback, IntPtr data);
    [DllImport(\"user32.dll\")]
    private static extern uint GetWindowThreadProcessId(IntPtr window, out uint process);
    [DllImport(\"user32.dll\", CharSet = CharSet.Unicode, EntryPoint = \"GetClassNameW\")]
    private static extern int GetClassName(IntPtr window, StringBuilder name, int count);
    [DllImport(\"user32.dll\")]
    private static extern bool GetGUIThreadInfo(uint thread, ref GuiThreadInfo info);
    [DllImport(\"user32.dll\", EntryPoint = \"PostMessageW\", SetLastError = true)]
    private static extern bool PostMessage(IntPtr window, uint message, IntPtr wparam, IntPtr lparam);
    private static bool OwnedActiveMenu(IntPtr window, uint expected) {
        uint process;
        uint thread = GetWindowThreadProcessId(window, out process);
        if (thread == 0 || process != expected) return false;
        StringBuilder name = new StringBuilder(256);
        if (GetClassName(window, name, name.Capacity) == 0 ||
            name.ToString() != \"SumiExplorerPlatformMenuOwner\") return false;
        GuiThreadInfo info = new GuiThreadInfo();
        info.Size = (uint)Marshal.SizeOf(typeof(GuiThreadInfo));
        return GetGUIThreadInfo(thread, ref info) &&
            (info.Flags & 4) != 0 && info.MenuOwner == window;
    }
    public static bool CancelOwnedMenu(uint process) {
        IntPtr match = IntPtr.Zero;
        EnumWindows(delegate(IntPtr window, IntPtr data) {
            if (OwnedActiveMenu(window, process)) { match = window; return false; }
            return true;
        }, IntPtr.Zero);
        // Revalidate PID, class and actual menu mode immediately before posting.
        return match != IntPtr.Zero && OwnedActiveMenu(match, process) &&
            PostMessage(match, 0x001F, IntPtr.Zero, IntPtr.Zero);
    }
}
'@
while ([DateTime]::UtcNow -lt $deadline) {
    if ([SumiOwnedMenuCancel]::CancelOwnedMenu($NativePid)) {
        $record = \"owned_menu_observed=1`ncancel_posted=1`nnative_pid=$NativePid`n\"
        [IO.File]::WriteAllText($Receipt, $record)
        [Console]::Write($record)
        exit 0
    }
    Start-Sleep -Milliseconds 20
}
throw 'Owned native menu was not observed/cancelled before the deadline'
"
  "Bounded PowerShell helper cancelling only this host's active native menu.
The Emacs parent also enforces a ten-second helper lifetime.")

(ert-deftest sumi-explorer-windows-native-shell-menu-cancel ()
  "Reach the real host's Shell menu from Emacs and cancel only its owned HWND."
  (sumi-explorer-windows-test--session
    (let* ((first (expand-file-name "日本語 first.txt" root))
           (second (expand-file-name "日本語 second.txt" root))
           (targets (list first second))
           (script (expand-file-name "cancel-owned-menu.ps1" root))
           (receipt (expand-file-name "cancel-owned-menu.receipt" root))
           (powershell (or (executable-find "powershell.exe") (executable-find "pwsh.exe")))
           (output (generate-new-buffer " *sumi-owned-menu-cancel*"))
           helper)
      (unwind-protect
          (progn
            (should powershell)
            (sumi-explorer-windows-test--write first (unibyte-string 65 0 255 10))
            (sumi-explorer-windows-test--write second (unibyte-string 66 128 10))
            (sumi-explorer-refresh)
            (sumi-explorer-select first)
            (should (file-equal-p first (cdr (assoc "selected" (sumi-explorer-state)))))
            (sumi-explorer-select-files targets 10)
            (let ((selection (sumi-explorer-selected-files))
                  (history (sumi-explorer-undo-history))
                  (native-pid (process-id sumi-explorer--process))
                  (deadline (+ (float-time) 10)))
              (should (and (integerp native-pid) (> native-pid 0)))
              (let ((coding-system-for-write 'utf-8-unix))
                (write-region sumi-explorer-windows-test--cancel-helper nil script nil 'silent))
              (setq helper
                    (make-process :name "sumi-owned-menu-cancel" :buffer output :noquery t
                                  :command (list powershell "-NoProfile" "-NonInteractive"
                                                 "-ExecutionPolicy" "Bypass" "-File" script
                                                 "-NativePid" (number-to-string native-pid)
                                                 "-Receipt" receipt)))
              (let ((failure (should-error (sumi-explorer-shell-menu targets 10))))
                (should (string-match-p (regexp-quote "Native menu cancelled without invoking a command")
                                        (error-message-string failure))))
              (while (and (process-live-p helper) (< (float-time) deadline))
                (accept-process-output helper 0.02))
              (should-not (process-live-p helper))
              (should (= (process-exit-status helper) 0))
              (should (equal (sumi-explorer-windows-test--bytes receipt)
                             (format "owned_menu_observed=1\ncancel_posted=1\nnative_pid=%d\n" native-pid)))
              (should-not sumi-explorer--pending)
              (should (equal (sumi-explorer-selected-files) selection))
              (should (equal (sumi-explorer-undo-history) history))
              (should (equal (sumi-explorer-windows-test--bytes first) (unibyte-string 65 0 255 10)))
              (should (equal (sumi-explorer-windows-test--bytes second) (unibyte-string 66 128 10)))
              (sumi-explorer-refresh)
              (should (file-equal-p root (cdr (assoc "directory" (sumi-explorer-state)))))))
        (when (and helper (process-live-p helper)) (delete-process helper))
        (message "Owned Shell cancellation helper output: %s" (with-current-buffer output (buffer-string)))
        (kill-buffer output)))))

(provide 'sumi-explorer-windows-test)
;;; sumi-explorer-windows-test.el ends here
