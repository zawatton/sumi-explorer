;;; sumi-explorer-windows-test.el --- Real Windows acceptance -*- lexical-binding: t; -*-

;; Load explicitly, then invoke ert-run-tests-batch-and-exit.  Loading this file
;; never starts tests.  Only owned-menu cancellation is automated; Security UI
;; remains a manual check.
(require 'ert)
(require 'sumi-explorer)

(defun sumi-explorer-windows-test--bytes (path)
  "Read exact fixture bytes from PATH."
  (with-temp-buffer
    (set-buffer-multibyte nil)
    (insert-file-contents-literally path)
    (buffer-string)))

(defun sumi-explorer-windows-test--write (path bytes)
  "Write exact BYTES to the disposable PATH."
  (let ((coding-system-for-write 'no-conversion))
    (write-region bytes nil path nil 'silent)))

(defun sumi-explorer-windows-test--top-id (operation)
  "Require newest Undo group to describe OPERATION and return its ID."
  (let ((group (car (cdr (assoc "groups" (sumi-explorer-undo-history))))))
    (should (equal (cdr (assoc "operation" group)) operation))
    (should (cdr (assoc "id" group)))
    (cdr (assoc "id" group))))

(defmacro sumi-explorer-windows-test--session (&rest body)
  "Run BODY with one real Windows host and disposable ROOT directory."
  (declare (indent 0) (debug t))
  `(progn
     (should (eq system-type 'windows-nt))
     (let* ((binary (getenv "SUMI_EXPLORER_TEST_BIN"))
            (root (make-temp-file "sumi-windows-acceptance-" t))
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
           (progn
             (should (and (stringp binary) (file-executable-p binary)))
             (dolist (name '("config" "data"))
               (make-directory (expand-file-name name root)))
             (setenv "XDG_CONFIG_HOME" (expand-file-name "config" root))
             (setenv "XDG_DATA_HOME" (expand-file-name "data" root))
             (setenv "SUMI_EXPLORER_SESSION" nil)
             (sumi-explorer-start root binary)
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
         ;; Forced disposal is only failure cleanup for this owned test host.
         (sumi-explorer-stop t)
         (delete-directory root t)))))

(ert-deftest sumi-explorer-windows-zip-and-top-undo ()
  "Real Windows ZIP preserves nested Unicode/spaced fixtures and reverses outputs."
  (sumi-explorer-windows-test--session
    (let* ((source (expand-file-name "日本語 source" root))
           (nested (expand-file-name "nested folder" source))
           (first (expand-file-name "日本語 spaced.txt" source))
           (second (expand-file-name "binary payload.bin" nested))
           (archive (expand-file-name "日本語 archive.zip" root))
           (destination (expand-file-name "new extracted directory" root)))
      (make-directory nested t)
      (sumi-explorer-windows-test--write first (encode-coding-string "日本語 fixture\n" 'utf-8-unix))
      (sumi-explorer-windows-test--write second (unibyte-string 0 1 127 128 255 13 10))
      (sumi-explorer-zip-create archive (list source) 30)
      (should (file-regular-p archive))
      (let ((archive-id (sumi-explorer-windows-test--top-id "zip-create")))
        (sumi-explorer-zip-extract archive destination 30)
        (let* ((restored (expand-file-name (file-name-nondirectory source) destination))
               (restored-nested (expand-file-name (file-name-nondirectory nested) restored)))
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
      (should (equal (sumi-explorer-windows-test--bytes second)
                     (unibyte-string 0 1 127 128 255 13 10)))
      (should-not (cdr (assoc "groups" (sumi-explorer-undo-history)))))))

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
