#!/usr/bin/env python3
"""Real everyday acceptance; run inside isolated dbus-run-session and Xvfb.

NELISP_REPO_ROOT supplies the public compiler for the actual-helper AOT unit.
"""
import argparse
import configparser
import ctypes
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import os
from pathlib import Path
import resource
import signal
import shutil
import shlex
import subprocess
import tempfile
import time


def cut_completion_unit(artifacts, first, second, third):
    """AOT unit: real providers, actual completion helper, constructed outcomes."""
    source = Path(__file__).resolve().parents[1] / "explorer" / "sumi-explorer-native.el"
    compiler = os.environ.get("NELISP_REPO_ROOT")
    assert compiler, "NELISP_REPO_ROOT is required for the actual-helper AOT unit"
    text = source.read_text()
    start = text.index(" (defun main (")
    index, depth, string, escape, comment = start + 1, 0, False, False, False
    while index < len(text):
        character = text[index]
        if comment:
            comment = character != "\n"
        elif string:
            if escape:
                escape = False
            elif character == "\\":
                escape = True
            elif character == '"':
                string = False
        elif character == ';':
            comment = True
        elif character == '"':
            string = True
        elif character == '(':
            depth += 1
        elif character == ')':
            depth -= 1
            if depth == 0:
                break
        index += 1
    main = r'''
 (defun sumi_explorer_unit_wait (ctx)
   (seq
    (ptr-write-u64 ctx 136 (+ (ptr-read-u64 ctx 136) 1))
    (if (or (= (ptr-read-u64 ctx 616) 0) (> (ptr-read-u64 ctx 136) 500))
      (seq (extern-call g_main_loop_quit (ptr-read-u64 ctx 0)) 0) 1)))
 (defun main (argc argv)
   (let ((ctx (extern-call g_malloc0 728)) (window 0) (paths (extern-call g_malloc0 24))
         (job (extern-call g_malloc0 120)) (items (extern-call g_malloc0 24))
         (a (extern-call g_malloc0 104)) (b (extern-call g_malloc0 104)) (c (extern-call g_malloc0 104)) (good 0) (generation 0) (read 0) (timer 0))
     (seq
      (extern-call gtk_init)
      (setq window (extern-call gtk_window_new))
      (ptr-write-u64 ctx 568 (extern-call gtk_widget_get_clipboard window))
      (ptr-write-u64 paths 0 (ptr-read-u64 argv 8)) (ptr-write-u64 paths 8 (ptr-read-u64 argv 16))
      (ptr-write-u64 paths 16 (ptr-read-u64 argv 24))
      (sumi_explorer_clipboard_install ctx paths 3 1)
      (ptr-write-u64 ctx 624 (ptr-read-u64 ctx 592))
      (ptr-write-u64 ctx 632 (extern-call g_object_ref (ptr-read-u64 ctx 576)))
      (ptr-write-u64 job 80 3) (ptr-write-u64 job 88 items)
      (ptr-write-u64 items 0 a) (ptr-write-u64 items 8 b) (ptr-write-u64 items 16 c)
      (ptr-write-u64 a 8 (ptr-read-u64 paths 0)) (ptr-write-u64 a 96 1)
      (ptr-write-u64 b 8 (ptr-read-u64 paths 8))
      (ptr-write-u64 b 96 2) (ptr-write-u64 c 8 (ptr-read-u64 paths 16))
      (sumi_explorer_cut_completed ctx job)
      (setq good (and (= (ptr-read-u64 ctx 608) 2) (ptr-read-u64 ctx 584)
                     (sumi_explorer_same (ptr-read-u64 (ptr-read-u64 ctx 600) 0) (ptr-read-u64 paths 8))
                     (sumi_explorer_same (ptr-read-u64 (ptr-read-u64 ctx 600) 8) (ptr-read-u64 paths 16))
                     (= (ptr-read-u64 ctx 576) (extern-call gdk_clipboard_get_content (ptr-read-u64 ctx 568)))))
      (ptr-write-u64 ctx 624 (ptr-read-u64 ctx 592))
      (ptr-write-u64 ctx 632 (extern-call g_object_ref (ptr-read-u64 ctx 576)))
      (sumi_explorer_clipboard_install ctx paths 1 0)
      (setq generation (ptr-read-u64 ctx 592))
      (sumi_explorer_cut_completed ctx job)
      (setq good (and good (= generation (ptr-read-u64 ctx 592)) (= (ptr-read-u64 ctx 584) 0)
                     (= (ptr-read-u64 ctx 608) 1)
                     (sumi_explorer_same (ptr-read-u64 (ptr-read-u64 ctx 600) 0) (ptr-read-u64 paths 0))))
      ;; Actual GDK asynchronous callback: replace a captured owned Cut before
      ;; yielding, so generation validation must fail before starting any worker.
      (ptr-write-u64 ctx 0 (extern-call g_main_loop_new 0 0)) (ptr-write-u64 ctx 8 window)
      (ptr-write-u64 ctx 16 (extern-call gtk_list_box_new)) (extern-call g_object_ref_sink (ptr-read-u64 ctx 16))
      (ptr-write-u64 ctx 24 (extern-call gtk_entry_new)) (extern-call g_object_ref_sink (ptr-read-u64 ctx 24))
      (ptr-write-u64 ctx 32 (extern-call gtk_label_new (data-addr empty))) (extern-call g_object_ref_sink (ptr-read-u64 ctx 32))
      (ptr-write-u64 ctx 40 (extern-call g_strdup (ptr-read-u64 argv 40)))
      (ptr-write-u64 ctx 48 (extern-call g_strdup (data-addr empty)))
      (ptr-write-u64 ctx 56 (extern-call g_build_filename (ptr-read-u64 argv 32) (data-addr state_name) 0))
      (ptr-write-u64 ctx 160 (extern-call g_build_filename (ptr-read-u64 argv 32) (data-addr response_name) 0))
      (ptr-write-u64 ctx 256 (extern-call g_build_filename (ptr-read-u64 argv 32) (data-addr selection_name) 0))
      (ptr-write-u64 ctx 336 (extern-call g_build_filename (ptr-read-u64 argv 32) (data-addr workspace_name) 0))
      (ptr-write-u64 ctx 432 (extern-call g_build_filename (ptr-read-u64 argv 32) (data-addr view_name) 0))
      (ptr-write-u64 ctx 344 (extern-call g_malloc0 512))
      (ptr-write-u64 (ptr-read-u64 ctx 344) 0 (sumi_explorer_tab_new 1 (ptr-read-u64 ctx 40)))
      (ptr-write-u64 ctx 352 1) (ptr-write-u64 ctx 488 1) (ptr-write-u64 ctx 552 1)
      (sumi_explorer_clipboard_install ctx paths 3 1)
      (setq read (extern-call g_malloc0 48))
      (ptr-write-u64 read 0 (extern-call g_strdup (ptr-read-u64 argv 48)))
      (ptr-write-u64 read 8 1) (ptr-write-u64 read 16 (extern-call g_object_ref (ptr-read-u64 ctx 576)))
      (ptr-write-u64 read 24 (ptr-read-u64 ctx 592)) (ptr-write-u64 read 32 1) (ptr-write-u64 read 40 1)
      (ptr-write-u64 ctx 616 read) (ptr-write-u64 ctx 144 1) (ptr-write-u64 ctx 224 1)
      (ptr-write-u64 ctx 184 (extern-call g_cancellable_new))
      (extern-call gdk_clipboard_read_value_async (ptr-read-u64 ctx 568) (extern-call gdk_file_list_get_type) 0
        (ptr-read-u64 ctx 184) (addr-of sumi_explorer_clipboard_read) ctx)
      (sumi_explorer_clipboard_install ctx paths 1 0) (setq generation (ptr-read-u64 ctx 592))
      (setq timer (extern-call g_timeout_add 10 (addr-of sumi_explorer_unit_wait) ctx))
      (extern-call g_main_loop_run (ptr-read-u64 ctx 0))
      (setq good (and good (= (ptr-read-u64 ctx 616) 0) (= (ptr-read-u64 ctx 240) 0)
                     (= (ptr-read-u64 ctx 72) 1) (= (ptr-read-u64 ctx 176) 0) (= generation (ptr-read-u64 ctx 592))))
      (sumi_explorer_tab_free (ptr-read-u64 (ptr-read-u64 ctx 344) 0)) (extern-call g_free (ptr-read-u64 ctx 344))
      (extern-call g_free (ptr-read-u64 ctx 40)) (extern-call g_free (ptr-read-u64 ctx 48))
      (extern-call g_free (ptr-read-u64 ctx 56)) (extern-call g_free (ptr-read-u64 ctx 160))
      (extern-call g_free (ptr-read-u64 ctx 168)) (extern-call g_free (ptr-read-u64 ctx 256))
      (extern-call g_free (ptr-read-u64 ctx 336)) (extern-call g_free (ptr-read-u64 ctx 432))
      (sumi_explorer_paths_free (ptr-read-u64 ctx 272) (ptr-read-u64 ctx 280))
      (extern-call g_object_unref (ptr-read-u64 ctx 16)) (extern-call g_object_unref (ptr-read-u64 ctx 24))
      (extern-call g_object_unref (ptr-read-u64 ctx 32)) (extern-call g_main_loop_unref (ptr-read-u64 ctx 0))
      (extern-call gdk_clipboard_set_content (ptr-read-u64 ctx 568) 0)
      (extern-call g_object_unref (ptr-read-u64 ctx 576))
      (sumi_explorer_paths_free (ptr-read-u64 ctx 600) (ptr-read-u64 ctx 608))
      (extern-call gtk_window_destroy window)
      (extern-call g_free a) (extern-call g_free b) (extern-call g_free c) (extern-call g_free items)
      (extern-call g_free paths) (extern-call g_free job) (extern-call g_free ctx)
      (if good 0 1))))
'''
    fixture = artifacts / "cut-completion-unit.el"
    fixture.write_text(text[:start] + main + text[index + 1:])
    obj, exe = artifacts / "cut-completion-unit.o", artifacts / "cut-completion-unit"
    driver = artifacts / "cut-completion-build.el"
    quote = lambda path: json.dumps(str(path), ensure_ascii=False)
    driver.write_text(f'''(add-to-list 'load-path (expand-file-name "lisp" {quote(compiler)}))
(add-to-list 'load-path (expand-file-name "src" {quote(compiler)}))
(require 'nelisp-aot-compiler)
(with-temp-buffer (insert-file-contents {quote(fixture)}) (goto-char (point-min))
 (nelisp-aot-compile-to-object (read (current-buffer)) {quote(obj)} :format 'elf :arch (if (string-prefix-p "aarch64" system-configuration) 'aarch64 'x86_64)))
''')
    with (artifacts / "cut-completion-build.log").open("wb") as log:
        subprocess.run(["emacs", "-Q", "--batch", "-l", str(driver)], check=True, timeout=600, stdout=log, stderr=subprocess.STDOUT)
        cflags = shlex.split(subprocess.check_output(
            ["pkg-config", "--cflags", "gtk4", "libarchive"], text=True))
        libs = shlex.split(subprocess.check_output(
            ["pkg-config", "--libs", "gtk4", "libarchive"], text=True))
        platform = artifacts / "platform.o"
        cc = os.environ.get("CC", "cc")
        subprocess.run([cc, "-std=c11", "-Wall", "-Wextra", "-Werror", *cflags,
                        "-c", str(source.parent / "platform.c"), "-o", str(platform)],
                       check=True, timeout=600, stdout=log, stderr=subprocess.STDOUT)
        subprocess.run([cc, str(obj), str(platform), "-o", str(exe), *libs],
                       check=True, timeout=600, stdout=log, stderr=subprocess.STDOUT)
    session, destination = artifacts / "unit-session", artifacts / "unit-destination"
    session.mkdir(exist_ok=True)
    destination.mkdir(exist_ok=True)
    subprocess.run([str(exe), str(first), str(second), str(third), str(session), str(first.parent), str(destination)], check=True, timeout=15)
    assert not list(destination.iterdir()), "Changed captured Cut must fail before filesystem mutation"


def wait(check, description, seconds=15):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        result = check()
        if result:
            return result
        time.sleep(0.025)
    raise AssertionError(f"Timed out: {description}")


def normal_close(window):
    """Send WM_DELETE_WINDOW through X11, exercising GTK's normal close path."""
    library = ctypes.CDLL("libX11.so.6")
    library.XOpenDisplay.restype = ctypes.c_void_p
    display = library.XOpenDisplay(None)
    assert display
    library.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
    library.XInternAtom.restype = ctypes.c_ulong
    class Message(ctypes.Structure):
        _fields_ = [("type", ctypes.c_int), ("serial", ctypes.c_ulong), ("send_event", ctypes.c_int), ("display", ctypes.c_void_p), ("window", ctypes.c_ulong), ("message_type", ctypes.c_ulong), ("format", ctypes.c_int), ("data", ctypes.c_long * 5)]
    class Event(ctypes.Union):
        _fields_ = [("message", Message), ("padding", ctypes.c_long * 24)]
    event = Event()
    event.message.type, event.message.display, event.message.window = 33, display, int(window)
    event.message.message_type = library.XInternAtom(display, b"WM_PROTOCOLS", 0)
    event.message.format = 32
    event.message.data[0] = library.XInternAtom(display, b"WM_DELETE_WINDOW", 0)
    library.XSendEvent.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_long, ctypes.c_void_p]
    library.XFlush.argtypes = [ctypes.c_void_p]
    library.XCloseDisplay.argtypes = [ctypes.c_void_p]
    assert library.XSendEvent(display, int(window), 0, 0, ctypes.byref(event))
    library.XFlush(display)
    library.XCloseDisplay(display)


def run(args):
    assert os.environ.get("DBUS_SESSION_BUS_ADDRESS"), "Run inside an isolated dbus-run-session"
    for program in ("xdotool", "xclip", "xwininfo", "nautilus", "import"):
        assert shutil.which(program), f"UNAVAILABLE: required actual application {program}"
    spec = importlib.util.spec_from_file_location("gui", Path(__file__).with_name("explorer-gui-test.py"))
    gui = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gui)
    artifacts = args.artifacts or args.binary.resolve().parent / "everyday-evidence"
    artifacts.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="sumi presentation 日本語 ") as temporary:
        base = Path(temporary)
        root = base / "root"
        root.mkdir()
        folder = root / "Folder"
        folder.mkdir()
        alpha, beta, literal, hidden = [root / n for n in ("Alpha 日本語.txt", "Beta.txt", "literal*star", ".hidden")]
        for path, contents in ((alpha, b"alpha bytes"), (beta, b"bb"), (literal, b"star"), (hidden, b"hidden")):
            path.write_bytes(contents)
        os.utime(alpha, (1000000000, 1000000000))
        os.utime(beta, (1100000000, 1100000000))
        config = base / "config"
        config.mkdir()
        previous = {key: os.environ.get(key) for key in ("XDG_CONFIG_HOME", "XDG_DATA_HOME")}
        os.environ.update(XDG_CONFIG_HOME=str(config), XDG_DATA_HOME=str(base / "data"))
        directory = base / "session"
        directory.mkdir()
        session = gui.Session(args.binary.resolve(), root, directory)
        peers = []
        try:
            def command(name, path="", good=True, peer=session):
                response = peer.command(name, str(path))
                assert response["status"] == ("ok" if good else "error"), response
                return response

            def read(name, peer=session):
                parser = configparser.RawConfigParser(strict=True)
                path = peer.directory / name
                assert path.stat().st_size <= 1048576
                parser.read_string(path.read_text())
                return {section: {key: gui.unescape(value) for key, value in parser[section].items()} for section in parser.sections()}

            command("filter", "ALPHA")
            view = read("view.ini")["View"]
            assert set(view) == {"version", "directory", "filter", "sort", "order", "hidden", "view"}
            assert view["filter"] == "ALPHA" and view["directory"] == str(root)
            command("select", alpha)
            command("select", beta, good=False)
            command("filter", "*")
            command("select", literal)
            command("filter", "あ" * 257, good=False)
            assert read("view.ini")["View"]["filter"] == "*"
            command("filter", "")
            print("PASS literal case-folded Unicode filter and scalar bound with exact view snapshot", flush=True)
            for name, invalid in (("sort", "invalid"), ("order", "reverse"), ("hidden", "yes"), ("view", "grid")):
                before = read("view.ini")
                command(name, invalid, good=False)
                assert read("view.ini") == before
            command("sort", "size")
            command("order", "desc")
            command("view", "details")
            command("hidden", "show")
            command("select", hidden)
            command("hidden", "hide")
            assert session.state()["selected"] == "", (session.state(), read("view.ini"))
            command("select", hidden, good=False)
            print("PASS validated presentation enums and hidden selections discarded by identity", flush=True)
            command("view", "list")
            command("order", "asc")
            window = gui.xdo("search", "--sync", "--name", "^Sumi Explorer$").splitlines()[-1]
            command("select", folder)
            gui.xdo("windowfocus", "--sync", window)
            gui.xdo("key", "Down")
            session.wait(lambda state: state["selected"] == str(beta))
            command("order", "desc")
            command("select", folder)
            gui.xdo("key", "Down")
            session.wait(lambda state: state["selected"] == str(alpha))
            print("PASS actual GTK size ordering keeps directories first in both directions", flush=True)
            gui.xdo("mousemove", "--window", window, "700", "52")
            gui.xdo("click", "1")
            gui.xdo("key", "ctrl+a")
            gui.xdo("type", "Beta")
            gui.xdo("key", "Return")
            wait(lambda: read("view.ini")["View"]["filter"] == "Beta", "GUI filter Enter")
            command("select", alpha, good=False)
            gui.xdo("key", "ctrl+a")
            gui.xdo("key", "BackSpace")
            gui.xdo("key", "Return")
            wait(lambda: read("view.ini")["View"]["filter"] == "", "GUI filter clear")
            geometry = dict(line.split("=", 1) for line in gui.xdo("getwindowgeometry", "--shell", window).splitlines())
            assert (geometry["WIDTH"], geometry["HEIGHT"]) == ("800", "600"), geometry
            subprocess.run(["import", "-window", window, str(artifacts / "presentation.png")], check=True, timeout=8)
            print("PASS pointer-driven filter control and captured 800x600 layout", flush=True)
            command("filter", "alpha")
            command("new-tab", root)
            assert read("view.ini")["View"]["filter"] == ""
            gui.xdo("mousemove", "--window", window, "700", "52")
            gui.xdo("click", "1")
            gui.xdo("key", "ctrl+w", "ctrl+t", "ctrl+n")
            time.sleep(0.15)
            assert read("workspace.ini")["Workspace"]["count"] == "2"
            assert len(gui.xdo("search", "--name", "^Sumi Explorer$").splitlines()) == 1
            command("activate-tab", "1")
            assert read("view.ini")["View"]["filter"] == "alpha"
            command("filter", "")
            print("PASS presentation settings remain owned by each tab", flush=True)
            print("PASS filter editing keeps workspace shortcuts from closing or creating windows", flush=True)
            command("open", folder)
            before = read("view.ini")
            folder.rmdir()
            command("sort", "date", good=False)
            assert read("view.ini") == before
            folder.mkdir()
            command("open", root)
            print("PASS failed directory enumeration rolls back presentation settings", flush=True)
            destination = base / "clipboard destination 日本語"
            destination.mkdir()
            command("select", alpha)
            command("clipboard-copy")
            command("paste", destination)
            assert (destination / alpha.name).read_bytes() == alpha.read_bytes()
            command("select", beta)
            command("clipboard-cut")
            command("paste", destination)
            assert not beta.exists() and (destination / beta.name).read_bytes() == b"bb"
            command("clipboard-copy", root, good=False)
            print("PASS real system clipboard copy/cut/paste with retained copy and native move", flush=True)
            keyboard_file = root / "keyboard cut source"
            keyboard_file.write_bytes(b"keyboard-cut")
            command("refresh")
            command("select", keyboard_file)
            gui.xdo("key", "ctrl+x")
            command("open", destination)
            gui.xdo("key", "ctrl+v")
            wait(lambda: (destination / keyboard_file.name).exists() and not keyboard_file.exists(), "actual GUI Ctrl+X/V move")
            session.wait(lambda state: state["status"] == "ok" and state["message"] not in {"Operation in progress", "Reading clipboard files"})
            command("select", destination / keyboard_file.name)
            gui.xdo("key", "F2")
            prompt = gui.xdo("search", "--sync", "--name", "^Sumi Rename$").splitlines()[-1]
            command("select", destination / alpha.name)
            gui.xdo("windowfocus", "--sync", prompt)
            gui.xdo("key", "ctrl+a")
            gui.xdo("type", "renamed by prompt")
            gui.xdo("key", "Return")
            renamed = destination / "renamed by prompt"
            wait(lambda: renamed.exists(), "actual F2 rename prompt")
            session.wait(lambda state: state["status"] == "ok" and state["message"] != "Operation in progress")
            assert (destination / alpha.name).exists(), "Rename must retain the frozen original target"
            command("select", renamed)
            gui.xdo("windowfocus", "--sync", window)
            gui.xdo("key", "ctrl+shift+n")
            prompt = gui.xdo("search", "--sync", "--name", "^Sumi New Folder$").splitlines()[-1]
            command("open", root)
            gui.xdo("windowfocus", "--sync", prompt)
            gui.xdo("type", "folder by prompt")
            gui.xdo("key", "Return")
            wait(lambda: (destination / "folder by prompt").is_dir(), "actual new-folder prompt")
            session.wait(lambda state: state["status"] == "ok" and state["message"] != "Operation in progress")
            assert not (root / "folder by prompt").exists()
            command("open", destination)
            command("refresh")
            command("select", renamed)
            gui.xdo("windowfocus", "--sync", window)
            gui.xdo("key", "Delete")
            confirmation = gui.xdo("search", "--sync", "--name", "^Confirm Trash$").splitlines()[-1]
            command("select", destination / alpha.name)
            gui.xdo("windowfocus", "--sync", confirmation)
            gui.xdo("key", "Return")
            wait(lambda: not renamed.exists(), "actual Delete frozen Trash confirmation")
            session.wait(lambda state: state["status"] == "ok" and state["message"] != "Operation in progress")
            assert (destination / alpha.name).exists(), "Trash must retain the frozen original target"
            command("open", root)
            print("PASS GUI Cut/Paste, F2 Rename, New Folder and Delete confirmation mutate frozen targets", flush=True)

            def begin_paste(host, target):
                host.serial += 1
                text = f"[Explorer]\nversion=1\nrequest={host.serial}\ncommand=paste\npath={gui.escape(str(target))}\n"
                temporary = host.directory / "request.tmp"
                temporary.write_text(text)
                temporary.replace(host.directory / "request.ini")
                return host.serial

            nautilus_root = base / "Nautilus clipboard source 日本語"
            nautilus_root.mkdir()
            nautilus_file = nautilus_root / "foreign source.txt"
            nautilus_file.write_bytes(b"Nautilus clipboard COPY")
            nautilus_destination = base / "Nautilus clipboard destination"
            nautilus_destination.mkdir()
            with (artifacts / "nautilus-clipboard.log").open("wb") as log:
                nautilus = subprocess.Popen(["nautilus", "--new-window", str(nautilus_root)], stdout=log, stderr=subprocess.STDOUT)
                try:
                    nautilus_window = gui.xdo("search", "--sync", "--onlyvisible", "--class", "org.gnome.Nautilus").splitlines()[-1]
                    gui.xdo("windowfocus", "--sync", nautilus_window)
                    time.sleep(1)
                    gui.xdo("key", "Escape")
                    gui.xdo("mousemove", "--window", nautilus_window, "300", "130")
                    gui.xdo("click", "1")
                    gui.xdo("key", "ctrl+a", "ctrl+c")
                    time.sleep(0.5)
                    targets = subprocess.run(["xclip", "-selection", "clipboard", "-o", "-t", "TARGETS"], capture_output=True, timeout=3)
                    (artifacts / "nautilus-clipboard-targets.txt").write_bytes(targets.stdout)
                    subprocess.run(["import", "-window", nautilus_window, str(artifacts / "nautilus-clipboard.png")], check=True, timeout=8)
                    command("paste", nautilus_destination)
                    assert (nautilus_destination / nautilus_file.name).read_bytes() == nautilus_file.read_bytes()
                    assert nautilus_file.exists()
                    print("PASS actual Nautilus clipboard COPY transfers bytes and retains its source", flush=True)
                finally:
                    nautilus.terminate()
                    nautilus.wait(timeout=5)

            def start_file_owner():
                process = subprocess.Popen(["xclip", "-selection", "clipboard", "-t", "text/uri-list", "-i", "-quiet"], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                payload = (alpha.as_uri() + "\r\n").encode()
                process.stdin.write(payload)
                process.stdin.close()
                wait(lambda: subprocess.run(["xclip", "-selection", "clipboard", "-o", "-t", "text/uri-list"], capture_output=True, timeout=3).stdout == payload, "external clipboard ownership")
                time.sleep(0.25)  # Deliver the receiver's XFixes notification.
                return process

            def pause_owner(process):
                os.kill(process.pid, signal.SIGSTOP)
                assert os.WIFSTOPPED(os.waitpid(process.pid, os.WUNTRACED)[1])

            owner = start_file_owner()
            try:
                foreign = base / "foreign clipboard COPY"
                foreign.mkdir()
                command("paste", foreign)
                assert (foreign / alpha.name).read_bytes() == alpha.read_bytes() and alpha.exists()
                print("PASS foreign system file-list clipboard is COPY-only and retains its source", flush=True)
                owner.terminate()
                owner.wait(timeout=5)
                owner = start_file_owner()  # Fresh generation; no prior GDK data read.
                pause_owner(owner)
                pending_target = base / "cancel pending clipboard"
                pending_target.mkdir()
                pending = begin_paste(session, pending_target)
                wait(lambda: session.state().get("message") == "Reading clipboard files", "genuine asynchronous paused-owner clipboard read")
                assert session.state(response=True)["request"] != str(pending)
                time.sleep(0.15)
                assert session.state(response=True)["request"] != str(pending) and not list(pending_target.iterdir())
                (directory / "cancel.ini").write_text(f"[Explorer]\nversion=1\nrequest={pending}\n")
                wait(lambda: not (directory / "cancel.ini").exists(), "pending-read cancellable receives matching cancellation")
                # GTK/X11 may await the external owner's reply before delivering
                # cancellation. Keep ctx reserved until that real callback.
                os.kill(owner.pid, signal.SIGCONT)
                result = session.wait(lambda state: state["request"] == str(pending), response=True)
                assert result["status"] == "error" and not list(pending_target.iterdir())
                command("refresh")
                print("PASS cancellation completes a genuinely pending clipboard read without an absent job", flush=True)
                closing_directory = base / "closing pending clipboard session"
                closing_directory.mkdir()
                closing = gui.Session(args.binary.resolve(), root, closing_directory)
                peers.append(closing)
                closing_window = gui.xdo("search", "--sync", "--name", "^Sumi Explorer$").splitlines()[-1]
                pause_owner(owner)
                pending = begin_paste(closing, pending_target)
                wait(lambda: closing.state().get("message") == "Reading clipboard files", "close pending clipboard read")
                normal_close(closing_window)
                time.sleep(0.1)
                assert closing.process.poll() is None, "Context must remain alive until read callback"
                os.kill(owner.pid, signal.SIGCONT)
                closing.process.wait(timeout=8)
                assert closing.process.returncode == 0 and closing.state(response=True)["status"] == "error"
                assert not list(pending_target.iterdir())
                print("PASS normal close cancels pending read and waits for its callback before freeing context", flush=True)
            finally:
                if owner.poll() is None:
                    os.kill(owner.pid, signal.SIGCONT)
                    owner.terminate()
                    owner.wait(timeout=5)
            drag_root, drop_root = base / "external multi tree", base / "external multi destination"
            drag_root.mkdir()
            drop_root.mkdir()
            trees = [drag_root / "A 日本語", drag_root / "B tree"]
            for tree in trees:
                tree.mkdir()
                (tree / "line\nname.txt").write_bytes(b"recursive external COPY")
            link = drag_root / "preserved link"
            link.symlink_to(trees[0].name)
            command("open", drag_root)
            session.serial += 1
            manifest = f"[Explorer]\nversion=1\nrequest={session.serial}\ncommand=select-many\npath=\ncount=3\n"
            manifest += "".join(f"path{i}={gui.escape(str(path))}\n" for i, path in enumerate([*trees, link]))
            (directory / "request.ini").write_text(manifest)
            assert session.wait(lambda state: state["request"] == str(session.serial), response=True)["status"] == "ok"
            before_windows = set(gui.xdo("search", "--name", "^Sumi Explorer$").splitlines())
            drop_session_directory = base / "drop session"
            drop_session_directory.mkdir()
            drop_session = gui.Session(args.binary.resolve(), drop_root, drop_session_directory)
            peers.append(drop_session)
            drop_window = (set(gui.xdo("search", "--sync", "--name", "^Sumi Explorer$").splitlines()) - before_windows).pop()
            gui.xdo("windowmove", window, "0", "0")
            gui.xdo("windowmove", drop_window, "600", "0")
            def drag_many():
                gui.xdo("windowfocus", "--sync", window)
                gui.xdo("mousemove", "80", "52")
                time.sleep(0.4)
                gui.xdo("mousedown", "1")
                try:
                    for part in range(1, 11):
                        gui.xdo("mousemove", str(80 + part * 72), str(52 + part * 5))
                        time.sleep(0.05)
                finally:
                    gui.xdo("mouseup", "1")
            drag_many()
            wait(lambda: all((drop_root / tree.name / "line\nname.txt").exists() for tree in trees) and (drop_root / link.name).is_symlink(), "real multi-directory/symlink COPY into independent Sumi process")
            assert all((drop_root / tree.name / "line\nname.txt").read_bytes() == b"recursive external COPY" for tree in trees)
            assert os.readlink(drop_root / link.name) == os.readlink(link)
            assert all(tree.exists() for tree in trees) and link.is_symlink()
            prior = {path.name for path in drop_root.iterdir()}
            drag_many()
            drop_session.wait(lambda state: state["status"] == "error")
            assert {path.name for path in drop_root.iterdir()} == prior
            assert all((drop_root / tree.name / "line\nname.txt").read_bytes() == b"recursive external COPY" for tree in trees)
            drop_session.stop()
            peers.remove(drop_session)
            command("open", root)
            print("PASS real external multi-directory/symlink DND COPY and collision preflight preserve bytes and sources", flush=True)
            gui.xdo("windowfocus", "--sync", window)
            command("select", folder)
            gui.xdo("mousemove", "80", "52")
            gui.xdo("click", "3")
            gui.xdo("key", "Tab", "Tab", "Return")
            session.wait(lambda state: state["directory"] == str(folder))
            assert read("workspace.ini")["Workspace"]["count"] == "3"
            command("close-tab")
            command("activate-tab", "1")
            other = root / "ZZZ other directory"
            other.mkdir()
            command("refresh")
            command("select", folder)
            visible_before = set(gui.xdo("search", "--onlyvisible", "--pid", str(session.process.pid)).splitlines())
            gui.xdo("mousemove", "80", "52")
            gui.xdo("click", "3")
            gui.xdo("key", "Tab", "Tab")
            popups = set(gui.xdo("search", "--onlyvisible", "--pid", str(session.process.pid)).splitlines()) - visible_before
            assert popups, "Expected a real mapped GTK context popup surface"
            command("select", other)
            def mapped(popup):
                result = subprocess.run(["xwininfo", "-id", popup], capture_output=True, text=True, timeout=3)
                return result.returncode == 0 and "Map State: IsViewable" in result.stdout
            wait(lambda: not any(mapped(popup) for popup in popups), "stale context popup invalidated after selection changes")
            assert read("workspace.ini")["Workspace"]["count"] == "2"
            assert session.state()["directory"] == str(root)
            print("PASS real context New Tab uses clicked folder and stale menu activation cannot target a later selection", flush=True)
            command("bookmark-add", folder)
            saved = config / "sumi-explorer" / "bookmarks.ini"
            assert saved.exists() and read("bookmarks.ini")["Bookmark0"]["path"] == str(folder)
            second_directory = base / "second session"
            second_directory.mkdir()
            peer = gui.Session(args.binary.resolve(), root, second_directory)
            peers.append(peer)
            command("bookmark-add", root, peer=peer)
            command("bookmark-remove", folder)
            assert read("bookmarks.ini")["Bookmark0"]["path"] == str(root)
            command("bookmark-open", root)
            command("bookmark-open", "", good=False)
            print("PASS independent bookmark writers reload under exclusive lock and preserve updates", flush=True)
            saved.write_text("[Bookmarks]\nversion=1\ncount=0\n")
            with ThreadPoolExecutor(max_workers=2) as workers:
                jobs = [(session, folder), (peer, root)]
                futures = [workers.submit(host.command, "bookmark-add", str(path)) for host, path in jobs]
                responses = [future.result(timeout=10) for future in futures]
            for (host, path), response in zip(jobs, responses):
                assert response["status"] in {"ok", "error"}
                if response["status"] == "error":
                    command("bookmark-add", path, peer=host)
            command("bookmark-open", folder)
            snapshot = read("bookmarks.ini")
            assert {snapshot[f"Bookmark{i}"]["path"] for i in range(2)} == {str(root), str(folder)}
            command("open", root)
            print("PASS simultaneous independent writers retain both updates or explicitly retry lock collision", flush=True)
            new_bookmark = root / "saved despite snapshot failure"
            new_bookmark.mkdir()
            sidecar = directory / "bookmarks.ini"
            sidecar.unlink()
            sidecar.mkdir()
            response = command("bookmark-add", new_bookmark, good=False)
            assert "may have changed" in response["message"]
            persisted = configparser.RawConfigParser(strict=True)
            persisted.read_string(saved.read_text())
            assert str(new_bookmark) in {gui.unescape(persisted[group]["path"]) for group in persisted.sections() if group.startswith("Bookmark") and group != "Bookmarks"}
            sidecar.rmdir()
            command("bookmark-open", new_bookmark)
            command("open", root)
            broken_session = base / "snapshot failure startup"
            broken_session.mkdir()
            (broken_session / "bookmarks.ini").mkdir()
            broken_peer = gui.Session(args.binary.resolve(), root, broken_session)
            peers.append(broken_peer)
            assert "could not be published" in broken_peer.state()["message"]
            assert broken_peer.state()["directory"] == str(root)
            print("PASS bookmark snapshot I/O failures report actual saved truth and explicit startup warning", flush=True)
            for index in range(61):
                extra = base / f"bookmark limit {index}"
                extra.mkdir()
                command("bookmark-add", extra)
            assert read("bookmarks.ini")["Bookmarks"]["count"] == "64"
            extra = base / "bookmark rejected 65"
            extra.mkdir()
            before = saved.read_bytes()
            command("bookmark-add", extra, good=False)
            assert saved.read_bytes() == before
            print("PASS persistent 64-bookmark cap rejects excess without replacing saved bytes", flush=True)
            lock = saved.with_name("bookmarks.ini.lock")
            lock.write_text("competing writer")
            prior = saved.read_bytes()
            command("bookmark-add", folder, good=False)
            assert saved.read_bytes() == prior and lock.read_text() == "competing writer"
            lock.unlink()
            for corrupt in (b"", b"[Bookmarks]\nversion=01\ncount=0\n", b"[Bookmarks]\nversion=1\ncount=00\n", b"[Bookmarks]\nversion=1\ncount=0\nextra=1\n", b"[Bookmarks]\nversion=1\ncount=0\ncount=0\n", b"[Bookmarks]\nversion=1\ncount=0\n[Bookmarks]\n", b"\xff", b"x" * 1048577):
                saved.write_bytes(corrupt)
                command("bookmark-add", folder, good=False)
                assert saved.read_bytes() == corrupt
                assert set(read("bookmarks.ini")) == {"BookmarksError"}
            print("PASS corrupt and competing bookmark files remain byte unchanged", flush=True)
            cut_completion_unit(artifacts.resolve(), alpha, destination / beta.name, literal)
            print("PASS AOT unit: constructed partial-Cut outcomes retain failed/skipped entries and never replace newer real providers", flush=True)
        finally:
            for peer in peers:
                peer.stop()
            session.stop()
            shutil.copyfile(directory / "host.log", artifacts / "host.log")
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


if __name__ == "__main__":
    resource.setrlimit(resource.RLIMIT_AS, (8 * 1024**3, 8 * 1024**3))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path)
    run(parser.parse_args())
