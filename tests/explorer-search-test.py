#!/usr/bin/env python3
"""Bounded recursive search: real GTK, filesystem and Emacs acceptance.

Run under xvfb-run -a -s '-screen 0 1920x1080x24' dbus-run-session.
Receipts retain exact sources, binary and runtime logs. Verification rechecks
retained evidence; it does not re-execute the native application.
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
import subprocess
import tempfile
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
GROUPS = {
    "behavior": ["recursive-unicode-literal", "full-paths", "filter-stays-local", "repeated-frozen-root", "result-containing-folder"],
    "safety": ["empty-and-oversized-refused", "invalid-root-refused", "symlink-loop-not-followed", "permissions-visible", "results-bounded", "depth-bounded", "visited-bounded", "busy-refused", "cancel-completes", "close-waits"],
    "workflow": ["gui-distinct-action", "gui-shortcut", "gui-unicode-search", "gui-exact-result", "gui-close-cancels", "gui-repeat", "emacs-search-open", "emacs-begin-cancel", "emacs-strict-decoder"],
}


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sources():
    paths = [p for p in (ROOT / "explorer").iterdir() if p.suffix in {".el", ".c", ".h", ".inc"}]
    paths += [Path(__file__).resolve(), ROOT / "tests/explorer-gui-test.py", ROOT / "docs/productivity-expansion.md"]
    return {str(p.relative_to(ROOT)): digest(p) for p in sorted(paths)}


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def verify(path, check="all"):
    path = Path(path)
    require(path.stat().st_size <= 1048576, "Oversized receipt")
    record = json.loads(path.read_text(), object_pairs_hook=unique)
    require(set(record) == {"schema", "kind", "sources", "binary", "groups", "artifacts"}, "Receipt fields")
    require(type(record["schema"]) is int and record["schema"] == 1 and record["kind"] == "sumi-search-real", "Receipt schema")
    require(record["sources"] == sources(), "Source hashes differ")
    require(set(record["binary"]) == {"path", "sha256"} and digest(record["binary"]["path"]) == record["binary"]["sha256"], "Binary hash differs")
    require(set(record["groups"]) == set(GROUPS), "Receipt groups")
    for name, expected in GROUPS.items():
        group = record["groups"][name]
        require(set(group) == {"status", "checks", "error"}, "Group fields")
        require(group["status"] in {"pass", "fail", "not-run"} and type(group["error"]) is str, "Group status")
        require(type(group["checks"]) is list and len(set(group["checks"])) == len(group["checks"])
                and all(item in expected for item in group["checks"]), "Group check IDs")
        if group["status"] == "pass":
            require(group["checks"] == expected and group["error"] == "", "Incomplete passing group")
        if check in {"all", name}:
            require(group["status"] == "pass", f"Group did not pass: {name}")
    artifacts = record["artifacts"]
    require(type(artifacts) is list and len(artifacts) >= 2, "Missing runtime evidence")
    names = set()
    for artifact in artifacts:
        require(set(artifact) == {"path", "sha256", "kind"} and artifact["kind"] in {"native-log", "event-log", "emacs-log", "screenshot"}, "Artifact fields")
        require(artifact["path"] not in names and digest(artifact["path"]) == artifact["sha256"], "Artifact hash/duplicate")
        names.add(artifact["path"])
    events = [a for a in artifacts if a["kind"] == "event-log"]
    require(len(events) == 1, "Missing execution event log")
    lines = Path(events[0]["path"]).read_text().splitlines()
    require(lines and lines[0] == "BINARY " + record["binary"]["sha256"], "Event binary binding")
    for name, group in record["groups"].items():
        for item in group["checks"]:
            require(lines.count(f"PASS {name}/{item}") == 1, "Missing/duplicate runtime event")
    if record["groups"]["workflow"]["status"] == "pass":
        kinds = {a["kind"] for a in artifacts}
        require({"emacs-log", "screenshot", "native-log"}.issubset(kinds), "Missing GUI/Emacs evidence")
        for artifact in artifacts:
            if artifact["kind"] == "emacs-log":
                require("SEARCH-EMACS-PASS" in Path(artifact["path"]).read_text(), "Emacs did not complete")
            if artifact["kind"] == "screenshot":
                require(Path(artifact["path"]).read_bytes().startswith(b"\x89PNG\r\n\x1a\n"), "Invalid screenshot")
    print(f"PASS search retained evidence: {check}")
    return record


def negative_controls(path):
    original = json.loads(Path(path).read_text())
    changes = [lambda r: r.update(extra=True), lambda r: r["groups"].pop("safety"),
               lambda r: r["groups"]["behavior"].update(checks=[]),
               lambda r: r["groups"]["behavior"].update(status="skip"),
               lambda r: r["sources"].update({"explorer/sumi-explorer-native.el": "0" * 64}),
               lambda r: r["binary"].update(sha256="0" * 64),
               lambda r: r["artifacts"][0].update(sha256="0" * 64),
               lambda r: r["artifacts"].append(r["artifacts"][0])]
    with tempfile.TemporaryDirectory(prefix="sumi-search-verifier-") as tmp:
        bad = Path(tmp) / "bad.json"
        values = []
        for change in changes:
            value = copy.deepcopy(original); change(value); values.append(json.dumps(value))
        values.append(Path(path).read_text().replace('"schema": 1', '"schema": 1, "schema": 1', 1))
        for index, value in enumerate(values):
            bad.write_text(value)
            try:
                verify(bad)
            except (AssertionError, ValueError, KeyError, TypeError, OSError):
                continue
            raise AssertionError(f"Corrupt receipt {index} accepted")
    print(f"PASS {len(values)} search receipt negative controls")


def wait(predicate, message, seconds=30):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.015)
    raise AssertionError(message)


def load_gui():
    spec = importlib.util.spec_from_file_location("search_gui", ROOT / "tests/explorer-gui-test.py")
    gui = importlib.util.module_from_spec(spec); spec.loader.exec_module(gui)
    return gui


class Run:
    def __init__(self, binary, artifacts):
        self.binary, self.artifacts = binary.resolve(), artifacts.resolve()
        self.record = {"schema": 1, "kind": "sumi-search-real", "sources": sources(),
                       "binary": {"path": str(self.binary), "sha256": digest(self.binary)},
                       "groups": {name: {"status": "not-run", "checks": [], "error": ""} for name in GROUPS}, "artifacts": []}
        self.events = (self.artifacts / "events.log").open("w")
        self.events.write("BINARY " + digest(self.binary) + "\n"); self.events.flush()
        self.gui = load_gui()

    def check(self, group, name, condition=True):
        require(condition, f"{group}/{name}")
        self.record["groups"][group]["checks"].append(name)
        self.events.write(f"PASS {group}/{name}\n"); self.events.flush()

    def snapshot(self):
        path = self.session.directory / "search.ini"
        if not path.exists():
            return {}
        raw = path.read_bytes(); require(len(raw) <= 1048576, "Snapshot exceeds 1 MiB")
        parser = configparser.RawConfigParser(strict=True, empty_lines_in_values=False)
        parser.read_string(raw.decode("utf-8"))
        header = {k: self.gui.unescape(v) for k, v in parser["Search"].items()}
        paths = [self.gui.unescape(parser[f"Search{i//250}"][f"path{i%250}"]) for i in range(int(header["count"]))]
        header["results"] = paths
        return header

    def search(self, query):
        response = self.session.command("search", query)
        require(response["status"] == "ok", f"Search launch: {response}")
        serial = str(self.session.serial)
        return wait(lambda: (s := self.snapshot()) and s["request"] == serial and s["status"] != "running" and s, "Search completion")

    def behavior(self, root, target):
        s = self.search("STRASSE")
        require(self.search("[?*")["results"] == [str(root / "literal[?*.txt")], "Query was interpreted as a glob/regexp")
        self.check("behavior", "recursive-unicode-literal", str(target) in s["results"] and s["status"] == "complete")
        self.check("behavior", "full-paths", all(Path(p).is_absolute() and Path(p).is_relative_to(root) for p in s["results"]))
        require(self.session.command("filter", "STRASSE")["status"] == "ok", "Current filter")
        self.check("behavior", "filter-stays-local", self.session.command("select", str(target))["status"] == "error")
        require(self.session.command("open", str(target.parent))["status"] == "ok", "Navigate")
        s = self.search("STRASSE")
        self.check("behavior", "repeated-frozen-root", s["root"] == str(target.parent) and s["query"] == "STRASSE" and s["results"] == [str(target)])
        response = self.session.command("search-open", str(target))
        self.check("behavior", "result-containing-folder", response["status"] == "ok" and response["directory"] == str(target.parent) and response["selected"] == str(target))
        renamed = target.with_name("renamed.txt"); target.rename(renamed)
        try:
            require(self.session.command("search-open", str(target))["status"] == "error", "Missing result selected a different file")
        finally:
            renamed.rename(target)
        require(self.session.command("search-open", str(root / "not-a-result"))["status"] == "error", "Arbitrary result refused")
        self.session.command("open", str(root))

    def safety(self, root, base, target):
        require(self.session.command("search", "")["status"] == "error", "Empty query accepted")
        self.check("safety", "empty-and-oversized-refused", self.session.command("search", "界" * 257)["status"] == "error")
        alias = base / "root-alias"; alias.symlink_to(root, target_is_directory=True)
        require(self.session.command("open", str(alias))["status"] == "ok", "Alias navigation")
        require(self.session.command("search", "STRASSE")["status"] == "error", "Symlink root accepted")
        gone = base / "gone"; gone.mkdir(); self.session.command("open", str(gone)); gone.rmdir(); gone.touch()
        self.check("safety", "invalid-root-refused", self.session.command("search", "STRASSE")["status"] == "error")
        self.session.command("open", str(root))
        outside = base / "outside"; outside.mkdir(); (outside / "outside-needle").touch()
        (root / "loop").symlink_to(root, target_is_directory=True)
        (root / "escape").symlink_to(outside, target_is_directory=True)
        s = self.search("outside-needle")
        self.check("safety", "symlink-loop-not-followed", not s["results"] and int(s["visited"]) < 30)
        denied = root / "denied"; denied.mkdir(); (denied / "secret-needle").touch(); denied.chmod(0)
        try:
            s = self.search("secret-needle")
            self.check("safety", "permissions-visible", int(s["warnings"]) > 0 and not s["results"])
        finally:
            denied.chmod(0o700)
        bulk = root / "bulk"; bulk.mkdir()
        for i in range(2300): (bulk / f"limit-needle-{i}").touch()
        s = self.search("limit-needle")
        self.check("safety", "results-bounded", len(s["results"]) == 2000 and s["truncated"] == "1")
        deep = root / "deep"; deep.mkdir()
        for i in range(66): deep /= "d"; deep.mkdir()
        (deep / "deep-needle").touch()
        s = self.search("deep-needle")
        self.check("safety", "depth-bounded", not s["results"] and s["truncated"] == "1" and int(s["visited"]) < 2400)
        # Navigate into a separate fixture so the depth-bound fixture cannot
        # short-circuit the visited-count check.
        big = base / "big"; big.mkdir()
        bucket = big / "entries"; bucket.mkdir()
        for i in range(100010): (bucket / f"entry-{i:06}").touch()
        self.big = big
        self.session.command("open", str(big))
        s = self.search("never-match")
        self.check("safety", "visited-bounded", s["truncated"] == "1" and s["visited"] == "100000")
        require(self.session.command("search", "never-match")["status"] == "ok", "Start cancellation search")
        require(self.snapshot()["status"] == "running", "Fixture did not keep worker busy")
        self.check("safety", "busy-refused", self.session.command("search", "obsolete")["status"] == "error")
        require(self.session.command("search-cancel")["status"] == "ok", "Cancel acknowledgement")
        s = wait(lambda: (s := self.snapshot()) and s["status"] != "running" and s, "Cancel completion")
        self.check("safety", "cancel-completes", s["status"] == "cancelled" and s["query"] == "never-match")
        require(self.search("entry-000001")["results"] == [str(bucket / "entry-000001")], "Repeat after cancellation")
        require(self.session.command("search", "never-match")["status"] == "ok", "Close search launch")
        require(self.snapshot()["status"] == "running", "Close fixture worker not busy")
        self.session.command("quit")
        self.session.process.wait(timeout=15)
        self.check("safety", "close-waits", self.session.process.returncode == 0)

    def workflow(self, root, base, target):
        self.session.stop()
        session_dir = base / "gui-session"; session_dir.mkdir(mode=0o700)
        self.session = self.gui.Session(self.binary, root, session_dir)
        ui = self.session.ui()
        window = self.gui.xdo("search", "--sync", "--name", "^Sumi Explorer$").splitlines()[-1]
        self.gui.xdo("windowfocus", window)
        ui.click("More"); ui.find("Search subfolders", "button")
        self.check("workflow", "gui-distinct-action")
        self.gui.xdo("key", "Escape"); self.gui.xdo("key", "ctrl+f")
        ui.find("Search filenames", "text")
        self.check("workflow", "gui-shortcut")
        ui.click("Search filenames", "text"); self.gui.xdo("type", "--clearmodifiers", "--", "STRASSE")
        ui.click("Search", "button")
        wait(lambda: (s := self.snapshot()) and s["status"] == "complete" and str(target) in s["results"], "GUI search")
        node = ui.find(str(target)); x, y, w, h = ui.rect(node)
        self.check("workflow", "gui-unicode-search", w > 0 and h > 0)
        screenshot = self.artifacts / "search-results.png"
        subprocess.run(["import", "-window", "root", str(screenshot)], check=True, timeout=10)
        self.gui.xdo("mousemove", str(x + min(w//2, 150)), str(y+h//2)); self.gui.xdo("click", "--repeat", "2", "--delay", "100", "1")
        s = self.session.wait(lambda s: s["directory"] == str(target.parent) and s["selected"] == str(target))
        self.check("workflow", "gui-exact-result", s["status"] == "ok")
        # Closing only the results window must cancel its worker while keeping
        # the main context alive. Reopen after completion and run a fresh query.
        big = getattr(self, "big", None)
        if big is None:
            big = base / "gui-big"; big.mkdir(); bucket = big / "entries"; bucket.mkdir()
            for i in range(50000): (bucket / f"entry-{i}").touch()
        self.session.command("open", str(big))
        self.gui.xdo("windowfocus", window); self.gui.xdo("key", "ctrl+f")
        ui.click("Search filenames", "text"); self.gui.xdo("type", "--clearmodifiers", "--", "never-match")
        ui.click("Search", "button")
        wait(lambda: (s := self.snapshot()) and s["status"] == "running", "GUI busy fixture")
        ui.click("Close search", "button")
        wait(lambda: (s := self.snapshot()) and s["status"] != "running", "Dialog close cancellation")
        self.check("workflow", "gui-close-cancels", self.snapshot()["status"] == "cancelled" and self.session.process.poll() is None)
        self.session.command("open", str(root)); self.gui.xdo("windowfocus", window); self.gui.xdo("key", "ctrl+f")
        ui.click("Search filenames", "text"); self.gui.xdo("type", "--clearmodifiers", "--", "STRASSE")
        ui.click("Search", "button")
        wait(lambda: (s := self.snapshot()) and s["status"] == "complete" and str(target) in s["results"], "GUI repeated search")
        self.check("workflow", "gui-repeat", self.snapshot()["query"] == "STRASSE")
        self.session.stop(); self.artifact(session_dir / "host.log", "native-log", "gui-host.log")
        environment = dict(os.environ, SEARCH_BINARY=str(self.binary), SEARCH_ROOT=str(root), SEARCH_TARGET=str(target),
                           SEARCH_BIG=str(getattr(self, "big", root)), SUMI_EXPLORER_UNDO_DIR=str(base / "emacs-undo"))
        code = r'''(progn
 (require 'sumi-explorer)
 (sumi-explorer-start (getenv "SEARCH_ROOT") (getenv "SEARCH_BINARY"))
 (unwind-protect
  (progn
   (let ((s (sumi-explorer-search "STRASSE")))
    (cl-assert (member (getenv "SEARCH_TARGET") (cdr (assoc "results" s)))))
   (sumi-explorer-filter "will-hide-result")
   (sumi-explorer-open-search-result (getenv "SEARCH_TARGET"))
   (cl-assert (equal (cdr (assoc "selected" (sumi-explorer-state))) (getenv "SEARCH_TARGET")))
   (princ "SEARCH-EMACS-OPEN\n")
   (sumi-explorer-open-directory (getenv "SEARCH_BIG"))
   (sumi-explorer-begin-search "never-match") (sumi-explorer-await)
   (sumi-explorer-cancel-search)
   (let ((end (+ (float-time) 20)))
    (while (equal (cdr (assoc "status" (sumi-explorer-search-results))) "running")
     (cl-assert (< (float-time) end)) (accept-process-output sumi-explorer--process 0.02)))
   (princ "SEARCH-EMACS-CANCEL\n")
   (let* ((file (expand-file-name "search.ini" sumi-explorer--session-directory))
          (raw (with-temp-buffer (insert-file-contents file) (buffer-string)))
          (samples (list (concat raw "\n[Search]\nversion=1\n")
                         (replace-regexp-in-string "count=[0-9]+" "count=2001" raw)
                         (concat raw "\n[Search9]\npath0=/outside\n")
                         (replace-regexp-in-string "truncated=[01]" "truncated=2" raw)
                         (replace-regexp-in-string "query=never-match" "query=" raw)
                         (concat raw "bad=\\x\n"))))
    (unwind-protect
     (dolist (sample samples)
      (let ((coding-system-for-write 'utf-8-unix)) (write-region sample nil file nil 'silent))
      (cl-assert (condition-case nil (progn (sumi-explorer-search-results) nil) (error t))))
     (let ((coding-system-for-write 'utf-8-unix)) (write-region raw nil file nil 'silent))))
   (princ "SEARCH-EMACS-DECODER\nSEARCH-EMACS-PASS\n"))
  (sumi-explorer-stop)))'''
        log = self.artifacts / "emacs.log"
        with log.open("w") as stream:
            completed = subprocess.run(["emacs", "-Q", "--batch", "-L", str(ROOT / "explorer"), "--eval", code],
                                       env=environment, stdout=stream, stderr=subprocess.STDOUT, timeout=60)
        require(completed.returncode == 0, f"Emacs workflow failed: {log}")
        text = log.read_text()
        self.check("workflow", "emacs-search-open", "SEARCH-EMACS-OPEN" in text)
        self.check("workflow", "emacs-begin-cancel", "SEARCH-EMACS-CANCEL" in text)
        self.check("workflow", "emacs-strict-decoder", "SEARCH-EMACS-DECODER" in text)
        self.artifact(log, "emacs-log")
        self.artifact(screenshot, "screenshot")

    def artifact(self, path, kind, name=None):
        if name:
            dest = self.artifacts / name; dest.write_bytes(Path(path).read_bytes()); path = dest
        self.record["artifacts"].append({"path": str(Path(path).resolve()), "sha256": digest(path), "kind": kind})

    def run(self, check):
        active = None
        try:
            with tempfile.TemporaryDirectory(prefix="sumi-search-") as tmp:
                base = Path(tmp); root = base / "日本語 root"; root.mkdir()
                nested = root / "nested 日本語"; nested.mkdir()
                target = nested / ".Straße 日本語\nname.txt"; target.write_text("search fixture")
                (root / "literal[?*.txt").touch()
                session_dir = base / "session"; session_dir.mkdir(mode=0o700)
                old_undo = os.environ.get("SUMI_EXPLORER_UNDO_DIR")
                os.environ["SUMI_EXPLORER_UNDO_DIR"] = str(base / "undo")
                self.session = self.gui.Session(self.binary, root, session_dir)
                try:
                    for name in GROUPS:
                        if check not in {"all", name}: continue
                        active = name
                        if name == "behavior": self.behavior(root, target)
                        elif name == "safety": self.safety(root, base, target)
                        else: self.workflow(root, base, target)
                        self.record["groups"][name]["status"] = "pass"
                finally:
                    self.session.stop()
                    self.artifact(session_dir / "host.log", "native-log", "host.log")
                    if old_undo is None: os.environ.pop("SUMI_EXPLORER_UNDO_DIR", None)
                    else: os.environ["SUMI_EXPLORER_UNDO_DIR"] = old_undo
        except Exception:
            if active:
                self.record["groups"][active].update(status="fail", error=traceback.format_exc())
            raise
        finally:
            self.events.close(); self.artifact(self.artifacts / "events.log", "event-log")
            (self.artifacts / "record.json").write_text(json.dumps(self.record, ensure_ascii=False, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path)
    parser.add_argument("--artifacts", type=Path)
    parser.add_argument("--verify", type=Path)
    parser.add_argument("--check", choices=["all", *GROUPS], default="all")
    parser.add_argument("--negative-controls", action="store_true")
    args = parser.parse_args()
    resource.setrlimit(resource.RLIMIT_AS, (8 * 1024**3, 8 * 1024**3))
    if args.verify:
        verify(args.verify, args.check)
        if args.negative_controls: negative_controls(args.verify)
    else:
        require(args.binary and args.artifacts, "--binary and --artifacts required")
        args.artifacts.mkdir(parents=True, exist_ok=True)
        Run(args.binary, args.artifacts).run(args.check)
        verify(args.artifacts / "record.json", args.check)
        if args.negative_controls: negative_controls(args.artifacts / "record.json")


if __name__ == "__main__":
    main()
