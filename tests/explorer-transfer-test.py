#!/usr/bin/env python3
"""Real explicit-transfer/GTK external MOVE acceptance with bound receipts.

Run through heavy-run.sh, Xvfb and dbus-run-session with the 8 GiB limit.
The external sender observes GTK's drag-end delete_data flag and performs its
own cleanup; receiver destination snapshots alone never establish MOVE success.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import resource
import shlex
import subprocess
import tempfile
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
GROUPS = {
    "conflicts": ["replace-undo", "merge-preserves", "merge-replace-undo", "skip", "mismatch", "self-descendant", "symlink", "hardlink-alias", "move-merge-skip-retains", "move-replace-undo", "bounded-retention"],
    "recovery": ["restart-replacement-undo", "restart-merge-undo", "changed-backup-refused", "changed-output-refused", "unavailable-journal-refuses", "interrupted-backup-retained"],
    "workflow": ["gui-policy-cancel", "gui-replace-undo", "emacs-transfer"],
    "move-success": ["external-move-sender-cleanup", "external-move-undo-restores-source"],
    "move-failure": ["external-collision-none", "external-invalid-manifest-none", "external-partial-cancel-none", "external-partial-restart-undo", "external-partial-eviction-cleanup", "external-postcommit-cancel-restart-undo", "external-retained-original-changed-refused", "external-close-none"],
    "move-workflow": ["external-copy-default", "external-move-restart-undo"],
    "inverse-window": ["destination-change-after-source-restore-refused", "quarantine-race-retained-source-refused", "negative-completed-decoder-refused"],
    "copy-links": ["external-copy-link-manifest", "external-move-link-manifest-none", "external-copy-worker-close-none"],
}
SOURCES = ["explorer/sumi-explorer-native.el", "explorer/sumi-explorer.el", "explorer/platform.c", "explorer/platform.h", "explorer/build.el", "tests/explorer-transfer-test.py", "tests/explorer-gui-test.py", "tests/explorer-design-test.py", "tests/explorer-navigation-test.py", "tests/explorer-everyday-test.py", "tests/explorer-advanced-test.py"]


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tests" / filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


gui = module("sumi_transfer_gui", "explorer-gui-test.py")


def require(value, message):
    if not value:
        raise AssertionError(message)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "Duplicate JSON key")
        result[key] = value
    return result


def wait(check, message, seconds=15):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if check():
            return
        time.sleep(.04)
    raise AssertionError("Timed out: " + message)


def verify(path, selected="all"):
    record = json.loads(Path(path).read_text(), object_pairs_hook=unique)
    require(type(record) is dict and set(record) == {"schema", "kind", "sources", "binary", "groups"}, "Invalid receipt fields")
    require(type(record["schema"]) is int and record["schema"] == 1 and record["kind"] == "sumi-transfer-real", "Invalid receipt schema")
    require(record["sources"] == {name: digest(ROOT / name) for name in SOURCES}, "Receipt sources differ")
    require(set(record["binary"]) == {"path", "sha256"} and digest(record["binary"]["path"]) == record["binary"]["sha256"], "Binary differs")
    require(type(record["groups"]) is dict and set(record["groups"]) == set(GROUPS), "Invalid group set")
    for name, checks in GROUPS.items():
        item = record["groups"][name]
        require(type(item) is dict and set(item) == {"status", "checks", "error", "log", "sha256"}, "Invalid group fields")
        require(item["status"] in {"pass", "fail", "not-run"} and type(item["checks"]) is list and item["checks"] == checks[:len(item["checks"])], "Invalid checks/status")
        require(type(item["error"]) is str, "Invalid error")
        if item["status"] == "not-run":
            require(item["checks"] == [] and item["error"] == item["log"] == item["sha256"] == "", "Unrun group has evidence")
        else:
            require(digest(item["log"]) == item["sha256"], "Log differs")
            events = [json.loads(line, object_pairs_hook=unique) for line in Path(item["log"]).read_text().splitlines()]
            require(events == [{"check": check, "passed": True} for check in item["checks"]], "Log/check mismatch")
            if item["status"] == "pass":
                require(item["checks"] == checks and item["error"] == "", "Incomplete passing group")
            else:
                require(bool(item["error"]), "Failure has no diagnostic")
    for name in GROUPS if selected == "all" else [selected]:
        require(record["groups"][name]["status"] == "pass", "Group did not pass: " + name)
    return record


class Run:
    def __init__(self, binary, base, artifacts, name):
        self.binary, self.base, self.artifacts, self.name = binary, base, artifacts, name
        self.root = base / "root"
        self.root.mkdir()
        self.profile = base / "profile"
        self.sessions = []
        self.start()

    def start(self, preload=None):
        session = self.base / ("session-" + str(len(self.sessions)))
        session.mkdir(mode=0o700)
        previous = os.environ.get("SUMI_EXPLORER_UNDO_DIR")
        previous_preload = os.environ.get("LD_PRELOAD")
        os.environ["SUMI_EXPLORER_UNDO_DIR"] = str(self.profile)
        if preload:
            os.environ["LD_PRELOAD"] = str(preload)
        try:
            self.s = gui.Session(self.binary, self.root, session)
        finally:
            if previous is None:
                os.environ.pop("SUMI_EXPLORER_UNDO_DIR", None)
            else:
                os.environ["SUMI_EXPLORER_UNDO_DIR"] = previous
            if previous_preload is None:
                os.environ.pop("LD_PRELOAD", None)
            else:
                os.environ["LD_PRELOAD"] = previous_preload
        self.sessions.append(self.s)

    def command(self, command, path="", **fields):
        self.s.serial += 1
        text = f"[Explorer]\nversion=1\nrequest={self.s.serial}\ncommand={command}\npath={gui.escape(str(path))}\n"
        text += "".join(key + "=" + gui.escape(str(value)) + "\n" for key, value in fields.items())
        temporary = self.s.directory / "request.tmp"
        temporary.write_text(text)
        temporary.replace(self.s.directory / "request.ini")
        return self.s.wait(lambda state: state["request"] == str(self.s.serial), seconds=30, response=True)

    def transfer(self, source, dest, policy, mode="copy", ok=True):
        result = self.command("transfer", source, destination=dest, policy=policy, mode=mode)
        require((result["status"] == "ok") == ok, repr(result))
        return result

    def undo(self, ok=True):
        import configparser
        parser = configparser.RawConfigParser(strict=True)
        parser.read(self.s.directory / "undo.ini")
        require(parser.getint("Undo", "count") > 0, "No Undo group")
        result = self.command("undo", parser["Undo0"]["id"])
        require((result["status"] == "ok") == ok, repr(result))
        return result

    def files(self, prefix):
        source, dest = self.root / (prefix + "-source"), self.root / (prefix + "-dest")
        source.write_bytes(b"new bytes\n")
        dest.write_bytes(b"old bytes\n")
        return source, dest

    def restart(self):
        self.s.stop()
        self.start()

    def stop(self):
        for session in self.sessions:
            if not session.log.closed:
                session.stop()


def conflicts(run, passed):
    s, d = run.files("replace")
    run.transfer(s, d, "replace"); require(s.read_bytes() == d.read_bytes(), "Replacement differs")
    run.undo(); require(d.read_bytes() == b"old bytes\n", "Replacement Undo lost old bytes"); passed("replace-undo")
    source, dest = run.root / "tree-source", run.root / "tree-dest"
    source.mkdir(); dest.mkdir()
    (source / "same").write_text("new"); (dest / "same").write_text("old")
    (source / "added").write_text("added"); (dest / "unrelated").write_text("keep")
    run.transfer(source, dest, "merge")
    require((dest / "same").read_text() == "old" and (dest / "added").read_text() == "added" and (dest / "unrelated").read_text() == "keep", "Merge lost entries")
    run.undo(); require(not (dest / "added").exists() and (dest / "unrelated").read_text() == "keep", "Merge Undo lost unrelated entries"); passed("merge-preserves")
    run.transfer(source, dest, "merge-replace"); require((dest / "same").read_text() == "new", "Merge replacement not applied")
    run.undo(); require((dest / "same").read_text() == "old" and not (dest / "added").exists() and (dest / "unrelated").read_text() == "keep", "Merge replacement Undo incorrect"); passed("merge-replace-undo")
    run.transfer(s, d, "skip"); require(d.read_bytes() == b"old bytes\n", "Skip changed destination"); passed("skip")
    run.transfer(s, dest, "replace", ok=False); run.transfer(source, d, "merge-replace", ok=False); require(d.is_file() and dest.is_dir(), "Mismatch changed types"); passed("mismatch")
    run.transfer(s, s, "replace", ok=False); run.transfer(source, source / "child", "replace", ok=False); require(not (source / "child").exists(), "Descendant changed"); passed("self-descendant")
    link = run.root / "link"; link.symlink_to(dest, target_is_directory=True)
    run.transfer(s, link / "new", "replace", ok=False); require(not (dest / "new").exists(), "Followed destination symlink"); passed("symlink")
    hard = run.root / "hardlink"; os.link(s, hard)
    run.transfer(s, hard, "replace", ok=False); require(os.path.samefile(s, hard), "Hardlink alias was replaced"); passed("hardlink-alias")
    run.transfer(source, dest, "merge", "move"); require(source.is_dir() and (source / "same").read_text() == "new", "Partial merge removed source")
    run.undo(); passed("move-merge-skip-retains")
    s, d = run.files("move")
    run.transfer(s, d, "replace", "move"); require(not s.exists() and d.read_bytes() == b"new bytes\n", "Move did not finish")
    run.undo(); require(s.read_bytes() == b"new bytes\n" and d.read_bytes() == b"old bytes\n", "Move Undo lost source/prestate"); passed("move-replace-undo")
    s, d = run.files("retention"); d.write_bytes(b"retention-old")
    for index in range(34):
        s.write_text("retention-new-" + str(index)); run.transfer(s, d, "replace")
    import configparser
    parser = configparser.RawConfigParser(strict=True); parser.read(run.s.directory / "undo.ini")
    require(parser.getint("Undo", "count") == 32, "History did not retain exactly 32 groups")
    require(not any(path.read_bytes() == b"retention-old" for path in run.root.glob(".sumi-transfer-*/old")), "Durably obsolete backup was leaked")
    run.restart(); run.undo(); require(d.read_text() == "retention-new-32", "Retained history failed after restart"); passed("bounded-retention")


def recovery(run, passed):
    s, d = run.files("restart")
    run.transfer(s, d, "replace"); run.restart(); run.undo()
    require(d.read_bytes() == b"old bytes\n", "Restart Undo lost prestate"); passed("restart-replacement-undo")
    source, dest = run.root / "restart-merge-source", run.root / "restart-merge-dest"
    source.mkdir(); dest.mkdir()
    (source / "same").write_text("new"); (source / "added").write_text("added")
    (dest / "same").write_text("old"); (dest / "unrelated").write_text("keep")
    run.transfer(source, dest, "merge-replace"); run.restart(); run.undo()
    require((dest / "same").read_text() == "old" and not (dest / "added").exists() and (dest / "unrelated").read_text() == "keep", "Restart merge Undo lost prestate/unrelated entries")
    passed("restart-merge-undo")
    s, d = run.files("tamper-backup"); run.transfer(s, d, "replace")
    backups = list(run.root.glob(".sumi-transfer-*/old")); require(len(backups) == 1, "Expected one owned backup")
    backups[0].write_bytes(b"external backup change")
    run.undo(ok=False); require(d.read_bytes() == b"new bytes\n" and backups[0].read_bytes() == b"external backup change", "Tampered backup was mutated"); passed("changed-backup-refused")
    s, d = run.files("tamper-output"); run.transfer(s, d, "replace"); d.write_bytes(b"external output change")
    run.undo(ok=False); require(d.read_bytes() == b"external output change", "Tampered output was removed"); passed("changed-output-refused")
    s, d = run.files("unavailable")
    run.profile.chmod(0o500)
    try:
        run.transfer(s, d, "replace", ok=False)
    finally:
        run.profile.chmod(0o700)
    require(s.read_bytes() == b"new bytes\n" and d.read_bytes() == b"old bytes\n", "Unavailable recovery mutated files"); passed("unavailable-journal-refuses")
    injection = run.artifacts / "interrupt-backup.c"
    injection.write_text(r'''#define _GNU_SOURCE
#include <dlfcn.h>
#include <string.h>
#include <unistd.h>
int renameat2(int a,const char *source,int b,const char *destination,unsigned flags) {
 int (*real)(int,const char*,int,const char*,unsigned)=dlsym(RTLD_NEXT,"renameat2");
 int result=real(a,source,b,destination,flags); const char *base=strrchr(destination,'/');
 if(!result && strstr(destination,"/.sumi-transfer-") && base && !strcmp(base,"/old")) _exit(86);
 return result;
}
''')
    library = run.artifacts / "interrupt-backup.so"
    subprocess.run([os.environ.get("CC", "cc"), "-shared", "-fPIC", str(injection), "-o", str(library), "-ldl"], check=True, timeout=30)
    run.s.stop(); run.start(preload=library)
    s, d = run.files("interrupt")
    previous_backups = set(run.root.glob(".sumi-transfer-*/old"))
    try:
        run.transfer(s, d, "replace")
    except AssertionError:
        require(run.s.process.poll() == 86, "Expected the real post-backup crash boundary")
    else:
        raise AssertionError("Crash boundary injection was not exercised")
    backups = [path for path in run.root.glob(".sumi-transfer-*/old") if path not in previous_backups and path.read_bytes() == b"old bytes\n"]
    require(len(backups) == 1 and not d.exists(), "Backup crash boundary did not retain prestate")
    run.s.stop(); run.start()
    run.transfer(s, d, "replace", ok=False)
    require(backups[0].read_bytes() == b"old bytes\n" and not d.exists() and s.read_bytes() == b"new bytes\n", "Interrupted recovery was silently consumed")
    passed("interrupted-backup-retained")


def workflow(run, passed):
    s, d = run.files("gui")
    run.command("refresh")
    run.command("select", s)
    ui = run.s.ui(); ui.click("More", "button"); ui.click("Copy to…", "button")
    ui.find("Replace", "button"); ui.find("Merge", "button"); ui.find("Merge and replace", "button"); ui.find("Skip", "button")
    ui.click("Cancel", "button"); require(d.read_bytes() == b"old bytes\n", "Cancel changed destination"); passed("gui-policy-cancel")
    ui.click("More", "button"); ui.click("Copy to…", "button"); ui.click("Destination path", "text")
    gui.xdo("key", "ctrl+a"); gui.xdo("type", "--clearmodifiers", str(d)); ui.click("Replace", "button")
    wait(lambda: d.read_bytes() == b"new bytes\n" and "completed" in run.s.state().get("message", ""), "GUI replace")
    run.undo(); require(d.read_bytes() == b"old bytes\n", "GUI Undo lost prestate"); passed("gui-replace-undo")
    driver = run.artifacts / "emacs-transfer.el"
    client = ROOT / "explorer/sumi-explorer.el"
    driver.write_text(f'''(load {json.dumps(str(client))} nil t)
(let ((process-environment (copy-sequence process-environment)))
 (setenv "SUMI_EXPLORER_UNDO_DIR" {json.dumps(str(run.base / "emacs-profile"))})
 (unwind-protect
  (progn (sumi-explorer-start {json.dumps(str(run.root))} {json.dumps(str(run.binary))})
   (sumi-explorer-transfer {json.dumps(str(s))} {json.dumps(str(d))} 'replace 'copy 20)
   (unless (equal (with-temp-buffer (insert-file-contents {json.dumps(str(d))}) (buffer-string)) "new bytes\\n") (error "Emacs transfer failed"))
   (sumi-explorer-undo nil 20)
   (unless (equal (with-temp-buffer (insert-file-contents {json.dumps(str(d))}) (buffer-string)) "old bytes\\n") (error "Emacs transfer Undo failed")))
  (when (process-live-p sumi-explorer--process) (sumi-explorer-stop))))
''')
    with (run.artifacts / "emacs-transfer.log").open("wb") as log:
        subprocess.run(["emacs", "-Q", "--batch", "-l", str(driver)], check=True, timeout=60, stdout=log, stderr=subprocess.STDOUT)
    passed("emacs-transfer")


SENDER = r'''
#include <gtk/gtk.h>
#include <glib/gstdio.h>
static char **paths; static int n; static char *record; static GMainLoop *loop;
static GdkContentProvider *prepare(GtkDragSource *s, double x, double y, gpointer u) {
 (void)s;(void)x;(void)y;(void)u; GFile **array=g_new0(GFile*,n);
 for(int i=0;i<n;i++) array[i]=strstr(paths[i],"://")?g_file_new_for_uri(paths[i]):g_file_new_for_path(paths[i]);
 GdkFileList *files=gdk_file_list_new_from_array(array,n);
 GdkContentProvider *p=gdk_content_provider_new_typed(GDK_TYPE_FILE_LIST,files);
 g_boxed_free(GDK_TYPE_FILE_LIST,files); for(int i=0;i<n;i++)g_object_unref(array[i]);g_free(array);return p;
}
static void ended(GtkDragSource *s,GdkDrag *d,gboolean delete_data,gpointer u) {
 (void)s;(void)u; unsigned action=gdk_drag_get_selected_action(d), deleted=0;
 if(delete_data && action==GDK_ACTION_MOVE)for(int i=0;i<n;i++)if(g_remove(paths[i])==0)deleted++;
 char *data=g_strdup_printf("{\"action\":%u,\"delete_data\":%s,\"deleted\":%u}\n",action,delete_data?"true":"false",deleted);
 g_file_set_contents(record,data,-1,NULL);g_free(data);
}
int main(int argc,char **argv){gtk_init();record=argv[1];unsigned action=atoi(argv[2]);paths=argv+3;n=argc-3;
 GtkWidget *w=gtk_window_new();gtk_window_set_title(GTK_WINDOW(w),"Sumi transfer sender");gtk_window_set_default_size(GTK_WINDOW(w),350,250);
 GtkWidget *label=gtk_label_new("Drag external files");gtk_window_set_child(GTK_WINDOW(w),label);
 GtkDragSource *s=gtk_drag_source_new();gtk_drag_source_set_actions(s,action);g_signal_connect(s,"prepare",G_CALLBACK(prepare),NULL);g_signal_connect(s,"drag-end",G_CALLBACK(ended),NULL);
 gtk_widget_add_controller(label,GTK_EVENT_CONTROLLER(s));gtk_window_present(GTK_WINDOW(w));loop=g_main_loop_new(NULL,FALSE);g_main_loop_run(loop);return 0;}
'''


def sender_build(artifacts):
    source, binary = artifacts / "external-sender.c", artifacts / "external-sender"
    source.write_text(SENDER)
    flags = shlex.split(subprocess.check_output(["pkg-config", "--cflags", "--libs", "gtk4"], text=True))
    with (artifacts / "external-sender-build.log").open("wb") as log:
        subprocess.run([os.environ.get("CC", "cc"), "-std=c11", "-Wall", "-Wextra", str(source), "-o", str(binary), *flags], check=True, timeout=60, stdout=log, stderr=subprocess.STDOUT)
    return binary


def ack_barrier(run):
    """Pause the actual worker after its durable commit, before GTK completion."""
    source, library = run.artifacts / "ack-barrier.c", run.artifacts / "ack-barrier.so"
    ready, release = run.artifacts / "ack-ready.json", run.artifacts / "ack-release"
    source.write_text(r'''#define _GNU_SOURCE
#include <gio/gio.h>
#include <dlfcn.h>
#include <unistd.h>
void g_task_return_boolean(GTask *task,gboolean result) {
 void (*real)(GTask*,gboolean)=dlsym(RTLD_NEXT,"g_task_return_boolean");
 guint64 *job=g_task_get_task_data(task); const char *base=g_getenv("SUMI_TRANSFER_ACK_BARRIER");
 if(base && job && job[0]==13) {
  guint64 *store=(guint64*)job[34]; gboolean committed=job[6] && store && !store[14] && store[15];
  char *ready=g_build_filename(base,"ack-ready.json",NULL), *release=g_build_filename(base,"ack-release",NULL);
  g_file_set_contents(ready,committed?"{\"committed\":true}\n":"{\"committed\":false}\n",-1,NULL);
  unsigned i=0; while(!g_file_test(release,G_FILE_TEST_EXISTS) && i++<30000) usleep(1000);
  g_free(ready);g_free(release);
 }
 real(task,result);
}
''')
    flags = shlex.split(subprocess.check_output(["pkg-config", "--cflags", "--libs", "gio-2.0"], text=True))
    subprocess.run([os.environ.get("CC", "cc"), "-shared", "-fPIC", str(source), "-o", str(library), "-ldl", *flags], check=True, timeout=30)
    for path in (ready, release):
        path.unlink(missing_ok=True)
    return library, ready, release


def late_cancel(run, source):
    library, ready, release = ack_barrier(run)
    run.s.stop()
    previous = os.environ.get("SUMI_TRANSFER_ACK_BARRIER")
    os.environ["SUMI_TRANSFER_ACK_BARRIER"] = str(run.artifacts)
    try:
        run.start(preload=library)
    finally:
        if previous is None:
            os.environ.pop("SUMI_TRANSFER_ACK_BARRIER", None)
        else:
            os.environ["SUMI_TRANSFER_ACK_BARRIER"] = previous
    def cancel_committed(window):
        wait(lambda: ready.exists(), "actual transfer worker committed before acknowledgement")
        require(json.loads(ready.read_text()) == {"committed": True}, "Barrier did not observe a durable committed transfer")
        run.s.ui().click("Cancel", "button")
        release.write_text("release\n")
    try:
        result = drag(run, [source], during=cancel_committed)
    finally:
        release.write_text("release\n")
    require(result["delete_data"] is False and result["deleted"] == 0 and source.exists(), repr(result))
    return result


def drag(run, paths, action=2, during=None):
    sender = sender_build(run.artifacts)
    record = run.artifacts / ("sender-result-" + str(time.time_ns()) + ".json")
    with (run.artifacts / (record.stem + ".log")).open("wb") as log:
        process = subprocess.Popen([str(sender), str(record), str(action), *map(str, paths)], stdout=log, stderr=subprocess.STDOUT)
        try:
            source = gui.xdo("search", "--sync", "--pid", str(process.pid), "--name", "^Sumi transfer sender$").splitlines()[-1]
            destination = gui.xdo("search", "--sync", "--pid", str(run.s.process.pid), "--name", "^Sumi Explorer$").splitlines()[-1]
            gui.xdo("windowsize", destination, "900", "700"); gui.xdo("windowmove", destination, "500", "0")
            gui.xdo("windowmove", source, "0", "100"); gui.xdo("windowfocus", source)
            point = run.s.ui().point(role="list")
            # A populated list's accessible allocation includes rows outside
            # its scroll viewport. Drop within the actual visible window.
            geometry = dict(line.split("=", 1) for line in gui.xdo("getwindowgeometry", "--shell", destination).splitlines())
            point = (point[0], min(point[1], int(geometry["Y"]) + int(geometry["HEIGHT"]) - 100))
            gui.xdo("mousemove", "170", "220"); gui.xdo("mousedown", "1")
            for index in range(1, 25):
                x = 170 + (point[0] - 170) * index // 24
                y = 220 + (point[1] - 220) * index // 24
                gui.xdo("mousemove", str(x), str(y)); time.sleep(.025)
            gui.xdo("mouseup", "1")
            if during:
                during(destination)
            wait(lambda: record.exists(), "external sender drag-end", seconds=30)
            return json.loads(record.read_text())
        finally:
            process.terminate(); process.wait(timeout=5)


def moves(run, passed, group):
    outside = run.base / "sender"; outside.mkdir()
    source = outside / "external 日本語.txt"; source.write_bytes(b"sender-owned source\n")
    dest = run.root / source.name
    if group == "move-success":
        result = drag(run, [source]); require(result == {"action": 2, "delete_data": True, "deleted": 1} and not source.exists() and dest.read_bytes() == b"sender-owned source\n", repr(result)); passed("external-move-sender-cleanup")
        run.undo(); require(source.read_bytes() == b"sender-owned source\n" and not dest.exists(), "External MOVE Undo lost source"); passed("external-move-undo-restores-source")
    elif group == "move-failure":
        dest.write_bytes(b"existing destination")
        result = drag(run, [source]); require(result["delete_data"] is False and result["deleted"] == 0 and source.exists() and dest.read_bytes() == b"existing destination", repr((result, run.s.state(), dest.read_bytes() if dest.exists() else None))); passed("external-collision-none")
        dest.unlink()
        result = drag(run, [source, "sftp://invalid.example/unsupported"])
        require(result["delete_data"] is False and result["deleted"] == 0 and source.exists() and not dest.exists(), repr(result)); passed("external-invalid-manifest-none")
        large = outside / "large.bin"
        with large.open("wb") as output:
            output.truncate(512 * 1024**2)
        def cancel_after_partial(window):
            wait(lambda: dest.exists(), "first external item completed before Cancel", seconds=30)
            run.s.ui().click("Cancel", "button")
        result = drag(run, [source, large], during=cancel_after_partial)
        require(result["delete_data"] is False and result["deleted"] == 0 and source.exists() and large.exists() and dest.read_bytes() == source.read_bytes(), repr(result)); passed("external-partial-cancel-none")
        run.restart(); run.undo()
        require(source.read_bytes() == b"sender-owned source\n" and large.exists() and not dest.exists() and not (run.root / large.name).exists(), "Restart Undo failed for sender-retained partial MOVE")
        require(not list(run.root.glob(".sumi-transfer-*/new")), "Consumed partial MOVE lost ownership of private stage")
        passed("external-partial-restart-undo")
        folder = outside / "evicted-partial"; folder.mkdir()
        with (folder / "large.bin").open("wb") as output:
            output.truncate(512 * 1024**2)
        def cancel_directory_stage(window):
            wait(lambda: dest.exists() and any(path.is_dir() for path in run.root.glob(".sumi-transfer-*/new")), "actual directory stage exists before Cancel", seconds=30)
            run.s.ui().click("Cancel", "button")
        result = drag(run, [source, folder], during=cancel_directory_stage)
        require(result["delete_data"] is False and result["deleted"] == 0 and folder.exists(), repr(result))
        stages = list(run.root.glob(".sumi-transfer-*/new"))
        require(stages and any(path.is_dir() for path in stages), "No partial stage was exercised")
        for index in range(34):
            require(run.command("mkdir", run.root / ("ordinary-eviction-" + str(index)))["status"] == "ok", "Ordinary operation failed")
        require(all(not path.exists() and not path.parent.exists() for path in stages), "Eviction leaked a verified partial stage or its ownership directory")
        require(source.exists() and folder.exists() and dest.read_bytes() == source.read_bytes(), "Eviction damaged source/completed destination")
        passed("external-partial-eviction-cleanup")
        dest.unlink()
        late_cancel(run, source)
        require(dest.read_bytes() == source.read_bytes(), "Committed pre-acknowledgement destination missing")
        run.restart(); run.undo()
        require(source.read_bytes() == b"sender-owned source\n" and not dest.exists(), "Restart Undo failed after post-commit/pre-acknowledgement cancellation")
        passed("external-postcommit-cancel-restart-undo")
        late_cancel(run, source); run.restart()
        source.write_bytes(b"external retained-original change\n")
        run.undo(ok=False)
        require(source.read_bytes() == b"external retained-original change\n" and dest.read_bytes() == b"sender-owned source\n", "Changed retained original or destination was mutated")
        passed("external-retained-original-changed-refused")
        run.command("open", run.root)
        second = outside / "close.bin"
        with second.open("wb") as output:
            output.truncate(512 * 1024**2)
        def close_pending(window):
            wait(lambda: run.s.state().get("message") == "Operation in progress", "external copy pending before close")
            current = gui.xdo("search", "--onlyvisible", "--pid", str(run.s.process.pid), "--name", "^Sumi Explorer$").splitlines()[-1]
            close_driver = "import importlib.util,sys; s=importlib.util.spec_from_file_location('close',sys.argv[1]); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); m.normal_close(sys.argv[2])"
            subprocess.run(["python3", "-c", close_driver, str(ROOT / "tests/explorer-everyday-test.py"), current], check=True, timeout=5)
        result = drag(run, [second], during=close_pending)
        require(result["delete_data"] is False and result["deleted"] == 0 and second.exists(), repr(result)); passed("external-close-none")
    else:
        result = drag(run, [source], 1); require(result["action"] == 1 and result["delete_data"] is False and source.exists() and dest.read_bytes() == source.read_bytes(), repr(result)); passed("external-copy-default")
        run.undo(); require(not dest.exists() and source.exists(), "COPY Undo damaged source")
        result = drag(run, [source]); require(result["delete_data"] is True and not source.exists(), repr(result))
        run.restart(); run.undo(); require(source.read_bytes() == b"sender-owned source\n" and not dest.exists(), "Restart external MOVE Undo failed"); passed("external-move-restart-undo")


def negatives(path):
    original = json.loads(path.read_text())
    mutations = []
    value = copy.deepcopy(original); value["binary"]["sha256"] = "0" * 64; mutations.append(value)
    value = copy.deepcopy(original); value["sources"][SOURCES[0]] = "0" * 64; mutations.append(value)
    value = copy.deepcopy(original); name = next(n for n in GROUPS if value["groups"][n]["status"] == "pass"); value["groups"][name]["checks"] = []; mutations.append(value)
    value = copy.deepcopy(original); value["groups"][name]["sha256"] = "0" * 64; mutations.append(value)
    for index, value in enumerate(mutations):
        probe = path.with_name("negative-" + str(index) + ".json"); probe.write_text(json.dumps(value))
        try:
            verify(probe, name)
        except (AssertionError, ValueError):
            pass
        else:
            raise AssertionError("Receipt negative control passed")
        finally:
            probe.unlink()
    print("PASS 4 malformed-receipt negative controls", flush=True)


def copy_links(run, passed):
    outside = run.base / "link-sender"; outside.mkdir()
    source = outside / "linked.txt"; source.write_bytes(b"sender-owned link target\n")
    link = outside / "relative link"; link.symlink_to(source.name)
    result = drag(run, [source, link], 1)
    require(result == {"action": 1, "delete_data": False, "deleted": 0}, repr(result))
    require((run.root / source.name).read_bytes() == source.read_bytes() and os.readlink(run.root / link.name) == source.name, "COPY did not preserve link entry")
    require(source.exists() and link.is_symlink(), "COPY sender lost originals")
    passed("external-copy-link-manifest")
    other = outside / "move.txt"; other.write_bytes(b"whole MOVE manifest retained\n")
    other_link = outside / "move link"; other_link.symlink_to(other.name)
    result = drag(run, [other, other_link], 2)
    require(result == {"action": 0, "delete_data": False, "deleted": 0}, repr(result))
    require(other.read_bytes() == b"whole MOVE manifest retained\n" and other_link.is_symlink() and not (run.root / other.name).exists() and not (run.root / other_link.name).is_symlink(), "Unsupported MOVE filtered or deleted manifest entries")
    passed("external-move-link-manifest-none")
    closing = outside / "closing.txt"; closing.write_bytes(b"close retains original\n")
    stub, library = run.artifacts / "copy-close.c", run.artifacts / "copy-close.so"
    ready, release = run.artifacts / "copy-close-ready", run.artifacts / "copy-close-release"
    ready.unlink(missing_ok=True); release.unlink(missing_ok=True)
    stub.write_text(r'''#define _GNU_SOURCE
#include <gio/gio.h>
#include <dlfcn.h>
#include <string.h>
#include <unistd.h>
gboolean g_file_copy(GFile*a,GFile*b,GFileCopyFlags f,GCancellable*c,GFileProgressCallback p,gpointer d,GError**e){
 gboolean(*real)(GFile*,GFile*,GFileCopyFlags,GCancellable*,GFileProgressCallback,gpointer,GError**)=dlsym(RTLD_NEXT,"g_file_copy");
 char*path=g_file_get_path(a);const char*source=g_getenv("SUMI_COPY_CLOSE_SOURCE"),*base=g_getenv("SUMI_COPY_CLOSE_DIR");
 if(source&&base&&path&&!strcmp(source,path)){char*ready=g_build_filename(base,"copy-close-ready",NULL),*release=g_build_filename(base,"copy-close-release",NULL);g_file_set_contents(ready,"ready\n",-1,NULL);unsigned i=0;while(!g_file_test(release,G_FILE_TEST_EXISTS)&&i++<30000)usleep(1000);g_free(ready);g_free(release);}g_free(path);return real(a,b,f,c,p,d,e);
}''')
    flags = shlex.split(subprocess.check_output(["pkg-config", "--cflags", "--libs", "gio-2.0"], text=True))
    subprocess.run([os.environ.get("CC", "cc"), "-shared", "-fPIC", str(stub), "-o", str(library), "-ldl", *flags], check=True, timeout=30)
    run.s.stop()
    values = {"SUMI_COPY_CLOSE_SOURCE": str(closing), "SUMI_COPY_CLOSE_DIR": str(run.artifacts)}
    previous = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        run.start(preload=library)
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    everyday = module("copy_close_everyday", "explorer-everyday-test.py")
    def close(window):
        wait(lambda: ready.exists(), "actual COPY worker entered")
        everyday.normal_close(window)
        release.write_text("release\n")
    try:
        result = drag(run, [closing], 1, during=close)
    finally:
        release.write_text("release\n")
    require(result == {"action": 0, "delete_data": False, "deleted": 0}, repr(result))
    run.s.process.wait(timeout=8)
    require(run.s.process.returncode == 0 and closing.read_bytes() == b"close retains original\n" and not (run.root / closing.name).exists(), "COPY close lost source or freed context before worker completion")
    passed("external-copy-worker-close-none")


def inverse_window(run, passed):
    def restart_observed():
        run.restart()
        state = run.s.state()
        if "Interrupted operation" not in state["message"]:
            # Preserve the failing observation before TemporaryDirectory removes
            # its profile. This does not retry or change the acceptance state.
            directory = run.artifacts / ("inverse-restart-failure-" + run.s.directory.name)
            directory.mkdir(exist_ok=True)
            (directory / "state.json").write_text(json.dumps(state, indent=2) + "\n")
            manifest = {}
            copied = 0
            for path in sorted(run.profile.iterdir())[:256]:
                entry = {"name": path.name, "symlink": path.is_symlink()}
                if path.is_file() and not path.is_symlink():
                    size = path.stat().st_size
                    entry["size"] = size
                    if size <= 16777216 and copied + size <= 67108864:
                        data = path.read_bytes()
                        entry["sha256"] = hashlib.sha256(data).hexdigest()
                        (directory / path.name).write_bytes(data)
                        copied += len(data)
                    else:
                        entry["omitted"] = "Diagnostic byte limit"
                manifest[path.name] = entry
            (directory / "profile.json").write_text(json.dumps(manifest, indent=2) + "\n")
            print("DIAGNOSTIC failed restart state/profile bytes: " + str(directory), flush=True)
        return state

    outside = run.base / "window-sender"; outside.mkdir()
    source = outside / "window.txt"; source.write_bytes(b"recorded destination preimage\n")
    dest = run.root / source.name
    require(drag(run, [source])["delete_data"] is True and not source.exists(), "Initial external MOVE did not finish")
    stub, library = run.artifacts / "inverse-window.c", run.artifacts / "inverse-window.so"
    stub.write_text(r'''#define _GNU_SOURCE
#include <gio/gio.h>
#include <dlfcn.h>
#include <string.h>
#include <unistd.h>
static void barrier(void) {
 const char *base=g_getenv("SUMI_TRANSFER_INVERSE_BARRIER");if(!base)return;
 char *ready=g_build_filename(base,"inverse-ready",NULL),*release=g_build_filename(base,"inverse-release",NULL);
 g_file_set_contents(ready,"ready\n",-1,NULL);unsigned i=0;while(!g_file_test(release,G_FILE_TEST_EXISTS)&&i++<30000)usleep(1000);
 g_free(ready);g_free(release);
}
gboolean g_file_copy(GFile *a,GFile *b,GFileCopyFlags f,GCancellable *c,GFileProgressCallback p,gpointer d,GError **e) {
 gboolean(*real)(GFile*,GFile*,GFileCopyFlags,GCancellable*,GFileProgressCallback,gpointer,GError**)=dlsym(RTLD_NEXT,"g_file_copy");
 gboolean good=real(a,b,f,c,p,d,e);char *path=g_file_get_path(b);const char *source=g_getenv("SUMI_TRANSFER_RESTORED_SOURCE");
 if(good&&source&&path&&!strcmp(source,path))barrier();g_free(path);return good;
}
int renameat2(int a,const char *s,int b,const char *d,unsigned f) {
 int(*real)(int,const char*,int,const char*,unsigned)=dlsym(RTLD_NEXT,"renameat2");
 const char *target=g_getenv("SUMI_TRANSFER_RACE_DESTINATION");if(target&&!strcmp(target,s)&&strstr(d,"/.sumi-transfer-")&&g_str_has_suffix(d,"/new"))barrier();
 return real(a,s,b,d,f);
}
''')
    flags = shlex.split(subprocess.check_output(["pkg-config", "--cflags", "--libs", "gio-2.0"], text=True))
    subprocess.run([os.environ.get("CC", "cc"), "-shared", "-fPIC", str(stub), "-o", str(library), "-ldl", *flags], check=True, timeout=30)
    ready, release = run.artifacts / "inverse-ready", run.artifacts / "inverse-release"
    def restart_injected(variable):
        run.s.stop()
        ready.unlink(missing_ok=True); release.unlink(missing_ok=True)
        values = {"SUMI_TRANSFER_INVERSE_BARRIER": str(run.artifacts), variable: str(source if variable.endswith("SOURCE") else dest)}
        previous = {key: os.environ.get(key) for key in values}
        os.environ.update(values)
        try:
            run.start(preload=library)
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
    def raced_undo():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            result = executor.submit(run.undo, False)
            try:
                wait(lambda: ready.exists(), "actual inverse mutation boundary")
                dest.write_bytes(b"external mutation after preflight\n")
            finally:
                release.write_text("release\n")
            return result.result(timeout=30)
    restart_injected("SUMI_TRANSFER_RESTORED_SOURCE")
    state = raced_undo()
    require(dest.read_bytes() == b"external mutation after preflight\n" and source.read_bytes() == b"recorded destination preimage\n", "Changed destination or actual restored source was lost")
    require("Source restored at:" in state["message"] and str(source) in state["message"], "Actual restored source outcome was not reported")
    require("Interrupted operation" in restart_observed()["message"], "Interrupted inverse intent not retained after restart")
    require(run.command("undo", "1")["status"] == "error" and dest.read_bytes() == b"external mutation after preflight\n", "Restart consumed ambiguous destination")
    passed("destination-change-after-source-restore-refused")
    # Start a deliberate independent profile for the second race; the first
    # profile remains blocked with its bytes and durable recovery intent.
    run.s.stop(); run.profile = run.base / "retained-race-profile"
    source = outside / "retained-window.txt"; dest = run.root / source.name
    source.write_bytes(b"retained source preimage\n")
    run.start(); late_cancel(run, source)
    restart_injected("SUMI_TRANSFER_RACE_DESTINATION")
    raced_undo()
    stages = [p for p in run.root.glob(".sumi-transfer-*/new") if p.is_file() and p.read_bytes() == b"external mutation after preflight\n"]
    require(stages and source.read_bytes() == b"retained source preimage\n", "Post-rename changed object gained deletion authority")
    require("Interrupted operation" in restart_observed()["message"] and stages[0].exists(), "Quarantine recovery was discarded on restart")
    passed("quarantine-race-retained-source-refused")
    # Feed canonical GKeyFile action data extracted from a real op13 journal
    # into the actual full native decoder, including a negative state value.
    metadata = []
    for path in run.profile.glob("*.journal"):
        raw = path.read_bytes(); position = raw.find(b"[Transfer]\n")
        if position >= 8:
            # The journal's zero/null distinction encodes byte length + 1.
            length = int.from_bytes(raw[position-8:position], "little") - 1
            value = raw[position:position+length]
            if len(value) == length and value.startswith(b"[Transfer]\n"):
                metadata.append(value)
    require(metadata, "Actual op13 journal action data not found")
    valid = run.artifacts / "decoder-valid.ini"; invalid = run.artifacts / "decoder-negative.ini"
    valid.write_bytes(metadata[0]); invalid.write_bytes(metadata[0].replace(b"completed=1\n", b"completed=-1\n"))
    require(invalid.read_bytes() != valid.read_bytes(), "Decoder negative fixture did not change actual state")
    native = (ROOT / "explorer/sumi-explorer-native.el").read_text()
    fixture = run.artifacts / "transfer-decoder.el"
    fixture.write_text(native[:native.index(" (defun main (")] + r'''
 (defun main (argc argv)
   (let ((slot (extern-call g_malloc0 8)) (actions 0) (status 7))
     (seq (extern-call g_file_get_contents (ptr-read-u64 argv 8) slot 0 0)
       (setq actions (sumi_explorer_transfer_decode (ptr-read-u64 slot 0)))
       (if actions (seq (setq status 0) (extern-call g_ptr_array_unref actions)))
       (extern-call g_free (ptr-read-u64 slot 0)) (extern-call g_free slot) status))))
''')
    advanced = module("transfer_decoder_build", "explorer-advanced-test.py")
    executable = advanced.build_aot_fixture(fixture, run.artifacts, Path(os.environ["NELISP_REPO_ROOT"]))
    require(subprocess.run([str(executable), str(valid)], timeout=10).returncode == 0, "Actual decoder rejected valid data")
    require(subprocess.run([str(executable), str(invalid)], timeout=10).returncode == 7, "Actual decoder accepted negative completed")
    passed("negative-completed-decoder-refused")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path)
    parser.add_argument("--artifacts", type=Path)
    parser.add_argument("--verify", type=Path)
    parser.add_argument("--check", choices=["all", *GROUPS], default="all")
    parser.add_argument("--negative-controls", action="store_true")
    args = parser.parse_args()
    if args.verify:
        verify(args.verify, args.check)
        if args.negative_controls:
            negatives(args.verify)
        print("PASS transfer receipt " + args.check)
        return
    require(args.binary and args.artifacts, "--binary and --artifacts required")
    args.binary, args.artifacts = args.binary.resolve(), args.artifacts.resolve()
    args.artifacts.mkdir(parents=True, exist_ok=True)
    record = {"schema": 1, "kind": "sumi-transfer-real", "sources": {name: digest(ROOT / name) for name in SOURCES}, "binary": {"path": str(args.binary), "sha256": digest(args.binary)}, "groups": {name: {"status": "not-run", "checks": [], "error": "", "log": "", "sha256": ""} for name in GROUPS}}
    failed = False
    for name in GROUPS if args.check == "all" else [args.check]:
        item = record["groups"][name]
        log = args.artifacts / (name + ".jsonl")
        item["log"] = str(log)
        with log.open("w") as output, tempfile.TemporaryDirectory(prefix="sumi-transfer-") as temporary:
            run = None
            def passed(check):
                require(check == GROUPS[name][len(item["checks"])], "Unexpected check order")
                item["checks"].append(check); output.write(json.dumps({"check": check, "passed": True}) + "\n"); output.flush(); print("PASS " + check, flush=True)
            try:
                run = Run(args.binary, Path(temporary), args.artifacts, name)
                if name in {"conflicts", "recovery", "workflow", "inverse-window", "copy-links"}:
                    globals()[name.replace("-", "_")](run, passed)
                else:
                    moves(run, passed, name)
                require(item["checks"] == GROUPS[name], "Incomplete checks")
                item["status"] = "pass"
            except Exception:
                failed = True; item["status"] = "fail"; item["error"] = traceback.format_exc(); print(item["error"], flush=True)
            finally:
                if run:
                    run.stop()
            item["sha256"] = digest(log)
    receipt = args.artifacts / "transfer-receipt.json"; receipt.write_text(json.dumps(record, indent=2) + "\n")
    if not failed:
        verify(receipt, args.check)
        if args.negative_controls:
            negatives(receipt)
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    resource.setrlimit(resource.RLIMIT_AS, (8 * 1024**3, 8 * 1024**3))
    main()
