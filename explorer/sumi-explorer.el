;;; sumi-explorer.el --- Local Sumi Explorer client -*- lexical-binding: t; -*-

(require 'cl-lib)
(require 'subr-x)

(defconst sumi-explorer--default-executable
  (expand-file-name (if (eq system-type 'windows-nt)
                        "../target/sumi-explorer.exe" "../target/sumi-explorer")
                    (file-name-directory (or load-file-name buffer-file-name))))
(defvar sumi-explorer--session-directory nil)
(defvar sumi-explorer--process nil)
(defvar sumi-explorer--request-id 0)
(defvar sumi-explorer--pending nil)
(defvar sumi-explorer--owned-session nil)
(defvar sumi-explorer--owned-process nil)
(defvar sumi-explorer--quit-requested nil)
(defvar sumi-explorer--stop-timeout 5)
(defconst sumi-explorer--maximum-request-id 9223372036854775807)

(defun sumi-explorer--escape (value)
  "Encode VALUE as GLib string data."
  (unless (and (stringp value)
               (cl-loop for c across value
                        always (and (< 0 c) (<= c #x10ffff)
                                    (not (<= #xd800 c #xdfff)))))
    (error "Invalid Explorer value"))
  (mapconcat (lambda (c) (or (cdr (assq c '((?\\ . "\\\\") (?\n . "\\n")
                                         (?\r . "\\r") (?\t . "\\t")
                                         (?\s . "\\s"))))
                            (char-to-string c))) value ""))

(defun sumi-explorer--unescape (value)
  "Decode GLib string VALUE, rejecting unknown escapes."
  (let ((i 0) result)
    (while (< i (length value))
      (let ((c (aref value i)))
        (when (= c 0) (error "NUL in Explorer value"))
        (when (= c ?\\)
          (setq i (1+ i))
          (let ((entry (and (< i (length value))
                            (assq (aref value i) '((?\\ . ?\\) (?n . ?\n)
                                                  (?r . ?\r) (?t . ?\t) (?s . ?\s))))))
            (unless entry (error "Invalid Explorer escape"))
            (setq c (cdr entry))))
        (push c result))
      (setq i (1+ i)))
    (apply #'string (nreverse result))))

(defun sumi-explorer--read-state (filename)
  "Read and validate session FILENAME as a string-keyed alist."
  (unless sumi-explorer--session-directory (error "Explorer is not started"))
  (let* ((file (expand-file-name filename sumi-explorer--session-directory))
         (attrs (file-attributes file))
         (keys '("version" "request" "status" "directory" "selected" "message"))
         result)
    (unless (and attrs (null (car attrs)) (<= (file-attribute-size attrs) 1048576))
      (error "Missing or oversized Explorer state"))
    (with-temp-buffer
      (let ((coding-system-for-read 'utf-8-unix)) (insert-file-contents file))
      (when (> (buffer-size) 1048576) (error "Oversized Explorer state"))
      (unless (cl-loop for c across (buffer-string)
                       always (and (<= c #x10ffff)
                                   (not (<= #xd800 c #xdfff))))
        (error "Invalid Unicode in Explorer state"))
      (let ((lines (split-string (buffer-string) "\n")))
        (unless (equal (pop lines) "[Explorer]") (error "Invalid Explorer group"))
        (dolist (line lines)
          (unless (string-empty-p line)
            (unless (string-match "\\`\\([a-z]+\\)=\\(.*\\)\\'" line)
              (error "Malformed Explorer state"))
            (let ((key (match-string 1 line)) (value (match-string 2 line)))
              (unless (and (member key keys) (not (assoc key result)))
                (error "Duplicate or unknown Explorer key"))
              (push (cons key (sumi-explorer--unescape value)) result))))))
    (unless (and (= (length result) (length keys))
                 (equal (cdr (assoc "version" result)) "1")
                 (string-match-p "\\`\\(?:0\\|[1-9][0-9]*\\)\\'" (cdr (assoc "request" result)))
                 (<= (string-to-number (cdr (assoc "request" result)))
                     sumi-explorer--maximum-request-id)
                 (member (cdr (assoc "status" result)) '("ok" "error")))
      (error "Invalid Explorer schema"))
    (nreverse result)))

(defun sumi-explorer-state ()
  "Read the current GUI state as a validated string-keyed alist."
  (sumi-explorer--read-state "state.ini"))

(defun sumi-explorer-start (&optional directory executable)
  "Start Explorer directly in DIRECTORY using EXECUTABLE."
  (interactive)
  (when sumi-explorer--session-directory (error "Explorer already started"))
  (let ((root (expand-file-name (or directory default-directory)))
        (program (expand-file-name (or executable sumi-explorer--default-executable))))
    (unless (file-directory-p root) (error "Invalid Explorer directory"))
    (unless (and (file-regular-p program) (file-executable-p program))
      (error "Invalid Explorer executable"))
    (setq sumi-explorer--session-directory (make-temp-file "sumi-explorer-" t)
          sumi-explorer--owned-session sumi-explorer--session-directory
          sumi-explorer--request-id 0 sumi-explorer--pending nil
          sumi-explorer--quit-requested nil)
    (condition-case err
        (progn
          (set-file-modes sumi-explorer--session-directory #o700)
          (let ((process-environment (copy-sequence process-environment))
              (deadline (+ (float-time) 5)))
          (setenv "SUMI_EXPLORER_SESSION" sumi-explorer--session-directory)
          (setenv "SUMI_EXPLORER_ROOT" root)
          (setq sumi-explorer--process
                (make-process :name "sumi-explorer" :command (list program)
                              :connection-type 'pipe :noquery t))
          (setq sumi-explorer--owned-process sumi-explorer--process)
          (while (and (process-live-p sumi-explorer--process)
                      (not (file-exists-p (expand-file-name "state.ini" sumi-explorer--session-directory)))
                      (< (float-time) deadline))
            (accept-process-output sumi-explorer--process 0.02))
          (unless (process-live-p sumi-explorer--process) (error "Explorer exited during startup"))
          (let ((state (sumi-explorer-state)))
            (unless (and (equal (cdr (assoc "request" state)) "0")
                         (equal (cdr (assoc "status" state)) "ok"))
              (error "Explorer failed readiness")))
          sumi-explorer--process))
      ((error quit)
       (let ((inhibit-quit t)) (sumi-explorer-stop t))
       (signal (car err) (cdr err))))))

(defconst sumi-explorer--mutation-commands '("copy" "move" "rename" "trash" "mkdir"))

(defun sumi-explorer--local-absolute-path-p (path)
  "Return non-nil when PATH names an absolute local filesystem path."
  (and (stringp path) (file-name-absolute-p path)
       (not (string-prefix-p "~" path))
       ;; Malformed TRAMP names (for example with LF) can evade file-remote-p.
       (not (string-match-p "\\`/[^/:]+:[^/]*:" path))
       (not (file-remote-p path))))

(defun sumi-explorer--publish (filename data &optional on-published)
  "Atomically publish bounded UTF-8 DATA to session FILENAME.
Run ON-PUBLISHED before allowing a quit after publication."
  (when (> (string-bytes data) 1048576) (error "Oversized Explorer request"))
  (unless (and sumi-explorer--session-directory
               (file-directory-p sumi-explorer--session-directory))
    (error "Explorer session is unavailable"))
  (let ((temp (make-temp-file (expand-file-name ".publish-" sumi-explorer--session-directory))))
    (unwind-protect
        (progn
          (set-file-modes temp #o600)
          (let ((coding-system-for-write 'utf-8-unix))
            (write-region data nil temp nil 'silent))
          (let ((inhibit-quit t))
            (rename-file temp (expand-file-name filename sumi-explorer--session-directory) t)
            (when on-published (funcall on-published))))
      (when (file-exists-p temp) (delete-file temp)))))

(defun sumi-explorer--submit (command path &optional destination)
  "Validate and submit one COMMAND with PATH and optional DESTINATION."
  (unless (member command (append '("open" "select" "refresh" "properties" "up" "quit"
                                    "new-tab" "new-window" "close-tab" "activate-tab"
                                    "back" "forward" "home" "filter" "sort" "order"
                                    "hidden" "view" "bookmark-add" "bookmark-remove"
                                    "bookmark-open" "clipboard-copy" "clipboard-cut" "paste"
                                    "zip-extract" "undo" "permissions" "chmod"
                                    "search" "search-cancel" "search-open")
                                  sumi-explorer--mutation-commands))
    (error "Unsupported Explorer command"))
  (let* ((encoded (sumi-explorer--escape (or path "")))
         (needs-destination (member command '("copy" "move" "rename" "zip-extract" "chmod")))
         (encoded-destination (and destination (sumi-explorer--escape destination))))
    (cond
     ((member command '("new-tab" "new-window" "bookmark-add" "bookmark-remove" "paste"))
      (unless (or (string-empty-p (or path "")) (sumi-explorer--local-absolute-path-p path))
        (error "Workspace directory must be empty or absolute and local")))
     ((member command '("activate-tab" "undo"))
      (sumi-explorer--decimal path 1 sumi-explorer--maximum-request-id))
     ((member command '("close-tab" "back" "forward" "home" "clipboard-copy" "clipboard-cut"))
      (unless (string-empty-p (or path "")) (error "Command requires an empty path")))
     ((equal command "bookmark-open")
      (unless (sumi-explorer--local-absolute-path-p path) (error "Bookmark must be absolute and local")))
     ((equal command "filter")
      (when (> (length (or path "")) 256) (error "Filter exceeds 256 scalar characters")))
     ((equal command "search")
      (unless (and (stringp path) (<= 1 (length path) 256))
        (error "Search requires 1–256 Unicode characters")))
     ((equal command "search-cancel")
      (unless (string-empty-p (or path "")) (error "Search cancellation requires an empty path")))
     ((equal command "search-open")
      (unless (sumi-explorer--local-absolute-path-p path) (error "Search result must be absolute and local")))
     ((equal command "sort")
      (unless (member path '("name" "size" "date" "type")) (error "Invalid Explorer sort key")))
     ((equal command "order")
      (unless (member path '("asc" "desc")) (error "Invalid Explorer sort order")))
     ((equal command "hidden")
      (unless (member path '("show" "hide")) (error "Invalid hidden-file setting")))
     ((equal command "view")
      (unless (member path '("list" "details")) (error "Invalid Explorer view"))))
    (when (member command (append '("zip-extract" "permissions" "chmod") sumi-explorer--mutation-commands))
      (unless (sumi-explorer--local-absolute-path-p path)
        (error "Mutation path must be absolute and local")))
    (if needs-destination
        (unless (if (equal command "chmod")
                    (and (stringp destination)
                         (string-match-p "\\`0?[0-7][0-7][0-7]\\'" destination))
                  (and destination (sumi-explorer--local-absolute-path-p destination)))
          (error "Mutation destination must be absolute and local"))
      (when destination (error "Unexpected Explorer destination")))
    (when (and (equal command "rename")
               (not (equal (file-name-directory (directory-file-name (expand-file-name path)))
                           (file-name-directory (directory-file-name (expand-file-name destination))))))
      (error "Rename must stay in the same parent directory"))
    (unless (and (processp sumi-explorer--process) (process-live-p sumi-explorer--process))
      (error "Explorer is not running"))
    (when sumi-explorer--pending (error "Explorer request is pending"))
    (unless (and (integerp sumi-explorer--request-id)
                 (<= 0 sumi-explorer--request-id)
                 (< sumi-explorer--request-id sumi-explorer--maximum-request-id))
      (error "Explorer request identifiers exhausted or invalid"))
    (let* ((id (1+ sumi-explorer--request-id))
           (data (concat (format "[Explorer]\nversion=1\nrequest=%d\ncommand=%s\npath=%s\n" id command encoded)
                         (when needs-destination (concat "destination=" encoded-destination "\n")))))
      (sumi-explorer--publish
       "request.ini" data
       (lambda ()
         (setq sumi-explorer--request-id id sumi-explorer--pending id)
         (when (equal command "quit") (setq sumi-explorer--quit-requested t))))
      id)))

;;;###autoload
(defun sumi-explorer-begin-operation (command path &optional destination)
  "Begin one mutation COMMAND on PATH, optionally to DESTINATION.
Return its request ID without waiting.  Use `sumi-explorer-await' for the
actual result; publishing a request never establishes operation success."
  (unless (member command sumi-explorer--mutation-commands)
    (error "Unsupported Explorer operation"))
  (sumi-explorer--submit command path destination))

;;;###autoload
(defun sumi-explorer-await (&optional timeout)
  "Await the pending request's final response for TIMEOUT seconds (default 5).
Timeout and quit retain the request for a later call, without resubmitting it."
  (interactive)
  (setq timeout (or timeout 5))
  (unless (and (numberp timeout) (> timeout 0)) (error "Invalid timeout"))
  (unless sumi-explorer--pending (error "No Explorer request is pending"))
  (let ((id sumi-explorer--pending)
        (session sumi-explorer--session-directory)
        (process sumi-explorer--process)
        (deadline (+ (float-time) timeout)))
    (catch 'ack
      (while (< (float-time) deadline)
        ;; Timers and process callbacks may await A and submit B while this
        ;; waiter yields.  It must never consume or clear B's transaction.
        (unless (and (equal sumi-explorer--session-directory session)
                     (eq sumi-explorer--process process)
                     (equal sumi-explorer--pending id))
          (error "Explorer pending transaction changed while awaiting"))
        (when (file-exists-p (expand-file-name "response.ini" sumi-explorer--session-directory))
          (let* ((state (sumi-explorer--read-state "response.ini"))
                 (ack (string-to-number (cdr (assoc "request" state)))))
            (when (> ack id) (error "Unexpected Explorer acknowledgement"))
            (when (= ack id)
              (setq sumi-explorer--pending nil)
              (when (equal (cdr (assoc "status" state)) "error")
                (error "Explorer: %s" (cdr (assoc "message" state))))
              (throw 'ack state))))
        (unless (and (processp sumi-explorer--process) (process-live-p sumi-explorer--process))
          (error "Explorer exited; request remains unresolved"))
        (accept-process-output sumi-explorer--process 0.02))
      (error "Explorer request timed out; request remains pending"))))

;;;###autoload
(defun sumi-explorer-cancel ()
  "Request cooperative cancellation and return the targeted request ID.
Require a positive pending protocol request.  The request remains unresolved
until its actual final response; GUI cancellation belongs to the native UI."
  (interactive)
  (unless (and (processp sumi-explorer--process) (process-live-p sumi-explorer--process))
    (error "Explorer is not running"))
  (unless (and (integerp sumi-explorer--pending)
               (< 0 sumi-explorer--pending)
               (<= sumi-explorer--pending sumi-explorer--maximum-request-id))
    (error "No Explorer protocol request is pending"))
  (let ((id sumi-explorer--pending))
    (sumi-explorer--publish "cancel.ini" (format "[Explorer]\nversion=1\nrequest=%d\n" id))
    id))

(defun sumi-explorer-send (command &optional path timeout)
  "Send COMMAND with PATH, waiting TIMEOUT seconds for acknowledgement.
Commands requiring a destination use the operation API instead."
  (unless (and (or (null timeout) (numberp timeout)) (> (or timeout 5) 0))
    (error "Invalid timeout"))
  (sumi-explorer--submit command path)
  (sumi-explorer-await timeout))

(defun sumi-explorer--run-operation (command path destination timeout)
  "Validate TIMEOUT, submit COMMAND with PATH and DESTINATION, and await."
  (unless (and (or (null timeout) (numberp timeout)) (> (or timeout 5) 0))
    (error "Invalid timeout"))
  (sumi-explorer-begin-operation command path destination)
  (sumi-explorer-await timeout))

(defun sumi-explorer--interactive-source (prompt)
  "Freeze the GUI selection, or ask for a local source using PROMPT."
  (or (car (condition-case nil (sumi-explorer-selected-files) (error nil)))
      (read-file-name prompt nil nil t)))

(defun sumi-explorer--interactive-transfer (prompt)
  "Read a frozen source and an exact new destination for PROMPT."
  (let ((source (sumi-explorer--interactive-source (concat prompt " source: "))))
    (list source (read-file-name (concat prompt " to new path: ")
                                (file-name-directory (directory-file-name source)) nil nil))))

(defun sumi-explorer--interactive-directory ()
  "Return the valid local GUI directory, falling back to `default-directory'."
  (let ((directory (condition-case nil
                       (cdr (assoc "directory" (sumi-explorer-state)))
                     (error nil))))
    (if (and (stringp directory)
             (sumi-explorer--local-absolute-path-p directory)
             (file-directory-p directory))
        (file-name-as-directory directory)
      default-directory)))

;;;###autoload
(defun sumi-explorer-copy (source destination &optional timeout)
  "Copy SOURCE to the exact new DESTINATION and await completion."
  (interactive (sumi-explorer--interactive-transfer "Copy"))
  (sumi-explorer--run-operation "copy" source destination timeout))

;;;###autoload
(defun sumi-explorer-move (source destination &optional timeout)
  "Move SOURCE to the exact new DESTINATION and await completion."
  (interactive (sumi-explorer--interactive-transfer "Move"))
  (sumi-explorer--run-operation "move" source destination timeout))

;;;###autoload
(defun sumi-explorer-transfer (source destination policy &optional mode timeout)
  "Transfer SOURCE to exact DESTINATION using explicit conflict POLICY.
POLICY is `replace', `merge', `merge-replace', or `skip' (symbols or strings).
MODE is `copy' (default) or `move'. Merge preserves unrelated entries; move
retains the source if any item is skipped or the transfer only partly succeeds.
Interactive Cancel publishes no request. Ordinary copy/move stay no-overwrite."
  (interactive
   (let* ((source (sumi-explorer--interactive-source "Transfer source: "))
          (destination (read-file-name "Exact destination: " nil nil nil))
          (policy (completing-read "Conflict policy: "
                                   '("Cancel" "replace" "merge" "merge-replace" "skip") nil t nil nil "Cancel")))
     (when (equal policy "Cancel") (keyboard-quit))
     (list source destination policy
           (completing-read "Transfer: " '("copy" "move") nil t nil nil "copy"))))
  (setq policy (if (symbolp policy) (symbol-name policy) policy)
        mode (if (symbolp mode) (symbol-name (or mode 'copy)) mode))
  (unless (and (member policy '("replace" "merge" "merge-replace" "skip"))
               (member mode '("copy" "move"))
               (sumi-explorer--local-absolute-path-p source)
               (sumi-explorer--local-absolute-path-p destination)
               (or (null timeout) (and (numberp timeout) (> timeout 0))))
    (error "Invalid explicit Explorer transfer"))
  (unless (and (processp sumi-explorer--process) (process-live-p sumi-explorer--process))
    (error "Explorer is not running"))
  (when sumi-explorer--pending (error "Explorer request is pending"))
  (unless (and (integerp sumi-explorer--request-id)
               (<= 0 sumi-explorer--request-id)
               (< sumi-explorer--request-id sumi-explorer--maximum-request-id))
    (error "Explorer request identifiers exhausted or invalid"))
  (let* ((id (1+ sumi-explorer--request-id))
         (data (format "[Explorer]\nversion=1\nrequest=%d\ncommand=transfer\npath=%s\ndestination=%s\npolicy=%s\nmode=%s\n"
                       id (sumi-explorer--escape source) (sumi-explorer--escape destination) policy mode)))
    (sumi-explorer--publish "request.ini" data
                            (lambda () (setq sumi-explorer--request-id id sumi-explorer--pending id)))
    (sumi-explorer-await timeout)))

;;;###autoload
(defun sumi-explorer-rename (path new-name &optional timeout)
  "Rename PATH to NEW-NAME in its existing parent and await completion."
  (interactive
   (let ((path (sumi-explorer--interactive-source "Rename source: ")))
     (list path (read-string "New name in same directory: "
                             (file-name-nondirectory (directory-file-name path))))))
  (sumi-explorer--escape new-name)
  (unless (and (not (string-empty-p new-name))
               (not (member new-name '("." "..")))
               (not (string-match-p (if (eq system-type 'windows-nt) "[/\\\\]" "/") new-name)))
    (error "Rename requires a single new name"))
  (sumi-explorer--escape path)
  (unless (sumi-explorer--local-absolute-path-p path) (error "Rename path must be absolute and local"))
  (sumi-explorer--run-operation
   "rename" path (concat (file-name-directory (directory-file-name path)) new-name) timeout))

;;;###autoload
(defun sumi-explorer-trash (path &optional timeout)
  "Trash PATH through GIO and await its actual result.
Interactive calls confirm the frozen selection or prompted source first."
  (interactive
   (let ((path (sumi-explorer--interactive-source "Trash source: ")))
     (unless (y-or-n-p (format "Move %S to Trash? " path))
       (user-error "Trash cancelled"))
     (list path)))
  (sumi-explorer--run-operation "trash" path nil timeout))

;;;###autoload
(defun sumi-explorer-mkdir (path &optional timeout)
  "Create the new directory PATH and await completion."
  (interactive (list (read-file-name "New directory path: "
                                    (sumi-explorer--interactive-directory) nil nil)))
  (sumi-explorer--run-operation "mkdir" path nil timeout))

(defun sumi-explorer--read-groups (filename)
  "Read bounded UTF-8 key-file FILENAME, rejecting duplicate groups and keys."
  (unless sumi-explorer--session-directory (error "Explorer is not started"))
  (let* ((file (expand-file-name filename sumi-explorer--session-directory))
         (attributes (file-attributes file)) groups current)
    (unless (and attributes (null (car attributes))
                 (<= (file-attribute-size attributes) 1048576))
      (error "Missing or oversized Explorer sidecar"))
    (with-temp-buffer
      (let ((coding-system-for-read 'utf-8-unix))
        (insert-file-contents file nil 0 1048577))
      (when (> (string-bytes (buffer-string)) 1048576)
        (error "Oversized Explorer sidecar"))
      (unless (cl-loop for c across (buffer-string)
                       always (and (< 0 c) (<= c #x10ffff)
                                   (not (<= #xd800 c #xdfff))))
        (error "Invalid Unicode in Explorer sidecar"))
      (dolist (line (split-string (buffer-string) "\n"))
        (cond
         ((string-empty-p line))
         ((string-match "\\`\\[\\([A-Za-z][A-Za-z0-9]*\\)\\]\\'" line)
          (let ((name (match-string 1 line)))
            (when (>= (length groups) 257) (error "Too many Explorer groups"))
            (when (assoc name groups) (error "Duplicate Explorer group"))
            (setq current (list name))
            (push current groups)))
         ((and current (string-match "\\`\\([a-z][a-z0-9]*\\)=\\(.*\\)\\'" line))
          (let ((key (match-string 1 line)) (value (match-string 2 line)))
            (when (>= (length (cdr current)) 261) (error "Too many Explorer keys"))
            (when (assoc key (cdr current)) (error "Duplicate Explorer key"))
            (setcdr current (cons (cons key (sumi-explorer--unescape value)) (cdr current)))))
         (t (error "Malformed Explorer sidecar")))))
    (nreverse groups)))

(defun sumi-explorer--exact-keys (fields keys)
  "Require FIELDS to contain precisely KEYS."
  (unless (and (= (length fields) (length keys))
               (cl-every (lambda (key) (assoc key fields)) keys))
    (error "Invalid Explorer sidecar keys")))

(defun sumi-explorer--decimal (value minimum maximum)
  "Validate canonical decimal VALUE between MINIMUM and MAXIMUM."
  (unless (and (stringp value) (string-match-p "\\`\\(?:0\\|[1-9][0-9]*\\)\\'" value)
               (<= minimum (string-to-number value) maximum))
    (error "Invalid Explorer numeric bound"))
  (string-to-number value))

(defun sumi-explorer--validated-paths (paths allow-empty)
  "Validate and copy distinct local absolute PATHS, allowing zero if ALLOW-EMPTY."
  (unless (and (listp paths) (proper-list-p paths)
               (<= (if allow-empty 0 1) (length paths) 256))
    (error "Explorer requires at most 256 paths"))
  (let ((seen (make-hash-table :test 'equal)))
    (mapcar (lambda (path)
              (sumi-explorer--escape path)
              (unless (and (sumi-explorer--local-absolute-path-p path)
                           (not (gethash path seen)))
                (error "Invalid or duplicate Explorer path"))
              (puthash path t seen)
              (copy-sequence path)) paths)))

(defun sumi-explorer--selection-snapshot ()
  "Read the independently consistent multi-selection snapshot."
  (let* ((groups (sumi-explorer--read-groups "selection.ini"))
         (fields (cdr (assoc "Selection" groups)))
         (count (sumi-explorer--decimal (cdr (assoc "count" fields)) 0 256))
         (keys (append '("version" "status" "directory" "count" "message")
                       (cl-loop for index below count collect (format "path%d" index)))))
    (unless (and (= (length groups) 1) (assoc "Selection" groups))
      (error "Invalid Explorer selection group"))
    (sumi-explorer--exact-keys fields keys)
    (unless (and (equal (cdr (assoc "version" fields)) "1")
                 (member (cdr (assoc "status" fields)) '("ok" "error"))
                 (sumi-explorer--local-absolute-path-p (cdr (assoc "directory" fields))))
      (error "Invalid Explorer selection schema"))
    (let ((paths (sumi-explorer--validated-paths
                  (cl-loop for index below count collect (cdr (assoc (format "path%d" index) fields))) t)))
      (when (equal (cdr (assoc "status" fields)) "error")
        (unless (= count 0) (error "Error selection contains paths"))
        (error "Explorer selection: %s" (cdr (assoc "message" fields))))
      paths)))

(defun sumi-explorer-selected-files ()
  "Return validated GUI selection, falling back only when the sidecar is absent."
  (unless sumi-explorer--session-directory (error "Explorer is not started"))
  (let ((snapshot (expand-file-name "selection.ini" sumi-explorer--session-directory)))
    (if (or (file-exists-p snapshot) (file-symlink-p snapshot))
        (sumi-explorer--selection-snapshot)
      (let ((selected (cdr (assoc "selected" (sumi-explorer-state)))))
        (unless (string-empty-p selected) (list selected))))))

(defun sumi-explorer--submit-paths (command paths &optional directory)
  "Publish an immutable indexed manifest for COMMAND, PATHS and DIRECTORY."
  (unless (member command '("select-many" "copy-many" "move-many" "trash-many" "zip-create" "shell-menu"))
    (error "Unsupported Explorer manifest command"))
  (setq paths (sumi-explorer--validated-paths paths (equal command "select-many")))
  (let ((needs-directory (member command '("copy-many" "move-many" "zip-create"))))
    (if needs-directory
        (progn
          (sumi-explorer--escape directory)
          (unless (sumi-explorer--local-absolute-path-p directory)
            (error "Batch destination must be an absolute local directory")))
      (when directory (error "Unexpected Explorer batch destination")))
    (unless (and (processp sumi-explorer--process) (process-live-p sumi-explorer--process))
      (error "Explorer is not running"))
    (when sumi-explorer--pending (error "Explorer request is pending"))
    (unless (and (integerp sumi-explorer--request-id)
                 (<= 0 sumi-explorer--request-id)
                 (< sumi-explorer--request-id sumi-explorer--maximum-request-id))
      (error "Explorer request identifiers exhausted or invalid"))
    (let* ((id (1+ sumi-explorer--request-id))
           (data (concat
                  (format "[Explorer]\nversion=1\nrequest=%d\ncommand=%s\npath=\ncount=%d\n" id command (length paths))
                  (when needs-directory (concat "destination=" (sumi-explorer--escape directory) "\n"))
                  (mapconcat (lambda (entry)
                               (format "path%d=%s\n" (car entry) (sumi-explorer--escape (cdr entry))))
                             (cl-loop for path in paths for index from 0 collect (cons index path)) ""))))
      (sumi-explorer--publish "request.ini" data
                              (lambda () (setq sumi-explorer--request-id id sumi-explorer--pending id)))
      id)))

;;;###autoload
(defun sumi-explorer-select-files (paths &optional timeout)
  "Select the complete PATHS list, or clear selection with nil, and await."
  (unless (and (or (null timeout) (numberp timeout)) (> (or timeout 5) 0))
    (error "Invalid timeout"))
  (sumi-explorer--submit-paths "select-many" paths)
  (sumi-explorer-await timeout))

;;;###autoload
(defun sumi-explorer-begin-batch (command paths &optional directory)
  "Begin copy, move or trash COMMAND over frozen PATHS; return its pending ID.
Copy/move DIRECTORY is the existing destination directory, checked by the host.
Await the actual result with `sumi-explorer-await', then read
`sumi-explorer-batch-result'."
  (unless (member command '("copy" "move" "trash")) (error "Unsupported Explorer batch"))
  (sumi-explorer--submit-paths (concat command "-many") paths directory))

;;;###autoload
(defun sumi-explorer-batch-result (&optional expected-request)
  "Return strictly validated Batch header fields plus an `items' alist list.
When EXPECTED-REQUEST is non-nil require that positive integer request ID.
This durable result is independent of GUI state and does not resolve a request."
  (when expected-request
    (unless (and (integerp expected-request) (<= 1 expected-request sumi-explorer--maximum-request-id))
      (error "Invalid expected Explorer request")))
  (let* ((groups (sumi-explorer--read-groups "batch-result.ini"))
         (header (cdr (assoc "Batch" groups)))
         (id (sumi-explorer--decimal (cdr (assoc "request" header)) 1 sumi-explorer--maximum-request-id))
         (count (sumi-explorer--decimal (cdr (assoc "count" header)) 1 256)) items)
    (sumi-explorer--exact-keys header '("version" "request" "count"))
    (unless (and (equal (cdr (assoc "version" header)) "1")
                 (= (length groups) (1+ count))
                 (or (null expected-request) (= id expected-request)))
      (error "Invalid or mismatched Explorer batch result"))
    (dotimes (index count)
      (let ((item (cdr (assoc (format "Item%d" index) groups))))
        (sumi-explorer--exact-keys item '("path" "destination" "status" "message"))
        (unless (and (sumi-explorer--local-absolute-path-p (cdr (assoc "path" item)))
                     (or (string-empty-p (cdr (assoc "destination" item)))
                         (sumi-explorer--local-absolute-path-p (cdr (assoc "destination" item))))
                     (<= (length (cdr (assoc "message" item))) 256)
                     (member (cdr (assoc "status" item)) '("ok" "error" "skipped")))
          (error "Invalid Explorer batch item"))
        (push item items)))
    (setq items (nreverse items))
    (let ((paths (mapcar (lambda (item) (cdr (assoc "path" item))) items)))
      ;; A rejected duplicate manifest may truthfully mirror the invalid inputs.
      ;; No successful or partially executed batch may repeat a source identity.
      (when (/= (length paths) (length (delete-dups (copy-sequence paths))))
        (unless (and (= 1 (cl-count "error" items :key (lambda (item) (cdr (assoc "status" item))) :test #'equal))
                     (cl-every (lambda (item) (member (cdr (assoc "status" item)) '("error" "skipped"))) items))
          (error "Repeated Explorer result paths outside preflight rejection"))))
    (append header (list (cons "items" items)))))

(defun sumi-explorer--interactive-batch-paths ()
  "Freeze and validate the complete GUI selection before prompts or confirmation."
  (sumi-explorer--validated-paths (sumi-explorer-selected-files) nil))

(defun sumi-explorer--interactive-batch-transfer (prompt)
  "Freeze selected paths, then read the destination directory for PROMPT."
  (let ((paths (sumi-explorer--interactive-batch-paths)))
    (list paths (read-directory-name (concat prompt " to existing directory: ")
                                     (file-name-directory (car paths)) nil t))))

(defun sumi-explorer--run-batch (command paths directory timeout)
  "Submit COMMAND for PATHS to DIRECTORY, await TIMEOUT, and read its result."
  (unless (and (or (null timeout) (numberp timeout)) (> (or timeout 5) 0))
    (error "Invalid timeout"))
  (let ((id (sumi-explorer-begin-batch command paths directory)))
    (sumi-explorer-await timeout)
    (sumi-explorer-batch-result id)))

;;;###autoload
(defun sumi-explorer-copy-files (paths directory &optional timeout)
  "Copy frozen PATHS into existing DIRECTORY and return the completed result."
  (interactive (sumi-explorer--interactive-batch-transfer "Copy selected files"))
  (sumi-explorer--run-batch "copy" paths directory timeout))

;;;###autoload
(defun sumi-explorer-move-files (paths directory &optional timeout)
  "Move frozen PATHS into existing DIRECTORY and return the completed result."
  (interactive (sumi-explorer--interactive-batch-transfer "Move selected files"))
  (sumi-explorer--run-batch "move" paths directory timeout))

;;;###autoload
(defun sumi-explorer-trash-files (paths &optional timeout)
  "Trash frozen PATHS and return the completed result; confirm interactive calls."
  (interactive
   (let ((paths (sumi-explorer--interactive-batch-paths))
         (print-length nil) (print-level nil) (print-escape-newlines t))
     (unless (y-or-n-p (format "Move these exact paths to Trash? %S " paths))
       (user-error "Batch Trash cancelled"))
     (list paths)))
  (sumi-explorer--run-batch "trash" paths nil timeout))

;;;###autoload
(defun sumi-explorer-tabs ()
  "Read the workspace snapshot as header fields plus a `tabs' alist list.
IDs, count and booleans remain canonical strings.  Only this independently
consistent snapshot is read; no durable response or selection is changed."
  (let* ((groups (sumi-explorer--read-groups "workspace.ini"))
         (header
          (progn
            (when (assoc "WorkspaceError" groups)
              (let ((fields (cdr (assoc "WorkspaceError" groups))))
                (sumi-explorer--exact-keys fields '("version" "message"))
                (unless (and (= (length groups) 1)
                             (equal (cdr (assoc "version" fields)) "1")
                             (<= (length (cdr (assoc "message" fields))) 256))
                  (error "Invalid Explorer workspace error snapshot"))
                (error "Explorer workspace: %s" (cdr (assoc "message" fields)))))
            (cdr (assoc "Workspace" groups))))
         (count (sumi-explorer--decimal (cdr (assoc "count" header)) 1 64))
         (active (cdr (assoc "active" header))) ids tabs)
    (sumi-explorer--exact-keys header '("version" "active" "count"))
    (sumi-explorer--decimal active 1 sumi-explorer--maximum-request-id)
    (unless (and (equal (cdr (assoc "version" header)) "1")
                 (equal (mapcar #'car groups)
                        (cons "Workspace" (cl-loop for index below count collect (format "Tab%d" index)))))
      (error "Invalid Explorer workspace schema or group order"))
    (dotimes (index count)
      (let* ((fields (cdr (assoc (format "Tab%d" index) groups)))
             (id (cdr (assoc "id" fields)))
             (directory (cdr (assoc "directory" fields))))
        (sumi-explorer--exact-keys fields '("id" "directory" "back" "forward"))
        (sumi-explorer--decimal id 1 sumi-explorer--maximum-request-id)
        (unless (and (not (member id ids))
                     (sumi-explorer--local-absolute-path-p directory)
                     (member (cdr (assoc "back" fields)) '("0" "1"))
                     (member (cdr (assoc "forward" fields)) '("0" "1")))
          (error "Invalid Explorer workspace tab"))
        (push id ids)
        (push fields tabs)))
    (unless (member active ids) (error "Explorer active tab is missing"))
    (append header (list (cons "tabs" (nreverse tabs))))))

;;;###autoload
(defun sumi-explorer-new-tab (&optional directory timeout)
  "Open DIRECTORY in a new tab; nil or empty means the current GUI directory."
  (interactive)
  (sumi-explorer-send "new-tab" directory timeout))

;;;###autoload
(defun sumi-explorer-new-window (&optional directory timeout)
  "Launch an independent window in DIRECTORY, or the current GUI directory."
  (interactive)
  (sumi-explorer-send "new-window" directory timeout))

;;;###autoload
(defun sumi-explorer-close-tab (&optional timeout)
  "Close the active tab and await completion; closing the last tab is rejected."
  (interactive)
  (sumi-explorer-send "close-tab" nil timeout))

;;;###autoload
(defun sumi-explorer-activate-tab (id &optional timeout)
  "Activate the positive tab ID, an integer or canonical decimal string."
  (interactive
   (list (completing-read "Activate tab ID: "
                          (mapcar (lambda (tab) (cdr (assoc "id" tab)))
                                  (cdr (assoc "tabs" (sumi-explorer-tabs)))) nil t)))
  (sumi-explorer-send "activate-tab" (if (integerp id) (number-to-string id) id) timeout))

;;;###autoload
(defun sumi-explorer-back (&optional timeout)
  "Navigate back in the active tab's history and await completion."
  (interactive) (sumi-explorer-send "back" nil timeout))

;;;###autoload
(defun sumi-explorer-forward (&optional timeout)
  "Navigate forward in the active tab's history and await completion."
  (interactive) (sumi-explorer-send "forward" nil timeout))

;;;###autoload
(defun sumi-explorer-home (&optional timeout)
  "Navigate the active tab to the host's home directory and await completion."
  (interactive) (sumi-explorer-send "home" nil timeout))

(defun sumi-explorer--snapshot-error (groups name)
  "Raise an explicit bounded NAME error snapshot from GROUPS, if present."
  (when (assoc name groups)
    (let ((fields (cdr (assoc name groups))))
      (sumi-explorer--exact-keys fields '("version" "message"))
      (unless (and (= (length groups) 1) (equal (cdr (assoc "version" fields)) "1")
                   (<= (length (cdr (assoc "message" fields))) 256))
        (error "Invalid Explorer error snapshot"))
      (error "Explorer %s: %s" name (cdr (assoc "message" fields))))))

;;;###autoload
(defun sumi-explorer-view-settings ()
  "Read the last published independent presentation snapshot as string-key fields.
This is not a durable result or a freshness guarantee after an I/O failure."
  (let* ((groups (sumi-explorer--read-groups "view.ini"))
         (fields (cdr (assoc "View" groups))))
    (sumi-explorer--snapshot-error groups "ViewError")
    (sumi-explorer--exact-keys fields '("version" "directory" "filter" "sort" "order" "hidden" "view"))
    (unless (and (equal (mapcar #'car groups) '("View"))
                 (equal (cdr (assoc "version" fields)) "1")
                 (sumi-explorer--local-absolute-path-p (cdr (assoc "directory" fields)))
                 (<= (length (cdr (assoc "filter" fields))) 256)
                 (member (cdr (assoc "sort" fields)) '("name" "size" "date" "type"))
                 (member (cdr (assoc "order" fields)) '("asc" "desc"))
                 (member (cdr (assoc "hidden" fields)) '("0" "1"))
                 (member (cdr (assoc "view" fields)) '("list" "details")))
      (error "Invalid Explorer presentation snapshot"))
    fields))

;;;###autoload
(defun sumi-explorer-bookmarks ()
  "Read the last published bookmark header plus ordered paths under `bookmarks'."
  (let* ((groups (sumi-explorer--read-groups "bookmarks.ini"))
         (_error (sumi-explorer--snapshot-error groups "BookmarksError"))
         (header (cdr (assoc "Bookmarks" groups)))
         (count (sumi-explorer--decimal (cdr (assoc "count" header)) 0 64)) paths)
    (sumi-explorer--exact-keys header '("version" "count"))
    (unless (and (equal (cdr (assoc "version" header)) "1")
                 (equal (mapcar #'car groups)
                        (cons "Bookmarks" (cl-loop for index below count collect (format "Bookmark%d" index)))))
      (error "Invalid Explorer bookmarks snapshot"))
    (dotimes (index count)
      (let ((fields (cdr (assoc (format "Bookmark%d" index) groups))))
        (sumi-explorer--exact-keys fields '("path"))
        (push (cdr (assoc "path" fields)) paths)))
    (setq paths (sumi-explorer--validated-paths (nreverse paths) t))
    (append header (list (cons "bookmarks" paths)))))

;;;###autoload
(defun sumi-explorer-filter (text &optional timeout)
  "Set literal filename filter TEXT and return its standard acknowledgement."
  (interactive (list (read-string "Filename filter: ")))
  (sumi-explorer-send "filter" text timeout))

;;;###autoload
(defun sumi-explorer-sort (key &optional timeout)
  "Set the active tab's sort KEY: name, size, date or type."
  (interactive (list (completing-read "Sort by: " '("name" "size" "date" "type") nil t)))
  (sumi-explorer-send "sort" key timeout))

;;;###autoload
(defun sumi-explorer-order (order &optional timeout)
  "Set the active tab's sort ORDER: asc or desc."
  (interactive (list (completing-read "Sort order: " '("asc" "desc") nil t)))
  (sumi-explorer-send "order" order timeout))

;;;###autoload
(defun sumi-explorer-show-hidden (show &optional timeout)
  "Show hidden files when SHOW is t, or hide them when SHOW is nil."
  (interactive (list (y-or-n-p "Show hidden files? ")))
  (unless (memq show '(t nil)) (error "Hidden visibility requires t or nil"))
  (sumi-explorer-send "hidden" (if show "show" "hide") timeout))

;;;###autoload
(defun sumi-explorer-set-view (view &optional timeout)
  "Set the active tab's VIEW to list or details."
  (interactive (list (completing-read "File view: " '("list" "details") nil t)))
  (sumi-explorer-send "view" view timeout))

;;;###autoload
(defun sumi-explorer-bookmark-add (&optional directory timeout)
  "Save DIRECTORY as a bookmark; nil means the active directory."
  (interactive) (sumi-explorer-send "bookmark-add" directory timeout))

;;;###autoload
(defun sumi-explorer-bookmark-remove (&optional directory timeout)
  "Remove DIRECTORY from bookmarks; nil means the active directory."
  (interactive) (sumi-explorer-send "bookmark-remove" directory timeout))

;;;###autoload
(defun sumi-explorer-bookmark-open (directory &optional timeout)
  "Open the saved absolute local bookmark DIRECTORY."
  (interactive (list (completing-read "Open bookmark: "
                                     (cdr (assoc "bookmarks" (sumi-explorer-bookmarks))) nil t)))
  (sumi-explorer-send "bookmark-open" directory timeout))

;;;###autoload
(defun sumi-explorer-clipboard-copy (&optional timeout)
  "Freeze the visible GUI selection into the system COPY clipboard."
  (interactive) (sumi-explorer-send "clipboard-copy" nil timeout))

;;;###autoload
(defun sumi-explorer-clipboard-cut (&optional timeout)
  "Freeze the visible GUI selection into the host-owned CUT clipboard."
  (interactive) (sumi-explorer-send "clipboard-cut" nil timeout))

;;;###autoload
(defun sumi-explorer-paste (&optional directory timeout)
  "Paste into DIRECTORY, or the active directory, awaiting actual completion.
Timeout and quit retain the same pending request; `sumi-explorer-await' resumes."
  (interactive) (sumi-explorer-send "paste" directory timeout))

(defun sumi-explorer-undo-history ()
  "Read the last published bounded session undo snapshot.
Return string-keyed header fields plus ordered group alists under `groups'.
This snapshot does not establish that a reversal will still be safe."
  (let* ((groups (sumi-explorer--read-groups "undo.ini"))
         (header (cdr (assoc "Undo" groups))) result
         (seen (make-hash-table :test 'equal)))
    (sumi-explorer--snapshot-error groups "UndoError")
    (sumi-explorer--exact-keys header '("version" "count"))
    (unless (equal (cdr (assoc "version" header)) "1") (error "Invalid undo version"))
    (let ((count (sumi-explorer--decimal (cdr (assoc "count" header)) 0 32)))
      (unless (equal (mapcar #'car groups)
                     (cons "Undo" (cl-loop for index below count collect (format "Undo%d" index))))
        (error "Invalid undo group count or order"))
      (dotimes (index count)
        (let* ((fields (cdr (assoc (format "Undo%d" index) groups)))
               (id (cdr (assoc "id" fields))))
          (sumi-explorer--exact-keys fields '("id" "operation" "count"))
          (sumi-explorer--decimal id 1 sumi-explorer--maximum-request-id)
          (sumi-explorer--decimal (cdr (assoc "count" fields)) 1 256)
          (unless (and (not (gethash id seen))
                       (member (cdr (assoc "operation" fields))
                               '("copy" "move" "rename" "mkdir" "trash" "zip-create" "zip-extract" "chmod" "transfer")))
            (error "Invalid undo group"))
          (puthash id t seen)
          (push fields result)))
      (append header (list (cons "groups" (nreverse result)))))))

(defun sumi-explorer-undo (&optional group-id timeout)
  "Reverse GROUP-ID, or the newest group from one snapshot, awaiting TIMEOUT.
Stale identifiers fail without retrying another group."
  (interactive)
  (let ((id (or group-id
                (cdr (assoc "id" (car (cdr (assoc "groups" (sumi-explorer-undo-history)))))))))
    (when (integerp id) (setq id (number-to-string id)))
    (sumi-explorer--decimal id 1 sumi-explorer--maximum-request-id)
    (sumi-explorer-send "undo" id timeout)))

(defun sumi-explorer--advanced-manifest (command paths destination timeout)
  "Submit frozen PATHS for COMMAND to DESTINATION and await actual completion."
  (unless (and (or (null timeout) (numberp timeout)) (> (or timeout 5) 0))
    (error "Invalid timeout"))
  (sumi-explorer--submit-paths command paths destination)
  (sumi-explorer-await timeout))

(defun sumi-explorer--interactive-archive ()
  "Freeze selected input paths before prompting for the exact new archive."
  (let ((files (sumi-explorer--interactive-batch-paths)))
    (list (read-file-name "Create ZIP at new path: " (file-name-directory (car files))) files)))

(defun sumi-explorer--advanced-single-target (&optional prompt)
  "Freeze exactly one selected path, otherwise prompt or use the GUI directory.
PROMPT requests a local file when no selection exists."
  (let ((paths (sumi-explorer-selected-files)))
    (when (> (length paths) 1) (user-error "This operation requires one target"))
    (car (sumi-explorer--validated-paths
          (list (or (car paths)
                    (if prompt (read-file-name prompt nil nil t)
                      (cdr (assoc "directory" (sumi-explorer-state)))))) nil))))

;;;###autoload
(defun sumi-explorer-zip-create (archive &optional files timeout)
  "Create exact unused ARCHIVE from frozen FILES or the GUI selection.
Await actual engine completion; timeout and quit retain the pending request."
  (interactive (sumi-explorer--interactive-archive))
  (sumi-explorer--advanced-manifest "zip-create"
                                    (sumi-explorer--validated-paths
                                     (or files (sumi-explorer-selected-files)) nil)
                                    archive timeout))

;;;###autoload
(defun sumi-explorer-zip-extract (archive destination &optional timeout)
  "Extract ARCHIVE into exact unused DESTINATION, awaiting TIMEOUT."
  (interactive (let ((source (sumi-explorer--advanced-single-target "Extract ZIP: ")))
                 (list source (read-file-name "Extract to new directory: " (file-name-directory source)))))
  (unless (and (or (null timeout) (numberp timeout)) (> (or timeout 5) 0)) (error "Invalid timeout"))
  (sumi-explorer--submit "zip-extract" archive destination)
  (sumi-explorer-await timeout))

;;;###autoload
(defun sumi-explorer-shell-menu (&optional paths timeout)
  "Invoke the Windows Shell menu on frozen PATHS, selection or current directory.
An acknowledgement establishes invocation, never app-managed mutation or undo."
  (interactive)
  (let ((targets (or paths (sumi-explorer-selected-files)
                     (list (cdr (assoc "directory" (sumi-explorer-state)))))))
    (sumi-explorer--advanced-manifest "shell-menu" targets nil timeout)))

;;;###autoload
(defun sumi-explorer-permissions (&optional path timeout)
  "Open the platform permission editor for one frozen PATH.
Acknowledgement means invocation, not that an ACL change was applied."
  (interactive (list (sumi-explorer--advanced-single-target)))
  (sumi-explorer-send "permissions"
                       (or path (sumi-explorer--advanced-single-target)) timeout))

;;;###autoload
(defun sumi-explorer-set-permissions (path mode &optional timeout)
  "Set local PATH to octal string MODE (0000 through 0777), awaiting TIMEOUT.
The host rejects symlinks and unsupported platforms."
  (interactive (let ((path (sumi-explorer--advanced-single-target "Set permissions for: ")))
                 (list path (read-string "Octal mode (0000..0777): "))))
  (unless (and (or (null timeout) (numberp timeout)) (> (or timeout 5) 0)) (error "Invalid timeout"))
  (sumi-explorer--submit "chmod" path mode)
  (sumi-explorer-await timeout))

(defun sumi-explorer-open-directory (directory)
  "Navigate to DIRECTORY."
  (interactive "DDirectory: ") (sumi-explorer-send "open" directory))
(defun sumi-explorer-select (path)
  "Select PATH."
  (interactive "fSelect: ") (sumi-explorer-send "select" path))
(defun sumi-explorer-properties (&optional path)
  "Show portable application properties for PATH or current selection."
  (interactive) (sumi-explorer-send "properties" path))
(defun sumi-explorer-refresh ()
  "Refresh Explorer."
  (interactive) (sumi-explorer-send "refresh"))
(defun sumi-explorer-stop (&optional force)
  "Request graceful exit and clean up after exit.
With FORCE (interactive prefix), forcibly dispose of the owned process.
Unresolved requests and slow operations retain their process and session."
  (interactive "P")
  (when (and (processp sumi-explorer--owned-process)
             (process-live-p sumi-explorer--owned-process))
    (unless (eq sumi-explorer--process sumi-explorer--owned-process)
      (error "Explorer ownership changed; cleanup pending"))
    (if force
        (delete-process sumi-explorer--owned-process)
      (when sumi-explorer--pending
        (let ((response (and (file-exists-p (expand-file-name "response.ini" sumi-explorer--session-directory))
                             (sumi-explorer--read-state "response.ini"))))
          (unless (and response
                       (= (string-to-number (cdr (assoc "request" response))) sumi-explorer--pending))
            (error "Explorer request unresolved; cleanup pending"))
          (setq sumi-explorer--pending nil)
          (when (equal (cdr (assoc "status" response)) "error")
            (error "Explorer: %s; cleanup pending" (cdr (assoc "message" response))))))
      (unless sumi-explorer--quit-requested
        (condition-case err
            (sumi-explorer-send "quit" nil sumi-explorer--stop-timeout)
          (error (error "%s; cleanup pending" (error-message-string err)))))
      (let ((deadline (+ (float-time) sumi-explorer--stop-timeout)))
        (while (and (process-live-p sumi-explorer--owned-process)
                    (< (float-time) deadline))
          (accept-process-output sumi-explorer--owned-process 0.02)))
      (when (process-live-p sumi-explorer--owned-process)
        (error "Explorer is still finishing; cleanup pending"))))
  (when (and sumi-explorer--owned-session
             (equal sumi-explorer--session-directory sumi-explorer--owned-session)
             (file-directory-p sumi-explorer--owned-session))
    (delete-directory sumi-explorer--owned-session t))
  (setq sumi-explorer--process nil sumi-explorer--session-directory nil
        sumi-explorer--owned-session nil sumi-explorer--owned-process nil
        sumi-explorer--pending nil sumi-explorer--quit-requested nil))

;;;###autoload
(defun sumi-explorer-begin-search (query)
  "Begin asynchronous literal filename QUERY below the current GUI directory.
The acknowledgement only confirms launch; use `sumi-explorer-search-results'
for completion, bounds, cancellation, and inaccessible-directory warnings."
  (interactive (list (read-string "Search subfolders: ")))
  (sumi-explorer--submit "search" query))

;;;###autoload
(defun sumi-explorer-search-results ()
  "Read one bounded, strictly decoded recursive-search snapshot.
Return string-key header fields plus `results', an ordered list of full paths.
Reading a snapshot does not establish freshness after a publication failure."
  (let* ((groups (sumi-explorer--read-groups "search.ini"))
         (header (cdr (assoc "Search" groups)))
         (count (sumi-explorer--decimal (cdr (assoc "count" header)) 0 2000))
         (root (cdr (assoc "root" header)))
         (query (cdr (assoc "query" header)))
         (chunks (/ (+ count 249) 250)) paths)
    (sumi-explorer--exact-keys header '("version" "request" "root" "query" "status" "count" "warnings" "visited" "truncated"))
    (unless (and (equal (cdr (assoc "version" header)) "1")
                 (sumi-explorer--local-absolute-path-p root)
                 (<= 1 (length query) 256)
                 (member (cdr (assoc "status" header)) '("running" "complete" "cancelled"))
                 (member (cdr (assoc "truncated" header)) '("0" "1"))
                 (equal (mapcar #'car groups)
                        (cons "Search" (cl-loop for i below chunks collect (format "Search%d" i)))))
      (error "Invalid Explorer search snapshot"))
    (sumi-explorer--decimal (cdr (assoc "request" header)) 0 sumi-explorer--maximum-request-id)
    (sumi-explorer--decimal (cdr (assoc "visited" header)) 0 100000)
    (sumi-explorer--decimal (cdr (assoc "warnings" header)) 0 200002)
    (when (and (equal (cdr (assoc "status" header)) "running")
               (not (and (= count 0) (equal (cdr (assoc "visited" header)) "0")
                         (equal (cdr (assoc "warnings" header)) "0")
                         (equal (cdr (assoc "truncated" header)) "0"))))
      (error "Invalid running Explorer search snapshot"))
    (dotimes (chunk chunks)
      (let* ((fields (cdr (assoc (format "Search%d" chunk) groups)))
             (size (min 250 (- count (* chunk 250)))))
        (sumi-explorer--exact-keys fields (cl-loop for i below size collect (format "path%d" i)))
        (dotimes (index size)
          (let ((path (cdr (assoc (format "path%d" index) fields))))
            (unless (and (sumi-explorer--local-absolute-path-p path)
                         (string-prefix-p (file-name-as-directory root) path)
                         (equal path (directory-file-name (expand-file-name path))))
              (error "Invalid Explorer search result path"))
            (push path paths)))))
    (setq paths (nreverse paths))
    (unless (= (length paths) (length (delete-dups (copy-sequence paths))))
      (error "Duplicate Explorer search result"))
    (append header (list (cons "results" paths)))))

;;;###autoload
(defun sumi-explorer-search (query &optional timeout)
  "Search current-directory descendants for literal QUERY and await TIMEOUT.
TIMEOUT defaults to 30 seconds. A timeout leaves the running search available
for `sumi-explorer-search-results' and `sumi-explorer-cancel-search'."
  (interactive (list (read-string "Search subfolders: ")))
  (setq timeout (or timeout 30))
  (unless (and (numberp timeout) (> timeout 0)) (error "Invalid search timeout"))
  (let ((id (sumi-explorer-begin-search query)) (deadline (+ (float-time) timeout)) result)
    (sumi-explorer-await (max 0.01 (- deadline (float-time))))
    (while (progn
             (setq result (sumi-explorer-search-results))
             (unless (equal (cdr (assoc "request" result)) (number-to-string id))
               (error "Explorer search was superseded"))
             (equal (cdr (assoc "status" result)) "running"))
      (when (>= (float-time) deadline) (error "Explorer search timed out; search remains available"))
      (unless (process-live-p sumi-explorer--process) (error "Explorer exited during search"))
      (accept-process-output sumi-explorer--process 0.02))
    result))

;;;###autoload
(defun sumi-explorer-open-search-result (path &optional timeout)
  "Navigate to the containing folder of current search result PATH and select it.
The GUI clears its current filename filter and shows hidden files so the exact
result can be selected. Missing or obsolete results produce an explicit error."
  (interactive
   (list (completing-read "Open containing folder: "
                          (cdr (assoc "results" (sumi-explorer-search-results))) nil t)))
  (sumi-explorer-send "search-open" path timeout))

;;;###autoload
(defun sumi-explorer-cancel-search (&optional timeout)
  "Request cancellation of the active recursive search and await acknowledgement.
The independent search snapshot reaches `cancelled' after its worker stops."
  (interactive) (sumi-explorer-send "search-cancel" "" timeout))

(provide 'sumi-explorer)
;;; sumi-explorer.el ends here
