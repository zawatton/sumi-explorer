;;; build.el --- Build the standalone explorer -*- lexical-binding: t; -*-
(require 'subr-x)
(declare-function nelisp-aot-compile-to-object "nelisp-aot-compiler" (form file &rest options))
(defconst sumi-explorer-build-root
  (expand-file-name ".." (file-name-directory (or load-file-name buffer-file-name))))
(defun sumi-explorer-build ()
  "Compile and atomically replace the explorer executable on success."
  (let* ((nelisp (or (getenv "NELISP_REPO_ROOT")
                     (expand-file-name "../nelisp" sumi-explorer-build-root)))
         (windows (memq system-type '(windows-nt cygwin ms-dos)))
         (format (cond (windows 'coff) ((eq system-type 'darwin) 'mach-o) (t 'elf)))
         (arch (cond
                ((string-match-p "\\`\\(?:aarch64\\|arm64\\)" system-configuration) 'aarch64)
                ((string-match-p "\\`\\(?:x86_64\\|amd64\\)" system-configuration) 'x86_64)
                (t (error "Unsupported native architecture: %s" system-configuration))))
         (target (expand-file-name "target" sumi-explorer-build-root))
         (exe (expand-file-name (if windows "sumi-explorer.exe" "sumi-explorer") target))
         (scratch nil))
    (add-to-list 'load-path (expand-file-name "lisp" nelisp))
    (add-to-list 'load-path (expand-file-name "src" nelisp))
    (require 'nelisp-aot-compiler)
    (make-directory target t)
    (setq scratch (make-temp-file (expand-file-name ".explorer-" target) t))
    (unwind-protect
        (let ((obj (expand-file-name "explorer.o" scratch))
              (platform-obj (expand-file-name "platform.o" scratch))
              (output (expand-file-name (if windows "explorer.exe" "explorer") scratch))
              (libs nil) (cflags nil))
          (with-temp-buffer
            (unless (eq 0 (process-file "pkg-config" nil t nil "--libs" "gtk4" "libarchive"))
              (error "pkg-config failed: %s" (buffer-string)))
            (setq libs (split-string-and-unquote (string-trim (buffer-string)))))
          (with-temp-buffer
            (unless (eq 0 (process-file "pkg-config" nil t nil "--cflags" "gtk4" "libarchive"))
              (error "GTK/libarchive compiler flags failed: %s" (buffer-string)))
            (setq cflags (split-string-and-unquote (string-trim (buffer-string)))))
          (with-temp-buffer
            (unless (eq 0 (apply #'process-file (or (getenv "CC") "cc") nil t nil
                                 (append '("-std=c11" "-Wall" "-Wextra") cflags
                                         (list "-c" (expand-file-name "explorer/platform.c" sumi-explorer-build-root)
                                               "-o" platform-obj))))
              (error "Platform bridge compilation failed: %s" (buffer-string))))
          (with-temp-buffer
            (insert-file-contents (expand-file-name "explorer/sumi-explorer-native.el" sumi-explorer-build-root))
            (goto-char (point-min))
            (nelisp-aot-compile-to-object (read (current-buffer)) obj :format format :arch arch))
          (with-temp-buffer
            (unless (eq 0 (apply #'process-file (or (getenv "CC") "cc") nil t nil
                                 obj platform-obj "-o" output
                                 (append libs (when windows '("-lshell32" "-lole32" "-luuid" "-luser32" "-ladvapi32")))))
              (error "Link failed: %s" (buffer-string))))
          (rename-file output exe t)
          (princ (format "EXPLORER-BUILD-OK %s\n" exe)))
      (when scratch (delete-directory scratch t)))))
(when noninteractive (sumi-explorer-build))
