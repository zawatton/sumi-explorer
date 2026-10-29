#!/usr/bin/env python3
"""Real zero-mode Undo, including durable failure controls; Linux/POSIX GUI."""
import argparse
import configparser
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import resource
import stat
import subprocess
import tempfile


def run(args):
    resource.setrlimit(resource.RLIMIT_AS, (8 * 1024**3, 8 * 1024**3))
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("gui", root / "tests/explorer-gui-test.py")
    gui = importlib.util.module_from_spec(spec); spec.loader.exec_module(gui)
    out = args.artifacts.resolve(); out.mkdir(parents=True, exist_ok=True)
    source = out / "zero-mode-fault.c"
    source.write_text(r'''#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
static int armed(const char *kind) {
  const char *arm=getenv("SUMI_ZERO_ARM"), *fault=getenv("SUMI_ZERO_FAULT");
  return arm && fault && !strcmp(fault,kind) && access(arm,F_OK)==0;
}
static void observed(void) {
  const char *path=getenv("SUMI_ZERO_OBSERVED"); FILE *out=path?fopen(path,"a"):NULL;
  if(out) { fputs("injected\n",out); fclose(out); }
}
int fsync(int fd) {
  const char *target=getenv("SUMI_ZERO_TARGET"); char name[64],path[16384];
  snprintf(name,sizeof(name),"/proc/self/fd/%d",fd); ssize_t n=readlink(name,path,sizeof(path)-1);
  if(n>=0) { path[n]=0; if(target && !strcmp(path,target) && armed("fsync")) { observed(); errno=EIO; return -1; } }
  int (*real_sync)(int)=dlsym(RTLD_NEXT,"fsync"); return real_sync(fd);
}
int openat(int dir, const char *name, int flags, ...) {
  mode_t mode=0; if(flags&O_CREAT) { va_list ap; va_start(ap,flags); mode=va_arg(ap,int); va_end(ap); }
  struct stat st;
  if(armed("open") && !strcmp(name,"zero") && (flags&O_ACCMODE)==O_RDONLY && !(flags&O_PATH) &&
     fstatat(dir,name,&st,AT_SYMLINK_NOFOLLOW)==0 && (st.st_mode&S_IRUSR)) { observed(); errno=EIO; return -1; }
  int (*real_open)(int,const char *,int,...)=dlsym(RTLD_NEXT,"openat"); return real_open(dir,name,flags,mode);
}
''')
    preload = out / "zero-mode-fault.so"
    subprocess.run(["cc", "-Wall", "-Wextra", "-Werror", "-shared", "-fPIC", str(source), "-o", str(preload), "-ldl"], check=True)
    records = []
    keys = ("SUMI_EXPLORER_UNDO_DIR", "LD_PRELOAD", "SUMI_ZERO_ARM", "SUMI_ZERO_FAULT", "SUMI_ZERO_TARGET", "SUMI_ZERO_OBSERVED")
    previous = {key: os.environ.get(key) for key in keys}
    try:
        with tempfile.TemporaryDirectory(prefix="sumi-zero-mode-") as temporary:
            base = Path(temporary)
            cases = [(directory, reverse, fault) for directory in (False, True) for reverse in (False, True) for fault in ("none", "fsync")]
            cases += [(directory, True, "open") for directory in (False, True)]
            for index, (directory, reverse, fault) in enumerate(cases):
                case = base / str(index); case.mkdir(); folder = case / "root"; folder.mkdir()
                path = folder / "zero"
                if directory: path.mkdir()
                else: path.write_bytes(b"zero mode bytes")
                old = 0o700 if reverse else 0; forward_mode = 0 if reverse else 0o700
                path.chmod(old); profile = case / "profile"; arm = case / "arm"; observed = case / "observed"
                os.environ.update(SUMI_EXPLORER_UNDO_DIR=str(profile), LD_PRELOAD=str(preload), SUMI_ZERO_ARM=str(arm),
                                  SUMI_ZERO_FAULT=fault, SUMI_ZERO_TARGET=str(path), SUMI_ZERO_OBSERVED=str(observed))
                session_path = case / "session"; session_path.mkdir(); session = gui.Session(args.binary.resolve(), folder, session_path)
                try:
                    session.serial = 1
                    (session_path / "request.ini").write_text(f"[Explorer]\nversion=1\nrequest=1\ncommand=chmod\npath={gui.escape(str(path))}\ndestination={forward_mode:04o}\n")
                    forward = session.wait(lambda state: state["request"] == "1", response=True)
                    assert forward["status"] == "ok" and stat.S_IMODE(path.stat().st_mode) == forward_mode
                    history = configparser.ConfigParser(); history.read(session_path / "undo.ini")
                    arm.touch(); inverse = session.command("undo", history["Undo0"]["id"])
                    history = configparser.ConfigParser(); history.read(session_path / "undo.ini")
                    journal = next(profile.glob("*.journal")); raw = journal.read_bytes()
                    size = int.from_bytes(raw[16:24], "little"); intent = raw[24:24 + max(0, size - 1)].decode()
                    actual = stat.S_IMODE(path.stat().st_mode)
                    if fault == "none":
                        assert inverse["status"] == "ok" and actual == old and not intent
                    else:
                        assert observed.exists(), "Failure injection did not execute"
                        assert inverse["status"] == "error" and intent and int(history["Undo"]["count"]) == 0
                        assert actual == (0o400 if fault == "open" else old)
                    records.append({"directory": directory, "old_mode": oct(old), "forward_mode": oct(forward_mode), "fault": fault,
                                    "inverse": inverse, "actual_mode": oct(actual), "pending_intent": bool(intent),
                                    "journal_sha256": hashlib.sha256(raw).hexdigest()})
                    arm.unlink(); session.stop()
                    if fault != "none":
                        restart = case / "restart"; restart.mkdir(); recovered = gui.Session(args.binary.resolve(), folder, restart)
                        try:
                            recovered.wait(lambda state: "Interrupted operation" in state["message"])
                            assert journal.read_bytes() == raw, "Ambiguous intent was overwritten after restart"
                        finally: recovered.stop()
                    print(f"PASS zero-mode directory={directory} old={old:04o} fault={fault}", flush=True)
                finally:
                    arm.unlink(missing_ok=True); path.chmod(0o700 if directory else 0o600)
                    if not session.log.closed: session.stop()
                    (out / f"case-{index}.log").write_bytes((session_path / "host.log").read_bytes())
    finally:
        for key, value in previous.items():
            if value is None: os.environ.pop(key, None)
            else: os.environ[key] = value
    receipt = {"binary_sha256": hashlib.sha256(args.binary.read_bytes()).hexdigest(), "checks": records,
               "sources": {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in
                           ("explorer/sumi-explorer-native.el", "explorer/platform.c", "explorer/platform.h", "tests/explorer-zero-mode-test.py")},
               "fault_source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "fault_binary_sha256": hashlib.sha256(preload.read_bytes()).hexdigest()}
    (out / "zero-mode.json").write_text(json.dumps(receipt, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    run(parser.parse_args())
