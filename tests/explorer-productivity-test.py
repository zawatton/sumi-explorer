#!/usr/bin/env python3
"""Real combined productivity workflow and concurrent-owner close regressions.

Run under heavy-run.sh, an 8-GiB cap, Xvfb and dbus-run-session. The close
regressions interpose the actual GTask return boundary in the supplied binary;
they never replace the application's search/transfer workers or callbacks.
"""
from __future__ import annotations

import argparse
import configparser
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
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tests" / filename)
    result = importlib.util.module_from_spec(spec); spec.loader.exec_module(result)
    return result


transfer = module("productivity_transfer", "explorer-transfer-test.py")
everyday = module("productivity_everyday", "explorer-everyday-test.py")
gui = transfer.gui
require, wait, digest = transfer.require, transfer.wait, transfer.digest
CLOSE_CHECKS = ["both-workers-held", "close-observed", "first-completion-retains-context", "late-command-refused", "sender-refused-move", "both-complete-cleanly"]
GROUPS = {"close-search-first": CLOSE_CHECKS, "close-transfer-first": CLOSE_CHECKS,
          "workflow": ["search-nested-unicode", "explicit-replace", "explicit-merge", "forced-zip64-extract", "external-move", "restart-durable-undo", "final-screenshot"]}


def sources():
    files = [p for p in (ROOT / "explorer").iterdir() if p.suffix in {".el", ".c", ".h", ".inc"}]
    files += [Path(__file__).resolve(), ROOT / "tests/explorer-transfer-test.py", ROOT / "tests/explorer-gui-test.py", ROOT / "tests/explorer-everyday-test.py", ROOT / "docs/productivity-expansion.md"]
    return {str(p.relative_to(ROOT)): digest(p) for p in sorted(files)}


def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "Duplicate JSON field")
        result[key] = value
    return result


def verify(path, selected="all"):
    path = Path(path); require(path.stat().st_size <= 1048576, "Oversized receipt")
    r = json.loads(path.read_text(), object_pairs_hook=unique)
    require(set(r) == {"schema", "kind", "sources", "binary", "groups", "artifacts"}, "Receipt fields")
    require(type(r["schema"]) is int and r["schema"] == 1 and r["kind"] == "sumi-productivity-desktop", "Receipt schema")
    require(r["sources"] == sources(), "Source hashes differ")
    require(set(r["binary"]) == {"path", "sha256"} and digest(r["binary"]["path"]) == r["binary"]["sha256"], "Binary differs")
    require(set(r["groups"]) == set(GROUPS), "Receipt groups")
    for name, expected in GROUPS.items():
        group = r["groups"][name]
        require(set(group) == {"status", "checks", "error", "log", "sha256"}, "Group fields")
        require(group["status"] in {"pass", "fail", "not-run"} and type(group["error"]) is str, "Group status")
        require(type(group["checks"]) is list and group["checks"] == expected[:len(group["checks"])], "Group checks")
        if group["status"] == "not-run":
            require(group["checks"] == [] and group["error"] == group["log"] == group["sha256"] == "", "Unrun evidence")
        else:
            require(digest(group["log"]) == group["sha256"], "Event log differs")
            events = [json.loads(line, object_pairs_hook=unique) for line in Path(group["log"]).read_text().splitlines()]
            require(events == [{"check": check, "passed": True} for check in group["checks"]], "Execution events differ")
            if group["status"] == "pass": require(group["checks"] == expected and not group["error"], "Incomplete pass")
            else: require(bool(group["error"]), "Failure lacks diagnosis")
        if selected in {"all", name}: require(group["status"] == "pass", "Group did not pass: " + name)
    require(type(r["artifacts"]) is list and r["artifacts"], "Missing runtime artifacts")
    seen = set()
    for item in r["artifacts"]:
        require(set(item) == {"path", "sha256", "kind"} and item["kind"] in {"log", "sender", "helper-source", "helper-binary", "snapshot", "screenshot", "fixture"}, "Artifact fields")
        require(item["path"] not in seen and digest(item["path"]) == item["sha256"], "Artifact missing/different/duplicate")
        seen.add(item["path"])
    if r["groups"]["workflow"]["status"] == "pass":
        screenshots = [x for x in r["artifacts"] if x["kind"] == "screenshot"]
        require(screenshots and all(Path(x["path"]).read_bytes().startswith(b"\x89PNG\r\n\x1a\n") for x in screenshots), "Missing valid screenshot")
    print("PASS productivity evidence: " + selected)
    return r


def negatives(path):
    original = json.loads(Path(path).read_text())
    changes = [lambda r: r.update(extra=True), lambda r: r["groups"].pop("workflow"),
               lambda r: r["groups"]["close-transfer-first"].update(checks=[]),
               lambda r: r["groups"]["close-transfer-first"].update(status="skip"),
               lambda r: r["sources"].update({"explorer/sumi-explorer-native.el": "0"*64}),
               lambda r: r["binary"].update(sha256="0"*64),
               lambda r: r["artifacts"][0].update(sha256="0"*64),
               lambda r: r["artifacts"].append(r["artifacts"][0])]
    with tempfile.TemporaryDirectory(prefix="sumi-productivity-verifier-") as tmp:
        bad = Path(tmp) / "bad.json"
        cases = []
        for change in changes:
            r = copy.deepcopy(original); change(r); cases.append(json.dumps(r))
        cases.append(Path(path).read_text().replace('"schema": 1', '"schema": 1, "schema": 1', 1))
        for index, value in enumerate(cases):
            bad.write_text(value)
            try: verify(bad)
            except (AssertionError, ValueError, KeyError, TypeError, OSError): continue
            raise AssertionError("Accepted corrupt receipt " + str(index))
    print("PASS 9 productivity receipt negative controls")


BARRIER = r'''
#define _GNU_SOURCE
#include <gio/gio.h>
#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <unistd.h>
#include <fcntl.h>
#include <stdlib.h>
static void mark(const char *dir,const char *tag,const char *phase) {
 char *path=g_strdup_printf("%s/%s-%s",dir,tag,phase);
 int fd=open(path,O_WRONLY|O_CREAT|O_TRUNC,0600); if(fd>=0){write(fd,"1\n",2);close(fd);}g_free(path);
 char *log=g_build_filename(dir,"barrier.log",NULL);fd=open(log,O_WRONLY|O_CREAT|O_APPEND,0600);
 if(fd>=0){dprintf(fd,"%s %s\n",tag,phase);close(fd);}g_free(log);
}
void g_task_return_boolean(GTask *task,gboolean value) {
 void (*real_return)(GTask*,gboolean)=dlsym(RTLD_NEXT,"g_task_return_boolean");
 const char *dir=getenv("SUMI_PRODUCTIVITY_BARRIERS"),*tag=NULL;
 uintptr_t *job=g_task_get_task_data(task);
 if(dir && job && !g_task_get_source_object(task) && !g_task_get_check_cancellable(task)) {
  if(job[0]==13)tag="transfer";
  else if(job[0]>4096 && job[4]==(uintptr_t)g_task_get_cancellable(task))tag="search";
 }
 if(tag) {
  mark(dir,tag,"ready"); char *release=g_strdup_printf("%s/%s-release",dir,tag);int noted=0,ticks=0;
  while(access(release,F_OK)!=0) {
   if(!noted && g_cancellable_is_cancelled(g_task_get_cancellable(task))){mark(dir,tag,"cancelled");noted=1;}
   if(++ticks>6000)_exit(98);usleep(10000);
  }
  g_free(release);mark(dir,tag,"released");
 }
 real_return(task,value);
}
'''


def build_barrier(artifacts):
    source, binary = artifacts / "task-return-barrier.c", artifacts / "task-return-barrier.so"
    source.write_text(BARRIER)
    flags = shlex.split(subprocess.check_output(["pkg-config", "--cflags", "--libs", "gio-2.0"], text=True))
    with (artifacts / "task-return-barrier-build.log").open("wb") as log:
        subprocess.run([os.environ.get("CC", "cc"), "-std=c11", "-Wall", "-Wextra", "-shared", "-fPIC", str(source), "-o", str(binary), *flags, "-ldl"], check=True, timeout=60, stdout=log, stderr=subprocess.STDOUT)
    return binary


def search_snapshot(run):
    path = run.s.directory / "search.ini"
    if not path.exists(): return {}
    parser = configparser.RawConfigParser(strict=True); parser.read(path)
    return dict(parser["Search"])


def queue(run, command, path):
    run.s.serial += 1
    data = f"[Explorer]\nversion=1\nrequest={run.s.serial}\ncommand={command}\npath={gui.escape(str(path))}\n"
    temp = run.s.directory / "request.tmp"; temp.write_text(data); temp.replace(run.s.directory / "request.ini")


def concurrent_close(binary, base, artifacts, passed, first):
    barriers = artifacts / "barriers"; barriers.mkdir()
    preload = build_barrier(artifacts)
    saved = {key: os.environ.get(key) for key in ["LD_PRELOAD", "SUMI_PRODUCTIVITY_BARRIERS"]}
    os.environ["LD_PRELOAD"] = str(preload); os.environ["SUMI_PRODUCTIVITY_BARRIERS"] = str(barriers)
    try: run = transfer.Run(binary, base, artifacts, "close-" + first)
    finally:
        for key, value in saved.items():
            if value is None: os.environ.pop(key, None)
            else: os.environ[key] = value
    try:
        nested = run.root / "nested 日本語"; nested.mkdir(); (nested / "search-result.txt").write_text("fixture")
        require(run.command("search", "search-result")["status"] == "ok", "Search launch")
        wait(lambda: (barriers / "search-ready").exists(), "real search worker blocked at return")
        outside = base / "sender"; outside.mkdir(); source = outside / "move 日本語.txt"; source.write_bytes(b"sender source survives close\n")
        dest = run.root / source.name

        def during(window):
            wait(lambda: (barriers / "transfer-ready").exists(), "real transfer worker blocked at return")
            require(dest.read_bytes() == source.read_bytes(), "Transfer did not actually execute before its return barrier")
            passed("both-workers-held")
            everyday.normal_close(window)
            wait(lambda: (barriers / "search-cancelled").exists(), "close reaches the pending search owner")
            passed("close-observed")
            (barriers / (first + "-release")).touch()
            if first == "search":
                wait(lambda: search_snapshot(run).get("status") != "running", "search callback finishes first")
            else:
                wait(lambda: bool(list(artifacts.glob("sender-result-*.json"))), "transfer callback finishes first")
            require(run.s.process.poll() is None, "Context destroyed before the second worker callback")
            passed("first-completion-retains-context")
            late = run.root / "must-not-start-after-close"
            queue(run, "mkdir", late); time.sleep(.2)
            require(not late.exists() and (run.s.directory / "request.ini").exists(), "Command started after close intent")
            passed("late-command-refused")
            (barriers / (("transfer" if first == "search" else "search") + "-release")).touch()

        result = transfer.drag(run, [source], during=during)
        require(result == {"action": 0, "delete_data": False, "deleted": 0} and source.read_bytes() == b"sender source survives close\n", "Close acknowledged MOVE: " + repr(result))
        passed("sender-refused-move")
        run.s.process.wait(timeout=15)
        require(run.s.process.returncode == 0, "Native process crashed or failed teardown")
        require(search_snapshot(run).get("status") != "running", "Search callback did not finish")
        require(all((barriers / (tag + "-released")).exists() for tag in ["search", "transfer"]), "Worker boundary not released")
        passed("both-complete-cleanly")
        (artifacts / "final-state.ini").write_bytes((run.s.directory / "state.ini").read_bytes())
        (artifacts / "search.ini").write_bytes((run.s.directory / "search.ini").read_bytes())
    finally:
        for tag in ["search", "transfer"]: (barriers / (tag + "-release")).touch()
        run.stop()
        for index, session in enumerate(run.sessions):
            (artifacts / f"native-{index}.log").write_bytes((session.directory / "host.log").read_bytes())


def workflow(binary, base, artifacts, passed):
    run = transfer.Run(binary, base, artifacts, "workflow")
    try:
        nested = run.root / "nested 日本語"; nested.mkdir()
        target = nested / "Straße 日本語.txt"; target.write_bytes(b"original document\n")
        unrelated = nested / "unrelated.txt"; unrelated.write_bytes(b"keep unrelated\n")
        window = gui.xdo("search", "--sync", "--onlyvisible", "--pid", str(run.s.process.pid), "--name", "^Sumi Explorer$").splitlines()[-1]
        gui.xdo("windowsize", window, "1000", "700"); gui.xdo("windowmove", window, "0", "0"); gui.xdo("windowfocus", window)
        gui.xdo("key", "ctrl+f"); ui = run.s.ui(); ui.click("Search filenames", "text")
        gui.xdo("type", "--clearmodifiers", "--", "STRASSE"); ui.click("Search", "button")
        wait(lambda: search_snapshot(run).get("status") == "complete", "GUI Unicode search completion")
        search_window = gui.xdo("search", "--onlyvisible", "--pid", str(run.s.process.pid), "--name", "^Search subfolders$").splitlines()[-1]
        gui.xdo("windowfocus", search_window)
        node = ui.find(str(target))
        while node.get_parent() is not None and node.get_role_name() not in {"list item", "table row"}:
            node = node.get_parent()
        x, y, w, h = ui.rect(node)
        (artifacts / "search-geometry.json").write_text(json.dumps({"row": [x, y, w, h], "window": search_window, "role": node.get_role_name()}))
        subprocess.run(["import", "-window", "root", str(artifacts / "search-before-open.png")], check=True, timeout=10)
        # GTK 4.18 omits the dialog's 18px CSS inset from row accessibility
        # coordinates (the shared helper documents the same allocation issue).
        gui.xdo("mousemove", str(x+18+min(w//2, 150)), str(y+18+h//2)); gui.xdo("click", "1"); gui.xdo("key", "Return")
        run.s.wait(lambda s: s["directory"] == str(nested) and s["selected"] == str(target))
        passed("search-nested-unicode")
        replacement = run.root / "replacement.txt"; replacement.write_bytes(b"updated document\n")
        run.transfer(replacement, target, "replace")
        require(target.read_bytes() == b"updated document\n", "Explicit replace failed"); passed("explicit-replace")
        merge = run.root / "merge source"; merge.mkdir(); (merge / "added 日本語.txt").write_bytes(b"merged document\n")
        (merge / "unrelated.txt").write_bytes(b"must be skipped\n")
        run.transfer(merge, nested, "merge")
        require((nested / "added 日本語.txt").read_bytes() == b"merged document\n" and unrelated.read_bytes() == b"keep unrelated\n", "Merge changed unrelated bytes")
        passed("explicit-merge")
        archive = artifacts / "forced-zip64.zip"
        with zipfile.ZipFile(archive, "w") as z:
            with z.open("展開 日本語.txt", "w", force_zip64=True) as member: member.write(b"ZIP64 workflow payload\n")
        raw = archive.read_bytes(); require(raw[:4] == b"PK\x03\x04" and raw[18:26] == b"\xff"*8, "Fixture is not forced ZIP64")
        output = run.root / "ZIP64 extracted"
        require(run.command("zip-extract", archive, destination=output)["status"] == "ok", "ZIP64 extraction failed")
        require((output / "展開 日本語.txt").read_bytes() == b"ZIP64 workflow payload\n", "ZIP64 bytes differ")
        passed("forced-zip64-extract")
        run.command("open", run.root)
        outside = base / "sender"; outside.mkdir(); source = outside / "external 日本語.txt"; source.write_bytes(b"moved source bytes\n")
        destination = run.root / source.name
        result = transfer.drag(run, [source])
        require(result == {"action": 2, "delete_data": True, "deleted": 1} and not source.exists() and destination.read_bytes() == b"moved source bytes\n", "Real external MOVE failed: " + repr(result))
        passed("external-move")
        run.restart()
        run.undo(); require(source.read_bytes() == b"moved source bytes\n" and not destination.exists(), "Restart external MOVE Undo failed")
        run.undo(); require(not output.exists(), "Restart ZIP64 Undo failed")
        run.undo(); require(not (nested / "added 日本語.txt").exists() and unrelated.read_bytes() == b"keep unrelated\n", "Restart merge Undo failed")
        run.undo(); require(target.read_bytes() == b"original document\n" and replacement.read_bytes() == b"updated document\n", "Restart replace Undo failed")
        passed("restart-durable-undo")
        run.command("open", nested); run.command("select", target)
        window = gui.xdo("search", "--sync", "--onlyvisible", "--pid", str(run.s.process.pid), "--name", "^Sumi Explorer$").splitlines()[-1]
        gui.xdo("windowmove", window, "0", "0"); gui.xdo("windowfocus", window)
        run.s.ui().find(target.name); time.sleep(.15)
        subprocess.run(["import", "-window", window, str(artifacts / "productivity-desktop.png")], check=True, timeout=10)
        for name in ["state.ini", "undo.ini", "view.ini", "workspace.ini"]:
            (artifacts / name).write_bytes((run.s.directory / name).read_bytes())
        (artifacts / "workflow-result.json").write_text(json.dumps({"target": target.name, "restored_sha256": digest(target), "unrelated_sha256": digest(unrelated), "sender_sha256": digest(source), "restart_sessions": len(run.sessions)}, ensure_ascii=False, indent=2))
        passed("final-screenshot")
    finally:
        run.stop()
        for index, session in enumerate(run.sessions):
            (artifacts / f"native-{index}.log").write_bytes((session.directory / "host.log").read_bytes())


def artifact_kind(path):
    if path.suffix == ".png": return "screenshot"
    if path.suffix == ".c": return "helper-source"
    if path.suffix == ".so" or path.name == "external-sender": return "helper-binary"
    if path.suffix == ".ini": return "snapshot"
    if path.name.startswith("sender-result") and path.suffix == ".json": return "sender"
    if path.suffix == ".zip": return "fixture"
    return "log"


def execute(args):
    artifacts = args.artifacts.resolve(); artifacts.mkdir(parents=True, exist_ok=True)
    binary = args.binary.resolve()
    record = {"schema": 1, "kind": "sumi-productivity-desktop", "sources": sources(), "binary": {"path": str(binary), "sha256": digest(binary)},
              "groups": {name: {"status": "not-run", "checks": [], "error": "", "log": "", "sha256": ""} for name in GROUPS}, "artifacts": []}
    failure = None
    try:
        for name in GROUPS:
            if args.check not in {"all", name}: continue
            directory = artifacts / name; directory.mkdir(exist_ok=True)
            group = record["groups"][name]; log = directory / "events.jsonl"
            try:
                with tempfile.TemporaryDirectory(prefix="sumi-productivity-") as temp, log.open("w") as stream:
                    def passed(check):
                        require(check == GROUPS[name][len(group["checks"])], "Unexpected check ordering")
                        group["checks"].append(check); stream.write(json.dumps({"check": check, "passed": True}) + "\n"); stream.flush()
                    if name == "workflow": workflow(binary, Path(temp), directory, passed)
                    else: concurrent_close(binary, Path(temp), directory, passed, "search" if name == "close-search-first" else "transfer")
                group["status"] = "pass"
            except Exception:
                group.update(status="fail", error=traceback.format_exc()); failure = group["error"]
            finally:
                group.update(log=str(log), sha256=digest(log))
            if failure: break
    finally:
        record["artifacts"] = [{"path": str(p), "sha256": digest(p), "kind": artifact_kind(p)} for p in sorted(artifacts.rglob("*")) if p.is_file() and p.name != "record.json"]
        (artifacts / "record.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    if failure: raise AssertionError(failure)
    verify(artifacts / "record.json", args.check)
    if args.negative_controls: negatives(artifacts / "record.json")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path); parser.add_argument("--artifacts", type=Path)
    parser.add_argument("--verify", type=Path); parser.add_argument("--check", choices=["all", *GROUPS], default="all")
    parser.add_argument("--negative-controls", action="store_true")
    args = parser.parse_args()
    resource.setrlimit(resource.RLIMIT_AS, (8*1024**3, 8*1024**3))
    if args.verify:
        verify(args.verify, args.check)
        if args.negative_controls: negatives(args.verify)
    else:
        require(args.binary and args.artifacts, "--binary and --artifacts required"); execute(args)


if __name__ == "__main__": main()
