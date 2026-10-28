;;; sumi-explorer-client-test.el --- Client tests -*- lexical-binding: t; -*-
(require 'ert)
(require 'sumi-explorer)

(ert-deftest sumi-explorer-presentation-clipboard-api ()
  (dolist (function '(sumi-explorer-view-settings sumi-explorer-bookmarks))
    (should (fboundp function)))
  (dolist (command '(sumi-explorer-filter sumi-explorer-sort sumi-explorer-order
                     sumi-explorer-show-hidden sumi-explorer-set-view
                     sumi-explorer-bookmark-add sumi-explorer-bookmark-remove
                     sumi-explorer-bookmark-open sumi-explorer-clipboard-copy
                     sumi-explorer-clipboard-cut sumi-explorer-paste))
    (should (commandp command))))

(ert-deftest sumi-explorer-workspace-api ()
  (should (fboundp 'sumi-explorer-tabs))
  (dolist (command '(sumi-explorer-new-tab sumi-explorer-new-window
                     sumi-explorer-close-tab sumi-explorer-activate-tab
                     sumi-explorer-back sumi-explorer-forward sumi-explorer-home))
    (should (commandp command))))

(ert-deftest sumi-explorer-batch-api ()
  (dolist (function '(sumi-explorer-select-files sumi-explorer-begin-batch
                      sumi-explorer-batch-result sumi-explorer-copy-files
                      sumi-explorer-move-files sumi-explorer-trash-files))
    (should (fboundp function))))

(ert-deftest sumi-explorer-operation-api ()
  (dolist (function '(sumi-explorer-begin-operation sumi-explorer-await
                      sumi-explorer-cancel sumi-explorer-copy sumi-explorer-move
                      sumi-explorer-rename sumi-explorer-trash sumi-explorer-mkdir))
    (should (fboundp function))))

(ert-deftest sumi-explorer-mutations-are-interactive ()
  (dolist (command '(sumi-explorer-copy sumi-explorer-move sumi-explorer-rename
                     sumi-explorer-trash sumi-explorer-mkdir sumi-explorer-await))
    (should (commandp command))))

(ert-deftest sumi-explorer-interactive-mkdir-defaults-to-gui-directory ()
  (let ((directory (make-temp-file "sumi-gui-directory-" t)) prompted-directory submission)
    (unwind-protect
        (cl-letf (((symbol-function 'sumi-explorer-state)
                   (lambda () (list (cons "directory" directory))))
                  ((symbol-function 'read-file-name)
                   (lambda (_prompt initial-directory &rest _)
                     (setq prompted-directory initial-directory)
                     (expand-file-name "new" initial-directory)))
                  ((symbol-function 'sumi-explorer--run-operation)
                   (lambda (&rest arguments) (setq submission arguments))))
          (call-interactively #'sumi-explorer-mkdir)
          (should (equal prompted-directory (file-name-as-directory directory)))
          (should (equal submission (list "mkdir" (expand-file-name "new" directory) nil nil)))
          (cl-letf (((symbol-function 'sumi-explorer-state) (lambda () (error "Unavailable"))))
            (call-interactively #'sumi-explorer-mkdir)
            (should (equal prompted-directory default-directory)))
          (cl-letf (((symbol-function 'sumi-explorer-state)
                     (lambda () '(("directory" . "/ssh:host:/tmp")))))
            (call-interactively #'sumi-explorer-mkdir)
            (should (equal prompted-directory default-directory))))
      (delete-directory directory))))

(ert-deftest sumi-explorer-interactive-selected-source-is-frozen ()
  (let ((selected "/tmp/selected 日本語\n source") submissions prompts)
    (cl-letf (((symbol-function 'sumi-explorer-selected-files) (lambda () (list selected)))
              ((symbol-function 'read-file-name)
               (lambda (prompt &rest _)
                 (push prompt prompts)
                 ;; A native selection change while prompting must not retarget.
                 (setq selected "/tmp/different selection")
                 "/tmp/new destination"))
              ((symbol-function 'sumi-explorer--run-operation)
               (lambda (&rest arguments) (push arguments submissions))))
      (call-interactively #'sumi-explorer-copy)
      (should (equal (car submissions) '("copy" "/tmp/selected 日本語\n source" "/tmp/new destination" nil)))
      (should (= (length prompts) 1))
      (setq selected "/tmp/move selection")
      (call-interactively #'sumi-explorer-move)
      (should (equal (car submissions) '("move" "/tmp/move selection" "/tmp/new destination" nil)))
      (setq selected "/tmp/rename selection")
      (cl-letf (((symbol-function 'read-string) (lambda (&rest _) "renamed 日本語")))
        (call-interactively #'sumi-explorer-rename))
      (should (equal (car submissions) '("rename" "/tmp/rename selection" "/tmp/renamed 日本語" nil))))))

(ert-deftest sumi-explorer-interactive-prompts-without-selection ()
  (let ((answers '("/tmp/prompted source" "/tmp/prompted destination" "/tmp/new directory"))
        submissions)
    (cl-letf (((symbol-function 'sumi-explorer-selected-files) (lambda () nil))
              ((symbol-function 'read-file-name) (lambda (&rest _) (pop answers)))
              ((symbol-function 'sumi-explorer--run-operation)
               (lambda (&rest arguments) (push arguments submissions))))
      (call-interactively #'sumi-explorer-copy)
      (should (equal (car submissions) '("copy" "/tmp/prompted source" "/tmp/prompted destination" nil)))
      (call-interactively #'sumi-explorer-mkdir)
      (should (equal (car submissions) '("mkdir" "/tmp/new directory" nil nil)))
      (should-not answers))))

(ert-deftest sumi-explorer-interactive-trash-confirms-frozen-target ()
  (let ((selected "/tmp/confirmed target") submission)
    (cl-letf (((symbol-function 'sumi-explorer-selected-files) (lambda () (list selected)))
              ((symbol-function 'y-or-n-p)
               (lambda (_prompt) (setq selected "/tmp/later selection") t))
              ((symbol-function 'sumi-explorer--run-operation)
               (lambda (&rest arguments) (setq submission arguments))))
      (call-interactively #'sumi-explorer-trash)
      (should (equal submission '("trash" "/tmp/confirmed target" nil nil))))))

(defconst sumi-explorer-test--state
  "[Explorer]\nversion=1\nrequest=0\nstatus=ok\ndirectory=/tmp\nselected=\nmessage=\n")
(defmacro sumi-explorer-test--session (&rest body)
  `(let* ((sumi-explorer--session-directory (make-temp-file "sumi-test-" t))
          (sumi-explorer--owned-session nil)
          (sumi-explorer--process nil)
          (sumi-explorer--owned-process nil)
          (sumi-explorer--request-id 0)
          (sumi-explorer--quit-requested nil)
          (sumi-explorer--pending nil))
     (unwind-protect (progn ,@body)
       (when (process-live-p sumi-explorer--process)
         (delete-process sumi-explorer--process))
       (delete-directory sumi-explorer--session-directory t))))
(defun sumi-explorer-test--write (text)
  (let ((coding-system-for-write 'utf-8-unix))
    (write-region text nil (expand-file-name "state.ini" sumi-explorer--session-directory) nil 'silent)))
(defun sumi-explorer-test--response (text)
  (let ((coding-system-for-write 'utf-8-unix))
    (write-region text nil (expand-file-name "response.ini" sumi-explorer--session-directory) nil 'silent)))

(defun sumi-explorer-test--contents (filename)
  (with-temp-buffer
    (insert-file-contents (expand-file-name filename sumi-explorer--session-directory))
    (buffer-string)))

(defun sumi-explorer-test--sidecar (filename data)
  (let ((coding-system-for-write 'utf-8-unix))
    (write-region data nil (expand-file-name filename sumi-explorer--session-directory) nil 'silent)))

(defconst sumi-explorer-test--view
  "[View]\nversion=1\ndirectory=/tmp\nfilter=\nsort=name\norder=asc\nhidden=0\nview=list\n")

(ert-deftest sumi-explorer-presentation-snapshot-validation ()
  (sumi-explorer-test--session
   (let* ((text " 日本語;\\\n\t ")
          (good (string-replace "filter=" (concat "filter=" (sumi-explorer--escape text)) sumi-explorer-test--view)))
     (sumi-explorer-test--sidecar "view.ini" good)
     (should (equal (cdr (assoc "filter" (sumi-explorer-view-settings))) text))
     (dolist (bad (list (concat good "sort=name\n") (concat good "unknown=x\n")
                       (concat good "[View]\n") (concat good "[Other]\n")
                       (string-replace "version=1" "version=2" good)
                       (string-replace "directory=/tmp" "directory=relative" good)
                       (string-replace "sort=name" "sort=invalid" good)
                       (string-replace "order=asc" "order=invalid" good)
                       (string-replace "hidden=0" "hidden=show" good)
                       (string-replace "view=list" "view=invalid" good)
                       (string-replace "filter=" (concat "filter=" (make-string 257 ?界)) sumi-explorer-test--view)
                       (string-replace "filter=" "filter=\\q" sumi-explorer-test--view)
                       (make-string 1048577 ?x)))
       (sumi-explorer-test--sidecar "view.ini" bad)
       (should-error (sumi-explorer-view-settings))))))

(ert-deftest sumi-explorer-presentation-bookmark-error-snapshots ()
  (sumi-explorer-test--session
   (dolist (spec '(("view.ini" "ViewError" sumi-explorer-view-settings)
                   ("bookmarks.ini" "BookmarksError" sumi-explorer-bookmarks)))
     (let ((good (format "[%s]\nversion=1\nmessage=unavailable 日本語\n" (nth 1 spec))))
       (sumi-explorer-test--sidecar (car spec) good)
       (should (string-match-p "unavailable 日本語"
                               (condition-case err (funcall (nth 2 spec)) (error (error-message-string err)))))
       (dolist (bad (list (concat good "extra=x\n") (concat good "[Other]\n")
                         (string-replace "version=1" "version=2" good)
                         (format "[%s]\nversion=1\nmessage=%s\n" (nth 1 spec) (make-string 257 ?界))))
         (sumi-explorer-test--sidecar (car spec) bad)
         (should-error (funcall (nth 2 spec))))))))

(defun sumi-explorer-test--bookmark-data (paths)
  (concat (format "[Bookmarks]\nversion=1\ncount=%d\n" (length paths))
          (mapconcat (lambda (item) (format "[Bookmark%d]\npath=%s\n" (car item) (sumi-explorer--escape (cdr item))))
                     (cl-loop for path in paths for index from 0 collect (cons index path)) "")))

(ert-deftest sumi-explorer-bookmark-snapshot-validation ()
  (sumi-explorer-test--session
   (let* ((paths '("/tmp/日本語;\\\n one" "/tmp/two"))
          (good (sumi-explorer-test--bookmark-data paths)))
     (sumi-explorer-test--sidecar "bookmarks.ini" good)
     (should (equal (cdr (assoc "bookmarks" (sumi-explorer-bookmarks))) paths))
     (dolist (bad (list (concat good "extra=x\n") (concat good "[Other]\n")
                       (string-replace "version=1" "version=2" good)
                       (string-replace "count=2" "count=65" good)
                       (string-replace "count=2" "count=02" good)
                       (string-replace "Bookmark1" "Bookmark01" good)
                       (string-replace "path=/tmp/two" "path=relative" good)
                       (sumi-explorer-test--bookmark-data '("/tmp/one" "/tmp/one"))
                       (sumi-explorer-test--bookmark-data '("/ssh:host:/remote"))
                       (make-string 1048577 ?x)))
       (sumi-explorer-test--sidecar "bookmarks.ini" bad)
       (should-error (sumi-explorer-bookmarks))))
   (let ((paths (cl-loop for index below 64 collect (format "/tmp/dir%d" index))))
     (sumi-explorer-test--sidecar "bookmarks.ini" (sumi-explorer-test--bookmark-data paths))
     (should (equal (cdr (assoc "bookmarks" (sumi-explorer-bookmarks))) paths)))
   (sumi-explorer-test--sidecar "bookmarks.ini" (sumi-explorer-test--bookmark-data nil))
   (should-not (cdr (assoc "bookmarks" (sumi-explorer-bookmarks))))))

(ert-deftest sumi-explorer-presentation-clipboard-wire-contract ()
  (sumi-explorer-test--session
   (setq sumi-explorer--process (make-pipe-process :name "sumi-test" :noquery t))
   (dolist (operation '((sumi-explorer-filter "literal / not a path 日本語\n" "filter" "literal / not a path 日本語\n")
                        (sumi-explorer-sort "size" "sort" "size")
                        (sumi-explorer-order "desc" "order" "desc")
                        (sumi-explorer-show-hidden t "hidden" "show")
                        (sumi-explorer-show-hidden nil "hidden" "hide")
                        (sumi-explorer-set-view "details" "view" "details")
                        (sumi-explorer-bookmark-add nil "bookmark-add" "")
                        (sumi-explorer-bookmark-remove nil "bookmark-remove" "")
                        (sumi-explorer-bookmark-open "/tmp/bookmark" "bookmark-open" "/tmp/bookmark")
                        (sumi-explorer-clipboard-copy nil "clipboard-copy" "")
                        (sumi-explorer-clipboard-cut nil "clipboard-cut" "")
                        (sumi-explorer-paste nil "paste" "")
                        (sumi-explorer-paste "/tmp/destination" "paste" "/tmp/destination")))
     (let ((id (1+ sumi-explorer--request-id)))
       (sumi-explorer-test--response (string-replace "request=0" (format "request=%d" id) sumi-explorer-test--state))
       (if (memq (car operation) '(sumi-explorer-clipboard-copy sumi-explorer-clipboard-cut))
           (funcall (car operation) 0.1)
         (funcall (car operation) (nth 1 operation) 0.1))
       (should (equal (sumi-explorer-test--contents "request.ini")
                      (format "[Explorer]\nversion=1\nrequest=%d\ncommand=%s\npath=%s\n"
                              id (nth 2 operation) (sumi-explorer--escape (nth 3 operation)))))))))

(ert-deftest sumi-explorer-presentation-clipboard-reject-before-publication ()
  (sumi-explorer-test--session
   (setq sumi-explorer--process (make-pipe-process :name "sumi-test" :noquery t))
   (dolist (request '(("sort" "invalid") ("order" "invalid") ("hidden" "1")
                      ("view" "invalid") ("bookmark-open" "") ("bookmark-add" "relative")
                      ("bookmark-remove" "/ssh:host:/remote") ("clipboard-copy" "/tmp")
                      ("clipboard-cut" "1") ("paste" "relative") ("paste" "/ssh:host:/remote")))
     (should-error (apply #'sumi-explorer-send (append request '(0.1)))))
   (should-error (sumi-explorer-show-hidden 'show 0.1))
   (should-error (sumi-explorer-filter (make-string 257 ?界) 0.1))
   (dolist (character '(0 #xd800 #x110000))
     (should-error (sumi-explorer-filter (string character) 0.1)))
   (should-not sumi-explorer--pending)
   (should (= 0 sumi-explorer--request-id))
   (should-not (file-exists-p (expand-file-name "request.ini" sumi-explorer--session-directory)))))

(ert-deftest sumi-explorer-paste-awaits-real-completion ()
  (sumi-explorer-test--session
   (setq sumi-explorer--process (make-pipe-process :name "sumi-test" :noquery t))
   (should-error (sumi-explorer-paste nil 0.01))
   (should (= 1 sumi-explorer--pending))
   (let ((request (sumi-explorer-test--contents "request.ini")))
     (should-error (sumi-explorer-clipboard-copy 0.1))
     (should (equal request (sumi-explorer-test--contents "request.ini")))
     (sumi-explorer-test--response
      (string-replace "status=ok" "status=error" (string-replace "request=0" "request=1" sumi-explorer-test--state)))
     (should-error (sumi-explorer-await 0.1))
     (should-not sumi-explorer--pending)
     (should (= 1 sumi-explorer--request-id)))))

(defun sumi-explorer-test--workspace (count)
  (concat (format "[Workspace]\nversion=1\nactive=1\ncount=%d\n" count)
          (mapconcat (lambda (index)
                       (format "[Tab%d]\nid=%d\ndirectory=%s\nback=0\nforward=1\n"
                               index (1+ index) (sumi-explorer--escape (format "/tmp/日本語;\\\n tab%d" index))))
                     (number-sequence 0 (1- count)) "")))

(ert-deftest sumi-explorer-workspace-snapshot-roundtrip ()
  (sumi-explorer-test--session
   (should-error (sumi-explorer-tabs))
   (sumi-explorer-test--sidecar "workspace.ini" (sumi-explorer-test--workspace 64))
   (let ((workspace (sumi-explorer-tabs)))
     (should (equal (cdr (assoc "active" workspace)) "1"))
     (should (= 64 (length (cdr (assoc "tabs" workspace)))))
     (should (equal (cdr (assoc "directory" (car (cdr (assoc "tabs" workspace))))) "/tmp/日本語;\\\n tab0")))
   (should-not sumi-explorer--pending)
   (should-not (file-exists-p (expand-file-name "request.ini" sumi-explorer--session-directory)))))

(ert-deftest sumi-explorer-workspace-malformed-snapshots ()
  (sumi-explorer-test--session
   (let ((good (sumi-explorer-test--workspace 2)))
     (dolist (bad (list (concat good "extra=x\n") (concat good "id=2\n")
                       (concat good "[Tab1]\n") (concat good "[Other]\n")
                       (string-replace "version=1" "version=2" good)
                       (string-replace "active=1" "active=3" good)
                       (string-replace "active=1" "active=01" good)
                       (string-replace "count=2" "count=0" good)
                       (string-replace "count=2" "count=65" good)
                       (string-replace "id=2" "id=1" good)
                       (string-replace "id=2" "id=9223372036854775808" good)
                       (string-replace "id=2" "id=0" good)
                       (string-replace "back=0" "back=true" good)
                       (string-replace "forward=1" "forward=2" good)
                       (string-replace "[Tab1]" "[Tab01]" good)
                       (string-replace "directory=/tmp/" "directory=relative/" good)
                       (string-replace "directory=/tmp/" "directory=/ssh:host:/" good)
                       (string-replace "directory=/tmp/" "directory=/tmp/\\q" good)
                       (string-replace "directory=/tmp/" "directory=/tmp/\0" good)
                       (make-string 1048577 ?x)))
       (sumi-explorer-test--sidecar "workspace.ini" bad)
       (should-error (sumi-explorer-tabs))))
   (sumi-explorer-test--sidecar "workspace.ini"
    "[Tab0]\nid=1\ndirectory=/tmp\nback=0\nforward=0\n[Workspace]\nversion=1\nactive=1\ncount=1\n")
   (should-error (sumi-explorer-tabs))
   (let ((coding-system-for-write 'no-conversion))
     (write-region (concat "[Workspace]\nversion=1\nactive=1\ncount=1\n[Tab0]\nid=1\ndirectory=/tmp/"
                           (unibyte-string 255) "\nback=0\nforward=0\n") nil
                   (expand-file-name "workspace.ini" sumi-explorer--session-directory) nil 'silent))
   (should-error (sumi-explorer-tabs))))

(ert-deftest sumi-explorer-workspace-command-wire-contract ()
  (sumi-explorer-test--session
   (setq sumi-explorer--process (make-pipe-process :name "sumi-test" :noquery t))
   (dolist (operation '((sumi-explorer-new-tab nil "new-tab" "")
                        (sumi-explorer-new-window "/tmp/日本語\n dir" "new-window" "/tmp/日本語\n dir")
                        (sumi-explorer-activate-tab 7 "activate-tab" "7")
                        (sumi-explorer-close-tab nil "close-tab" "")
                        (sumi-explorer-back nil "back" "")
                        (sumi-explorer-forward nil "forward" "")
                        (sumi-explorer-home nil "home" "")))
     (let ((id (1+ sumi-explorer--request-id)))
       (sumi-explorer-test--response (string-replace "request=0" (format "request=%d" id) sumi-explorer-test--state))
       (if (member (car operation) '(sumi-explorer-new-tab sumi-explorer-new-window sumi-explorer-activate-tab))
           (funcall (car operation) (nth 1 operation) 0.1)
         (funcall (car operation) 0.1))
       (should (equal (sumi-explorer-test--contents "request.ini")
                      (format "[Explorer]\nversion=1\nrequest=%d\ncommand=%s\npath=%s\n"
                              id (nth 2 operation) (sumi-explorer--escape (nth 3 operation)))))))))

(ert-deftest sumi-explorer-workspace-rejects-before-publication ()
  (sumi-explorer-test--session
   (setq sumi-explorer--process (make-pipe-process :name "sumi-test" :noquery t))
   (dolist (request '(("new-tab" "relative") ("new-window" "/ssh:host:/tmp")
                      ("activate-tab" "0") ("activate-tab" "01")
                      ("activate-tab" "9223372036854775808")
                      ("back" "/tmp") ("forward" "1") ("home" "/tmp") ("close-tab" "1")))
     (should-error (apply #'sumi-explorer-send (append request '(0.1)))))
   (should-error (sumi-explorer-activate-tab nil 0.1))
   (should-error (sumi-explorer-new-tab "/tmp/\0" 0.1))
   (should-not sumi-explorer--pending)
   (should (= 0 sumi-explorer--request-id))
   (should-not (file-exists-p (expand-file-name "request.ini" sumi-explorer--session-directory)))))

(ert-deftest sumi-explorer-workspace-timeout-resumes-same-request ()
  (sumi-explorer-test--session
   (setq sumi-explorer--process (make-pipe-process :name "sumi-test" :noquery t))
   (should-error (sumi-explorer-new-tab nil 0.01))
   (let ((request (sumi-explorer-test--contents "request.ini")))
     (should (= 1 sumi-explorer--pending))
     (should-error (sumi-explorer-home 0.1))
     (sumi-explorer-test--response (string-replace "request=0" "request=1" sumi-explorer-test--state))
     (should (sumi-explorer-await 0.1))
     (should (equal request (sumi-explorer-test--contents "request.ini")))
     (should (= 1 sumi-explorer--request-id)))))

(ert-deftest sumi-explorer-workspace-error-snapshots ()
  (sumi-explorer-test--session
   (sumi-explorer-test--sidecar "workspace.ini" "[WorkspaceError]\nversion=1\nmessage=unavailable 日本語\n")
   (should (string-match-p "unavailable 日本語"
                           (condition-case err (sumi-explorer-tabs) (error (error-message-string err)))))
   (dolist (bad (list "[WorkspaceError]\nversion=2\nmessage=unavailable\n"
                     "[WorkspaceError]\nversion=1\nmessage=x\nunknown=y\n"
                     (concat "[WorkspaceError]\nversion=1\nmessage=" (make-string 257 ?界) "\n")
                     (concat "[WorkspaceError]\nversion=1\nmessage=x\n" (sumi-explorer-test--workspace 1))))
     (sumi-explorer-test--sidecar "workspace.ini" bad)
     (should-error (sumi-explorer-tabs)))))

(ert-deftest sumi-explorer-local-path-filter-preserves-platform-identities ()
  (should (sumi-explorer--local-absolute-path-p (expand-file-name "日本語\n local" temporary-file-directory)))
  (should-not (sumi-explorer--local-absolute-path-p "/ssh:host:/日本語\n remote"))
  ;; Exercise the lexical filter when the host's native path predicate accepts
  ;; Windows drive/UNC forms.  This is not Windows runtime validation.
  (cl-letf (((symbol-function 'file-name-absolute-p) (lambda (_path) t))
            ((symbol-function 'file-remote-p) (lambda (_path) nil)))
    (should (sumi-explorer--local-absolute-path-p "C:/日本語/file"))
    (should (sumi-explorer--local-absolute-path-p "//server/share/file"))))

(defun sumi-explorer-test--selection-data (paths)
  (concat "[Selection]\nversion=1\nstatus=ok\ndirectory=/tmp\nmessage=\n"
          (format "count=%d\n" (length paths))
          (mapconcat (lambda (entry) (format "path%d=%s\n" (car entry) (sumi-explorer--escape (cdr entry))))
                     (cl-loop for path in paths for index from 0 collect (cons index path)) "")))

(defun sumi-explorer-test--batch-data (id paths &optional status)
  (concat (format "[Batch]\nversion=1\nrequest=%d\ncount=%d\n" id (length paths))
          (mapconcat (lambda (entry)
                       (format "[Item%d]\npath=%s\ndestination=\nstatus=%s\nmessage=\n"
                               (car entry) (sumi-explorer--escape (cdr entry)) (or status "ok")))
                     (cl-loop for path in paths for index from 0 collect (cons index path)) "")))

(ert-deftest sumi-explorer-selection-sidecar-roundtrip ()
  (sumi-explorer-test--session
   (sumi-explorer-test--write (string-replace "selected=" "selected=/tmp/legacy" sumi-explorer-test--state))
   (should (equal (sumi-explorer-selected-files) '("/tmp/legacy")))
   (let ((paths '("/tmp/ 日本語;\\\n\t " "/tmp/second\r path")))
     (sumi-explorer-test--sidecar "selection.ini" (sumi-explorer-test--selection-data paths))
     (should (equal paths (sumi-explorer-selected-files))))
   (sumi-explorer-test--sidecar "selection.ini" (sumi-explorer-test--selection-data nil))
   (should-not (sumi-explorer-selected-files))))

(ert-deftest sumi-explorer-selection-sidecar-schema-errors ()
  (sumi-explorer-test--session
   (sumi-explorer-test--write sumi-explorer-test--state)
   (let ((good (sumi-explorer-test--selection-data '("/tmp/one" "/tmp/two"))))
     (dolist (bad (list (concat good "unknown=x\n")
                       (concat good "count=2\n")
                       (concat good "[Selection]\n")
                       (concat good "[Other]\n")
                       (string-replace "version=1" "version=2" good)
                       (string-replace "count=2" "count=02" good)
                       (string-replace "count=2" "count=257" good)
                       (string-replace "path1=/tmp/two\n" "" good)
                       (string-replace "path1=/tmp/two" "path1=/tmp/one" good)
                       (string-replace "path1=/tmp/two" "path1=relative" good)
                       (string-replace "path1=/tmp/two" "path1=/ssh:host:/file" good)
                       (string-replace "message=" "message=\\x" good)
                       (string-replace "status=ok" "status=error" good)
                       (concat good (mapconcat (lambda (index) (format "[Other%d]\n" index))
                                                (number-sequence 0 257) ""))
                       (make-string 1048577 ?x)))
       (sumi-explorer-test--sidecar "selection.ini" bad)
       (should-error (sumi-explorer-selected-files))))
   (let ((coding-system-for-write 'no-conversion))
     (write-region (concat "[Selection]\n" (unibyte-string 255)) nil
                   (expand-file-name "selection.ini" sumi-explorer--session-directory) nil 'silent))
   (should-error (sumi-explorer-selected-files))
   (sumi-explorer-test--sidecar "selection.ini"
                                "[Selection]\nversion=1\nstatus=error\ndirectory=/tmp\ncount=0\nmessage=limit exceeded\n")
   (should-error (sumi-explorer-selected-files))))

(ert-deftest sumi-explorer-selection-size-bound-survives-replacement ()
  (sumi-explorer-test--session
   (let ((old-attributes (progn
                           (sumi-explorer-test--sidecar "selection.ini" (sumi-explorer-test--selection-data nil))
                           (file-attributes (expand-file-name "selection.ini" sumi-explorer--session-directory)))))
     (sumi-explorer-test--sidecar
      "selection.ini" (string-replace "message=" (concat "message=" (make-string 400000 ?界))
                                      (sumi-explorer-test--selection-data nil)))
     ;; Model an atomic replacement between metadata lookup and the read.
     (cl-letf (((symbol-function 'file-attributes) (lambda (&rest _) old-attributes)))
       (should-error (sumi-explorer-selected-files))))))

(ert-deftest sumi-explorer-selection-boundary-and-select-many ()
  (sumi-explorer-test--session
   (setq sumi-explorer--process (make-pipe-process :name "sumi-test" :noquery t))
   (let ((paths (cl-loop for index below 256 collect (format "/tmp/item%d" index))))
     (sumi-explorer-test--sidecar "selection.ini" (sumi-explorer-test--selection-data paths))
     (should (equal paths (sumi-explorer-selected-files)))
     (sumi-explorer-test--response (string-replace "request=0" "request=1" sumi-explorer-test--state))
     (sumi-explorer-select-files paths 0.1)
     (should (string-match-p "command=select-many\npath=\ncount=256\n" (sumi-explorer-test--contents "request.ini"))))
   (sumi-explorer-test--response (string-replace "request=0" "request=2" sumi-explorer-test--state))
   (sumi-explorer-select-files nil 0.1)
   (should (string-match-p "count=0\n" (sumi-explorer-test--contents "request.ini")))))

(ert-deftest sumi-explorer-batch-manifest-validation-before-publication ()
  (sumi-explorer-test--session
   (setq sumi-explorer--process (make-pipe-process :name "sumi-test" :noquery t))
   (dolist (arguments '(("copy" nil "/tmp") ("copy" ("/tmp/a") nil)
                        ("move" ("relative") "/tmp") ("trash" ("/tmp/a") "/tmp")
                        ("copy" ("/tmp/a" "/tmp/a") "/tmp")
                        ("copy" ("/tmp/a") "/ssh:host:/tmp")
                        ("trash" ("/ssh:host:/tmp")) ("unknown" ("/tmp/a"))))
     (should-error (apply #'sumi-explorer-begin-batch arguments)))
   (should-error (sumi-explorer-begin-batch "trash" (make-list 257 "/tmp/a")))
   (should-error (sumi-explorer-begin-batch "trash" (list (concat "/tmp/" (make-string 1048576 ?x)))))
   (dolist (character (list 0 #xd800 #x110000))
     (should-error (sumi-explorer-begin-batch "trash" (list (concat "/tmp/" (string character))))))
   (should (= sumi-explorer--request-id 0))
   (should-not sumi-explorer--pending)
   (should-not (file-exists-p (expand-file-name "request.ini" sumi-explorer--session-directory)))
   (let* ((paths (list (copy-sequence "/tmp/ 日本語;\\\n ") "/tmp/second"))
          (expected (mapcar #'copy-sequence paths)))
     (should (= 1 (sumi-explorer-begin-batch "copy" paths "/tmp/destination")))
     (aset (car paths) 5 ?X)
     (setcar (cdr paths) "/tmp/replaced")
     (should (equal (sumi-explorer-test--contents "request.ini")
                    (concat "[Explorer]\nversion=1\nrequest=1\ncommand=copy-many\npath=\ncount=2\ndestination=/tmp/destination\n"
                            "path0=" (sumi-explorer--escape (car expected)) "\npath1=/tmp/second\n")))
     (should-error (sumi-explorer-begin-batch "trash" expected))
     (should (= 1 sumi-explorer--request-id)))))

(ert-deftest sumi-explorer-batch-result-schema-and-request ()
  (sumi-explorer-test--session
   (let* ((paths '("/tmp/日本語;\\\n " "/tmp/second"))
          (good (sumi-explorer-test--batch-data 7 paths "skipped")))
     (sumi-explorer-test--sidecar "batch-result.ini" good)
     (let ((result (sumi-explorer-batch-result 7)))
       (should (equal (cdr (assoc "request" result)) "7"))
       (should (equal (mapcar (lambda (item) (cdr (assoc "path" item))) (cdr (assoc "items" result))) paths)))
     (should-error (sumi-explorer-batch-result 6))
     (dolist (bad (list (concat good "unknown=x\n")
                       (concat good "[Item0]\n")
                       (concat good "[Other]\n")
                       (string-replace "version=1" "version=2" good)
                       (string-replace "request=7" "request=0" good)
                       (string-replace "request=7" "request=9223372036854775808" good)
                       (string-replace "count=2" "count=257" good)
                       (string-replace "Item1" "Item01" good)
                       (string-replace "path=/tmp/second" "path=relative" good)
                       (string-replace "destination=" "destination=relative" good)
                       (string-replace "status=skipped" "status=maybe" good)
                       (string-replace "message=" "message=\\z" good)
                       (string-replace "message=" (concat "message=" (make-string 257 ?界)) good)
                       (string-replace "message=\n" "" good)
                       (make-string 1048577 ?x)))
       (sumi-explorer-test--sidecar "batch-result.ini" bad)
       (should-error (sumi-explorer-batch-result))))))

(ert-deftest sumi-explorer-batch-cancel-final-results-and-late-await ()
  (sumi-explorer-test--session
   (setq sumi-explorer--process (make-pipe-process :name "sumi-test" :noquery t))
   (let* ((paths '("/tmp/first" "/tmp/second"))
          (id (sumi-explorer-begin-batch "trash" paths)))
     (should-error (sumi-explorer-await 0.01))
     (should (= id (sumi-explorer-cancel)))
     (should (= id sumi-explorer--pending))
     (sumi-explorer-test--sidecar "batch-result.ini" (sumi-explorer-test--batch-data id paths "skipped"))
     (sumi-explorer-test--response
      (string-replace "status=ok" "status=error"
                      (string-replace "request=0" (format "request=%d" id) sumi-explorer-test--state)))
     (should-error (sumi-explorer-await 0.1))
     (should-not sumi-explorer--pending)
     (should (= 2 (length (cdr (assoc "items" (sumi-explorer-batch-result id))))))
     (should (equal (sumi-explorer-test--contents "cancel.ini") "[Explorer]\nversion=1\nrequest=1\n")))))

(ert-deftest sumi-explorer-batch-wrapper-requires-matching-result ()
  (sumi-explorer-test--session
   (setq sumi-explorer--process (make-pipe-process :name "sumi-test" :noquery t))
   (sumi-explorer-test--sidecar "batch-result.ini" (sumi-explorer-test--batch-data 2 '("/tmp/a")))
   (sumi-explorer-test--response (string-replace "request=0" "request=1" sumi-explorer-test--state))
   (should-error (sumi-explorer-trash-files '("/tmp/a") 0.1))
   (should-not sumi-explorer--pending)))

(ert-deftest sumi-explorer-batch-rejected-duplicate-result ()
  (sumi-explorer-test--session
   (let ((rejected (sumi-explorer-test--batch-data 9 '("/tmp/repeated" "/tmp/repeated") "skipped")))
     (string-match "status=skipped" rejected)
     (setq rejected (replace-match "status=error" t t rejected))
     (sumi-explorer-test--sidecar "batch-result.ini" rejected)
     (should (= 2 (length (cdr (assoc "items" (sumi-explorer-batch-result 9))))))
     (sumi-explorer-test--sidecar "batch-result.ini"
                                  (sumi-explorer-test--batch-data 9 '("/tmp/repeated" "/tmp/repeated")))
     (should-error (sumi-explorer-batch-result 9))
     (sumi-explorer-test--sidecar "batch-result.ini" (string-replace "status=skipped" "status=ok" rejected))
     (should-error (sumi-explorer-batch-result 9)))))

(ert-deftest sumi-explorer-batch-interactive-frozen-and-confirmed ()
  (let ((selected (list (copy-sequence "/tmp/first;\n") "/tmp/second")) submitted)
    (cl-letf (((symbol-function 'sumi-explorer-selected-files) (lambda () selected))
              ((symbol-function 'read-directory-name)
               (lambda (&rest _)
                 (aset (car selected) 5 ?X)
                 (setcar (cdr selected) "/tmp/replaced")
                 "/tmp/destination"))
              ((symbol-function 'sumi-explorer--run-batch)
               (lambda (&rest arguments) (setq submitted arguments))))
      (call-interactively #'sumi-explorer-copy-files)
      (should (equal submitted '("copy" ("/tmp/first;\n" "/tmp/second") "/tmp/destination" nil)))
      (setq selected '("/tmp/confirmed one" "/tmp/confirmed two"))
      (cl-letf (((symbol-function 'y-or-n-p)
                 (lambda (prompt)
                   (should (string-match-p (regexp-quote "/tmp/confirmed one") prompt))
                   (should (string-match-p (regexp-quote "/tmp/confirmed two") prompt))
                   (setq selected '("/tmp/later selection")) t)))
        (call-interactively #'sumi-explorer-trash-files))
      (should (equal submitted '("trash" ("/tmp/confirmed one" "/tmp/confirmed two") nil nil)))
      (setq submitted nil)
      (cl-letf (((symbol-function 'y-or-n-p) (lambda (&rest _) nil)))
        (should-error (call-interactively #'sumi-explorer-trash-files) :type 'user-error))
      (should-not submitted)
      (dolist (command '(sumi-explorer-copy-files sumi-explorer-move-files sumi-explorer-trash-files))
        (should (commandp command))))))

(ert-deftest sumi-explorer-batch-trash-confirmation-is-never-truncated ()
  (let ((print-length 1) (print-level 1) submitted)
    (cl-letf (((symbol-function 'sumi-explorer-selected-files)
               (lambda () '("/tmp/first" "/tmp/second" "/tmp/last\nfile")))
              ((symbol-function 'y-or-n-p)
               (lambda (prompt)
                 (should (string-match-p (regexp-quote "/tmp/second") prompt))
                 (should (string-match-p (regexp-quote "/tmp/last\\nfile") prompt)) t))
              ((symbol-function 'sumi-explorer--run-batch)
               (lambda (&rest arguments) (setq submitted arguments))))
      (call-interactively #'sumi-explorer-trash-files)
      (should (equal (nth 1 submitted) '("/tmp/first" "/tmp/second" "/tmp/last\nfile"))))))

(ert-deftest sumi-explorer-interactive-trash-decline-does-not-publish ()
  (sumi-explorer-test--session
   (setq sumi-explorer--process (make-pipe-process :name "sumi-test" :noquery t))
   (cl-letf (((symbol-function 'sumi-explorer-selected-files) (lambda () '("/tmp/frozen target")))
             ((symbol-function 'y-or-n-p)
              (lambda (prompt)
                (should (string-match-p (regexp-quote "/tmp/frozen target") prompt))
                nil)))
     (should-error (call-interactively #'sumi-explorer-trash) :type 'user-error))
   (should-not sumi-explorer--pending)
   (should (= 0 sumi-explorer--request-id))
   (should-not (file-exists-p (expand-file-name "request.ini" sumi-explorer--session-directory)))))

(ert-deftest sumi-explorer-mutation-publication ()
  (sumi-explorer-test--session
   (setq sumi-explorer--process (make-pipe-process :name "sumi-test" :noquery t))
   (let ((source (expand-file-name "日本語 source\\\n\t " temporary-file-directory))
         (destination (expand-file-name "日本語 destination\\\n\t " temporary-file-directory)))
     (should (= 1 (sumi-explorer-begin-operation "copy" source destination)))
     (should (= 1 sumi-explorer--pending))
     (should (equal (sumi-explorer-test--contents "request.ini")
                    (format "[Explorer]\nversion=1\nrequest=1\ncommand=copy\npath=%s\ndestination=%s\n"
                            (sumi-explorer--escape source) (sumi-explorer--escape destination))))
     (should (= #o600 (file-modes (expand-file-name "request.ini" sumi-explorer--session-directory))))
     (should-error (sumi-explorer-begin-operation "mkdir" destination))
     (should (= 1 sumi-explorer--request-id)))))

(ert-deftest sumi-explorer-mutation-rejections ()
  (sumi-explorer-test--session
   (setq sumi-explorer--process (make-pipe-process :name "sumi-test" :noquery t))
   (dolist (arguments '(("copy" "relative" "/tmp/destination")
                        ("copy" "/tmp/source" "relative")
                        ("move" "/tmp/source")
                        ("rename" "/tmp/source" "/other/destination")
                        ("mkdir" "/tmp/new" "/tmp/unexpected")
                        ("trash" "/tmp/source" "/tmp/unexpected")
                        ("delete" "/tmp/source")
                        ("mkdir" "/ssh:host:/tmp/source")
                        ("mkdir" "~/source")
                        ("trash" "/tmp/\0source")))
     (should-error (apply #'sumi-explorer-begin-operation arguments)))
   (dolist (invalid (list (string #xd800) (string #x110000)
                          (decode-coding-string (unibyte-string 255) 'utf-8)))
     (should-error (sumi-explorer-begin-operation "mkdir" (concat "/tmp/" invalid))))
   (should-error (sumi-explorer-rename "/tmp/source" "../other"))
   (should-error (sumi-explorer-rename "/tmp/source" ".."))
   (should-error (sumi-explorer-copy "/tmp/source" "/tmp/destination" -1))
   (should-error (sumi-explorer-mkdir "/tmp/new" 'invalid))
   (should-error (sumi-explorer-begin-operation "mkdir" (concat "/tmp/" (make-string 1048576 ?x))))
   (should-not sumi-explorer--pending)
   (should (= sumi-explorer--request-id 0))
   (should-not (file-exists-p (expand-file-name "request.ini" sumi-explorer--session-directory)))))

(ert-deftest sumi-explorer-rename-preserves-literal-name ()
  (sumi-explorer-test--session
   (setq sumi-explorer--process (make-pipe-process :name "sumi-test" :noquery t))
   (sumi-explorer-test--response (string-replace "request=0" "request=1" sumi-explorer-test--state))
   (sumi-explorer-rename "/tmp/source" "~literal 日本語\n name" 0.1)
   (should (string-match-p
            (regexp-quote (concat "destination=" (sumi-explorer--escape "/tmp/~literal 日本語\n name") "\n"))
            (sumi-explorer-test--contents "request.ini")))))

(ert-deftest sumi-explorer-await-late-completion ()
  (sumi-explorer-test--session
   (setq sumi-explorer--process (make-pipe-process :name "sumi-test" :noquery t))
   (sumi-explorer-begin-operation "mkdir" "/tmp/new")
   (let ((request (sumi-explorer-test--contents "request.ini")))
     (should-error (sumi-explorer-await 0.01))
     (should (= sumi-explorer--pending 1))
     (sumi-explorer-test--response sumi-explorer-test--state)
     (should-error (sumi-explorer-await 0.01))
     (should (= sumi-explorer--pending 1))
     (sumi-explorer-test--response (string-replace "request=0" "request=1" sumi-explorer-test--state))
     (should (equal (cdr (assoc "status" (sumi-explorer-await 0.1))) "ok"))
     (should-not sumi-explorer--pending)
     (should (= sumi-explorer--request-id 1))
     (should (equal request (sumi-explorer-test--contents "request.ini")))
     (should-error (sumi-explorer-await 0.01)))))

(ert-deftest sumi-explorer-await-quit-retains-pending ()
  (sumi-explorer-test--session
   (setq sumi-explorer--process (make-pipe-process :name "sumi-test" :noquery t))
   (sumi-explorer-begin-operation "trash" "/tmp/source")
   (let (propagated)
     (cl-letf (((symbol-function 'accept-process-output) (lambda (&rest _) (signal 'quit nil))))
       (condition-case nil (sumi-explorer-await 1) (quit (setq propagated t))))
     (should propagated))
   (should (= sumi-explorer--pending 1))
   (should (process-live-p sumi-explorer--process))
   (should (file-directory-p sumi-explorer--session-directory))))

(ert-deftest sumi-explorer-await-reentrant-completion ()
  (sumi-explorer-test--session
   (setq sumi-explorer--process (make-pipe-process :name "sumi-test" :noquery t))
   (sumi-explorer-begin-operation "mkdir" "/tmp/first")
   (let ((timer (run-at-time
                 0.01 nil
                 (lambda ()
                   (sumi-explorer-test--response
                    (string-replace "request=0" "request=1" sumi-explorer-test--state))
                   (sumi-explorer-await 0.1)
                   (sumi-explorer-begin-operation "mkdir" "/tmp/second")))))
     (unwind-protect (should-error (sumi-explorer-await 0.2)) (cancel-timer timer)))
   (should (= 2 sumi-explorer--pending))
   (should (= 2 sumi-explorer--request-id))
   (sumi-explorer-test--response (string-replace "request=0" "request=2" sumi-explorer-test--state))
   (should (sumi-explorer-await 0.1))
   (should-not sumi-explorer--pending)))

(ert-deftest sumi-explorer-await-future-response-retains-pending ()
  (sumi-explorer-test--session
   (setq sumi-explorer--process (make-pipe-process :name "sumi-test" :noquery t))
   (sumi-explorer-begin-operation "mkdir" "/tmp/new")
   (sumi-explorer-test--response (string-replace "request=0" "request=2" sumi-explorer-test--state))
   (should-error (sumi-explorer-await 0.1))
   (should (= 1 sumi-explorer--pending))
   (should (= 1 sumi-explorer--request-id))))

(ert-deftest sumi-explorer-cancel-target-and-final-result ()
  (sumi-explorer-test--session
   (setq sumi-explorer--process (make-pipe-process :name "sumi-test" :noquery t))
   (should-error (sumi-explorer-cancel))
   (should-not (file-exists-p (expand-file-name "cancel.ini" sumi-explorer--session-directory)))
   (sumi-explorer-begin-operation "copy" "/tmp/source" "/tmp/destination")
   (let ((request (sumi-explorer-test--contents "request.ini")))
     (should (= 1 (sumi-explorer-cancel)))
     (should (equal (sumi-explorer-test--contents "cancel.ini") "[Explorer]\nversion=1\nrequest=1\n"))
     (should (= #o600 (file-modes (expand-file-name "cancel.ini" sumi-explorer--session-directory))))
     (should (= 1 sumi-explorer--pending))
     (should-error (sumi-explorer-await 0.01))
     (should (equal request (sumi-explorer-test--contents "request.ini")))
     (sumi-explorer-test--response
      (string-replace "message=" "message=Cancelled; partial destination /tmp/destination"
                      (string-replace "status=ok" "status=error"
                                      (string-replace "request=0" "request=1" sumi-explorer-test--state))))
     (should-error (sumi-explorer-await 0.1))
     (should-not sumi-explorer--pending)
     (should-error (sumi-explorer-cancel))
     (should (= 1 sumi-explorer--request-id)))
   ;; A cancellation arriving after completion does not manufacture an error.
   (sumi-explorer-begin-operation "mkdir" "/tmp/new")
   (sumi-explorer-test--response (string-replace "request=0" "request=2" sumi-explorer-test--state))
   (should (= 2 (sumi-explorer-cancel)))
   (should (equal (cdr (assoc "status" (sumi-explorer-await 0.1))) "ok"))))

(ert-deftest sumi-explorer-synchronous-operation-wrappers ()
  (sumi-explorer-test--session
   (setq sumi-explorer--process (make-pipe-process :name "sumi-test" :noquery t))
   (dolist (operation '((sumi-explorer-copy "/tmp/source" "/tmp/copy")
                        (sumi-explorer-move "/tmp/copy" "/tmp/moved")
                        (sumi-explorer-rename "/tmp/moved" "renamed 日本語")
                        (sumi-explorer-trash "/tmp/renamed 日本語")
                        (sumi-explorer-mkdir "/tmp/new")))
     (let ((timer (run-at-time 0.01 nil #'sumi-explorer-test--response
                               (string-replace "request=0" (format "request=%d" (1+ sumi-explorer--request-id))
                                               sumi-explorer-test--state))))
       (unwind-protect (should (apply (car operation) (append (cdr operation) '(1))))
         (cancel-timer timer))))
   (should (= 5 sumi-explorer--request-id))
   (should-not sumi-explorer--pending)))

(ert-deftest sumi-explorer-roundtrip ()
  (let ((value " 日本語 λ \\ \n\r\t trailing "))
    (should (equal value (sumi-explorer--unescape (sumi-explorer--escape value)))))
  (dolist (value '("\\q" "\\" "a\0b"))
    (should-error (sumi-explorer--unescape value)))
  (should-error (sumi-explorer--escape "\0")))

(ert-deftest sumi-explorer-schema ()
  (sumi-explorer-test--session
   (sumi-explorer-test--write sumi-explorer-test--state)
   (should (equal (cdr (assoc "request" (sumi-explorer-state))) "0"))
   (dolist (text (list (concat sumi-explorer-test--state "status=ok\n")
                      (concat sumi-explorer-test--state "unknown=x\n")
                      (concat sumi-explorer-test--state "[Other]\n")
                      (string-replace "request=0" "request=-1" sumi-explorer-test--state)
                      (string-replace "request=0" "request=01" sumi-explorer-test--state)
                      (string-replace "status=ok" "status=maybe" sumi-explorer-test--state)
                      (string-replace "version=1" "version=2" sumi-explorer-test--state)
                      (string-replace "message=" "message=\\x" sumi-explorer-test--state)
                      (string-replace "message=" "message=\0" sumi-explorer-test--state)
                      (string-replace "selected=\n" "" sumi-explorer-test--state)
                      (make-string 1048577 ?x)))
     (sumi-explorer-test--write text)
     (should-error (sumi-explorer-state)))))

(ert-deftest sumi-explorer-selection ()
  (sumi-explorer-test--session
   (let ((path "/tmp/ 日本語\\\n\t "))
     (sumi-explorer-test--write
      (string-replace "selected=" (concat "selected=" (sumi-explorer--escape path)) sumi-explorer-test--state))
     (should (equal (sumi-explorer-selected-files) (list path))))))

(ert-deftest sumi-explorer-command-controls ()
  (sumi-explorer-test--session
   (sumi-explorer-test--write sumi-explorer-test--state)
   (setq sumi-explorer--process (make-pipe-process :name "sumi-test" :noquery t))
   (should-error (sumi-explorer-send "copy" "/tmp/a"))
   (should (= sumi-explorer--request-id 0))
   (should-not (file-exists-p (expand-file-name "request.ini" sumi-explorer--session-directory)))
   (should-error (sumi-explorer-send "refresh" nil 0.03))
   (should (= sumi-explorer--pending 1))
   (let ((request (with-temp-buffer
                    (insert-file-contents (expand-file-name "request.ini" sumi-explorer--session-directory))
                    (buffer-string))))
     (should-error (sumi-explorer-send "up" nil 0.03))
     (should (= sumi-explorer--request-id 1))
     (should (equal request (with-temp-buffer
                             (insert-file-contents (expand-file-name "request.ini" sumi-explorer--session-directory))
                             (buffer-string)))))))

(ert-deftest sumi-explorer-acknowledgement ()
  (sumi-explorer-test--session
   (sumi-explorer-test--write sumi-explorer-test--state)
   (setq sumi-explorer--process (make-pipe-process :name "sumi-test" :noquery t))
   (let ((timer (run-at-time 0.02 nil #'sumi-explorer-test--response
                             (string-replace "request=0" "request=1" sumi-explorer-test--state))))
     (unwind-protect (should (sumi-explorer-send "refresh" nil 1)) (cancel-timer timer)))
   (should-not sumi-explorer--pending)
   (let ((timer (run-at-time 0.02 nil #'sumi-explorer-test--response
                             (string-replace "status=ok" "status=error"
                                             (string-replace "request=0" "request=2" sumi-explorer-test--state)))))
     (unwind-protect (should-error (sumi-explorer-send "up" nil 1)) (cancel-timer timer)))
   (should-not sumi-explorer--pending)))

(ert-deftest sumi-explorer-startup-validation ()
  (let ((sumi-explorer--session-directory nil))
    (should-error (sumi-explorer-start "/missing-sumi-directory" "/missing-sumi-program"))
    (should-not sumi-explorer--session-directory)
    (should-error (sumi-explorer-start temporary-file-directory "/missing-sumi-program"))
    (should-not sumi-explorer--session-directory)
    (should-error (sumi-explorer-start temporary-file-directory (executable-find "true")))
    (should-not sumi-explorer--session-directory)))

(ert-deftest sumi-explorer-stop-ownership ()
  (let ((sumi-explorer--process (make-pipe-process :name "unrelated" :noquery t))
        (sumi-explorer--owned-process nil)
        (sumi-explorer--session-directory (make-temp-file "user-directory-" t))
        (sumi-explorer--owned-session nil))
    (let ((process sumi-explorer--process) (directory sumi-explorer--session-directory))
      (unwind-protect
          (progn (sumi-explorer-stop)
                 (should (process-live-p process))
                 (should (file-directory-p directory)))
        (delete-process process) (delete-directory directory)))))

(ert-deftest sumi-explorer-startup-fixture ()
  ;; This process is only a local protocol fixture, never a native GUI test.
  (let ((program (make-temp-file "sumi-fixture-"))
        (sumi-explorer--session-directory nil)
        (sumi-explorer--owned-session nil)
        (sumi-explorer--owned-process nil)
        (sumi-explorer--process nil))
    (unwind-protect
        (progn
          (write-region
           (concat "#!/usr/bin/env python3\nimport os,time\n"
                   "p=os.path.join(os.environ['SUMI_EXPLORER_SESSION'],'state.ini')\n"
                   "with open(p,'w',encoding='utf-8') as f:\n"
                   " f.write('[Explorer]\\nversion=1\\nrequest=0\\nstatus=ok\\ndirectory=/tmp\\nselected=\\nmessage=\\n')\n"
                   "while True:\n"
                   " r=os.path.join(os.environ['SUMI_EXPLORER_SESSION'],'request.ini')\n"
                   " if os.path.exists(r):\n"
                   "  text=open(r,encoding='utf-8').read()\n"
                   "  if 'command=quit\\n' in text:\n"
                   "   ident=[s for s in text.splitlines() if s.startswith('request=')][0]\n"
                   "   with open(os.path.join(os.environ['SUMI_EXPLORER_SESSION'],'response.ini'),'w') as f:\n"
                   "    f.write('[Explorer]\\nversion=1\\n'+ident+'\\nstatus=ok\\ndirectory=/tmp\\nselected=\\nmessage=\\n')\n"
                   "   break\n"
                   " time.sleep(.01)\n") nil program nil 'silent)
          (set-file-modes program #o700)
          (should (process-live-p (sumi-explorer-start temporary-file-directory program)))
          (should (= (file-modes sumi-explorer--session-directory) #o700))
          (let ((directory sumi-explorer--session-directory))
            (sumi-explorer-stop)
            (should-not (file-exists-p directory)))
          (sumi-explorer-stop))
      (sumi-explorer-stop t)
      (delete-file program))))

(ert-deftest sumi-explorer-durable-error-response ()
  (sumi-explorer-test--session
   (setq sumi-explorer--process (make-pipe-process :name "sumi-test" :noquery t))
   (sumi-explorer-test--write (string-replace "request=0" "request=1" sumi-explorer-test--state))
   (sumi-explorer-test--response
    (string-replace "status=ok" "status=error"
                    (string-replace "request=0" "request=1" sumi-explorer-test--state)))
   (should-error (sumi-explorer-send "refresh" nil 0.1))
   (should-not sumi-explorer--pending)))

(ert-deftest sumi-explorer-stop-still-finishing ()
  (sumi-explorer-test--session
   (setq sumi-explorer--process (make-pipe-process :name "sumi-test" :noquery t)
         sumi-explorer--owned-process sumi-explorer--process
         sumi-explorer--owned-session sumi-explorer--session-directory)
   (let ((sumi-explorer--stop-timeout 0.03)
         (timer (run-at-time 0.01 nil #'sumi-explorer-test--response
                             (string-replace "request=0" "request=1" sumi-explorer-test--state))))
     (unwind-protect (should-error (sumi-explorer-stop)) (cancel-timer timer)))
   (should (process-live-p sumi-explorer--process))
   (should (file-directory-p sumi-explorer--session-directory))
   (should sumi-explorer--quit-requested)
   (let ((id sumi-explorer--request-id))
     (let ((sumi-explorer--stop-timeout 0.01)) (should-error (sumi-explorer-stop)))
     (should (= id sumi-explorer--request-id)))
   (delete-process sumi-explorer--process)
   (let ((directory sumi-explorer--session-directory))
     (sumi-explorer-stop)
     (should-not (file-exists-p directory))
     ;; The session macro owns this harmless replacement for fixture cleanup.
     (setq sumi-explorer--session-directory (make-temp-file "sumi-test-" t)))))

(ert-deftest sumi-explorer-stop-unresolved-request ()
  (sumi-explorer-test--session
   (setq sumi-explorer--process (make-pipe-process :name "sumi-test" :noquery t)
         sumi-explorer--owned-process sumi-explorer--process
         sumi-explorer--owned-session sumi-explorer--session-directory
         sumi-explorer--pending 1 sumi-explorer--request-id 1)
   (should-error (sumi-explorer-stop))
   (should (process-live-p sumi-explorer--process))
   (should (file-directory-p sumi-explorer--session-directory))
   (should (= sumi-explorer--pending 1))
   (should (= sumi-explorer--request-id 1))
   (should-not (file-exists-p (expand-file-name "request.ini" sumi-explorer--session-directory)))
   (let ((directory sumi-explorer--session-directory) (process sumi-explorer--process))
     (sumi-explorer-stop t)
     (should-not (process-live-p process))
     (should-not (file-exists-p directory))
     (setq sumi-explorer--session-directory (make-temp-file "sumi-test-" t)))))

(ert-deftest sumi-explorer-invalid-utf8 ()
  (sumi-explorer-test--session
   (let ((coding-system-for-write 'no-conversion))
     (write-region (concat (encode-coding-string
                            (string-remove-suffix "message=\n" sumi-explorer-test--state) 'utf-8)
                           "message=" (unibyte-string 255) "\n") nil
                   (expand-file-name "state.ini" sumi-explorer--session-directory) nil 'silent))
   (should-error (sumi-explorer-state))))

(ert-deftest sumi-explorer-request-int64-boundary ()
  (sumi-explorer-test--session
   (sumi-explorer-test--write
    (string-replace "request=0" "request=9223372036854775807" sumi-explorer-test--state))
   (should (equal (cdr (assoc "request" (sumi-explorer-state))) "9223372036854775807"))
   (sumi-explorer-test--write
    (string-replace "request=0" "request=9223372036854775808" sumi-explorer-test--state))
   (should-error (sumi-explorer-state))
   (setq sumi-explorer--process (make-pipe-process :name "sumi-test" :noquery t)
         sumi-explorer--request-id sumi-explorer--maximum-request-id)
   (should-error (sumi-explorer-send "refresh"))
   (should-not sumi-explorer--pending)
   (should-not (file-exists-p (expand-file-name "request.ini" sumi-explorer--session-directory)))))

(ert-deftest sumi-explorer-startup-quit-cleanup ()
  (let ((sumi-explorer--session-directory nil)
        (sumi-explorer--owned-session nil)
        (sumi-explorer--owned-process nil)
        (sumi-explorer--process nil)
        observed-process observed-directory propagated)
    (unwind-protect
        (progn
          (cl-letf (((symbol-function 'accept-process-output)
                     (lambda (&rest _)
                       (setq observed-process sumi-explorer--process
                             observed-directory sumi-explorer--session-directory)
                       (signal 'quit nil))))
            (condition-case nil
                (sumi-explorer-start temporary-file-directory (executable-find "cat"))
              (quit (setq propagated t))))
          (should propagated)
          (should observed-directory)
          (should-not (process-live-p observed-process))
          (should-not (file-exists-p observed-directory))
          (should-not sumi-explorer--session-directory))
      (sumi-explorer-stop))))
