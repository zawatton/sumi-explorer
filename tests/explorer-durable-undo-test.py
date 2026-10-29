#!/usr/bin/env python3
"""Fresh-process durable Undo acceptance; run under xvfb-run/dbus-run-session."""
from __future__ import annotations

import argparse
import configparser
import concurrent.futures
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import resource
import shutil
import subprocess
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ("explorer/sumi-explorer-native.el", "explorer/platform.c",
           "explorer/platform.h", "tests/explorer-durable-undo-test.py")
CHECKS = {"persistence": {"restart-copy", "restart-linked-rename", "monotonic-id",
                           "independent-live-windows", "death-releases-lock", "empty-window-history",
                           "commit-close-during-restore", "transient-sequence-contention", "concurrent-id-reservations"},
          "integrity": {"tampered-object-refusal", "tampered-history-refusal",
                        "bounded-decoder-refusal", "private-profile-refusal", "unavailable-persistence-outcome",
                        "ambiguous-intent-retained", "failed-flush-retains-journal",
                        "malformed-projection-refusal", "parent-link-guard-refusal", "all-pending-siblings-reported",
                        "deleted-established-counter-refusal", "zero-time-counter-distinction", "invalid-sequence-state-refusal",
                        "older-higher-id-refusal", "inverse-rename-sync-failure", "inverse-chmod-sync-failure"},
          "workflow": {"emacs-restart-inverse", "gui-restart-inverse",
                       "replacement-restart-inverse", "merge-restart-inverse"}}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def wire_layout(raw):
    """Locate serialized scalar fields for checksummed hostile-input fixtures."""
    pos = 8; links = []; ids = []
    def integer():
        nonlocal pos
        at = pos; pos += 8
        return int.from_bytes(raw[at:pos], "little"), (at, pos)
    def string():
        nonlocal pos
        length, span = integer(); pos += max(0, length - 1)
        return (span[0], pos)
    integer(); string(); count, _ = integer()
    for _ in range(count):
        _, identity = integer(); ids.append(identity); integer(); keys, _ = integer()
        for _ in range(keys): string(); string()
        items, _ = integer()
        for _ in range(items):
            for offset in range(0, 104, 8):
                if offset < 48: string()
                elif offset in (80, 88):
                    present, _ = integer()
                    if present:
                        fields = {}
                        for at in range(0, 96, 8):
                            fields[at] = string() if 24 <= at <= 48 or at == 88 or (offset == 88 and at == 72) else integer()[1]
                        links.append((offset == 88, fields))
                else: integer()
    assert pos == len(raw) - 64
    return ids, links


def checksum(payload):
    return bytes(payload) + hashlib.sha256(payload).hexdigest().encode()


def inject_string(raw, span, value):
    encoded = (0 if value is None else len(value) + 1).to_bytes(8, "little") + (value or b"")
    return checksum(raw[:span[0]] + encoded + raw[span[1]:-64])


def verify(path, selected="all"):
    record = json.loads(path.read_text())
    assert set(record) == {"version", "binary", "sources", "checks", "evidence_root", "evidence"}
    assert record["version"] == 2
    assert set(record["binary"]) == {"path", "sha256"}
    assert digest(record["binary"]["path"]) == record["binary"]["sha256"]
    assert set(record["sources"]) == set(SOURCES)
    for name, sha in record["sources"].items():
        assert digest(ROOT / name) == sha, name
    assert set(record["checks"]) == set(CHECKS)
    for group in CHECKS:
        assert set(record["checks"][group]) == CHECKS[group], group
        assert all(value is True for value in record["checks"][group].values())
    assert record["evidence"] and isinstance(record["evidence"], dict)
    evidence_root = Path(record["evidence_root"])
    assert evidence_root.is_absolute()
    for name, sha in record["evidence"].items():
        assert name == Path(name).name and digest(evidence_root / name) == sha, name
    assert selected == "all" or selected in CHECKS
    print(f"PASS durable Undo receipt {selected}")


def run(args):
    resource.setrlimit(resource.RLIMIT_AS, (8 * 1024**3, 8 * 1024**3))
    spec = importlib.util.spec_from_file_location("durable_gui", ROOT / "tests/explorer-gui-test.py")
    gui = importlib.util.module_from_spec(spec); spec.loader.exec_module(gui)
    artifacts = args.artifacts.resolve(); artifacts.mkdir(parents=True, exist_ok=True)
    checks = {group: {} for group in CHECKS}; evidence = {}

    def passed(group, name):
        checks[group][name] = True
        print(f"PASS {group}/{name}", flush=True)

    saved = os.environ.get("SUMI_EXPLORER_UNDO_DIR")
    sessions = []
    with tempfile.TemporaryDirectory(prefix="sumi-durable-undo-") as temporary:
        base = Path(temporary); root = base / "root"; root.mkdir()
        profile = base / "profile"; os.environ["SUMI_EXPLORER_UNDO_DIR"] = str(profile)

        def start(label):
            directory = base / label; directory.mkdir()
            session = gui.Session(args.binary.resolve(), root, directory)
            sessions.append(session)
            return session

        def stop(session, kill=False):
            if kill:
                session.process.kill(); session.process.wait(timeout=5)
            session.stop()
            target = artifacts / (session.directory.name + ".log")
            shutil.copyfile(session.directory / "host.log", target); evidence[target.name] = digest(target)

        def history(session):
            key = configparser.ConfigParser(interpolation=None)
            key.read(session.directory / "undo.ini")
            count = int(key["Undo"]["count"])
            return [dict(key[f"Undo{i}"]) for i in range(count)]

        def command(session, name, source="", destination=None, **fields):
            session.serial += 1; serial = session.serial
            text = f"[Explorer]\nversion=1\nrequest={serial}\ncommand={name}\npath={gui.escape(str(source))}\n"
            if destination is not None: text += f"destination={gui.escape(str(destination))}\n"
            text += "".join(key + "=" + gui.escape(str(value)) + "\n" for key, value in fields.items())
            request = session.directory / "request.tmp"; request.write_text(text)
            request.replace(session.directory / "request.ini")
            return session.wait(lambda state: state["request"] == str(serial), seconds=30, response=True)

        try:
            source = root / "original 日本語.txt"; source.write_bytes(b"durable source\n")
            output = root / "copy.txt"; renamed = root / "renamed.txt"
            first = start("first")
            assert command(first, "copy", source, output)["status"] == "ok"
            original_id = history(first)[0]["id"]; stop(first)
            second = start("second")
            assert history(second)[0]["id"] == original_id and output.read_bytes() == source.read_bytes()
            passed("persistence", "restart-copy")
            assert command(second, "rename", output, renamed)["status"] == "ok"
            rename_id = history(second)[0]["id"]; assert int(rename_id) > int(original_id)
            passed("persistence", "monotonic-id"); stop(second)
            third = start("third")
            assert [row["id"] for row in history(third)] == [rename_id, original_id]
            assert command(third, "undo", rename_id)["status"] == "ok" and output.exists() and not renamed.exists()
            assert command(third, "undo", original_id)["status"] == "ok" and not output.exists()
            passed("persistence", "restart-linked-rename"); stop(third)

            # Two live windows must hold distinct stable locks and never import
            # each other's live histories. A dead process releases its lock.
            left = start("left"); right = start("right")
            leftout, rightout = root / "left", root / "right"
            assert command(left, "copy", source, leftout)["status"] == "ok"
            assert command(right, "copy", source, rightout)["status"] == "ok"
            leftid, rightid = history(left)[0]["id"], history(right)[0]["id"]
            assert leftid != rightid and len(history(left)) == len(history(right)) == 1
            assert len(list(profile.glob("*.journal"))) == 2
            passed("persistence", "independent-live-windows")
            stop(right, kill=True)
            recovered = start("recovered")
            assert history(recovered)[0]["id"] == rightid
            assert command(recovered, "undo", rightid)["status"] == "ok" and not rightout.exists()
            passed("persistence", "death-releases-lock"); stop(recovered)
            stop(left)
            latest = start("latest")
            assert history(latest)[0]["id"] == leftid
            passed("persistence", "empty-window-history")
            leftout.write_bytes(b"outside edit\n")
            assert command(latest, "undo", leftid)["status"] == "error"
            assert leftout.read_bytes() == b"outside edit\n"
            passed("integrity", "tampered-object-refusal"); stop(latest)

            # Corruption remains on disk and blocks restored history. Decode
            # must reject even a rechecksummed impossible allocation count.
            journals = list(profile.glob("*.journal")); selected = max(journals, key=lambda p: p.stat().st_mtime_ns)
            untouched = {p: p.read_bytes() for p in journals}
            data = bytearray(selected.read_bytes()); data[10] ^= 64; selected.write_bytes(data)
            corrupt = start("corrupt"); assert not history(corrupt)
            corrupt.wait(lambda state: "Undo unavailable" in state["message"])
            assert selected.read_bytes() == data
            passed("integrity", "tampered-history-refusal"); stop(corrupt)
            for p, raw in untouched.items(): p.write_bytes(raw)
            data = bytearray(selected.read_bytes()); data[24:32] = (2**63 - 1).to_bytes(8, "little")
            data[-64:] = hashlib.sha256(data[:-64]).hexdigest().encode(); selected.write_bytes(data)
            bounded = start("bounded"); assert not history(bounded)
            assert selected.read_bytes() == data
            passed("integrity", "bounded-decoder-refusal"); stop(bounded)

            for p, raw in untouched.items(): p.write_bytes(raw)
            recovery = root / ".sumi-recovery-owned"; recovery.mkdir(); retained = recovery / "original"
            retained.write_bytes(b"never delete ambiguous backup")
            intent = f"Interrupted replacement backup={retained}".encode()
            raw = untouched[selected]
            payload = raw[:16] + (len(intent) + 1).to_bytes(8, "little") + intent + raw[24:-64]
            intent_bytes = payload + hashlib.sha256(payload).hexdigest().encode()
            selected.write_bytes(intent_bytes)
            ambiguous = start("ambiguous"); assert not history(ambiguous)
            ambiguous.wait(lambda state: "Interrupted operation" in state["message"] and str(retained) in state["message"])
            extra = root / "ambiguous-copy"
            assert command(ambiguous, "copy", source, extra)["status"] == "ok"
            assert selected.read_bytes() == intent_bytes and retained.read_bytes() == b"never delete ambiguous backup"
            passed("integrity", "ambiguous-intent-retained"); stop(ambiguous)

            for p, raw in untouched.items(): p.write_bytes(raw)
            failure = start("flush-failure")
            disk_before = {p: p.read_bytes() for p in profile.glob("*.journal")}
            profile.chmod(0o500)
            try:
                extra = root / "flush-failure-copy"
                result = command(failure, "copy", source, extra)
                assert result["status"] == "ok" and extra.read_bytes() == source.read_bytes()
                assert "Undo unavailable" in result["message"] and not history(failure)
                assert all(p.read_bytes() == raw for p, raw in disk_before.items())
            finally: profile.chmod(0o700)
            passed("integrity", "failed-flush-retains-journal"); stop(failure)

            insecure = base / "insecure"; insecure.mkdir(mode=0o755)
            os.environ["SUMI_EXPLORER_UNDO_DIR"] = str(insecure)
            unsafe = start("unsafe"); assert not history(unsafe)
            unsafe.wait(lambda state: "Undo unavailable" in state["message"])
            passed("integrity", "private-profile-refusal")
            actual = root / "actual-success"
            result = command(unsafe, "copy", source, actual)
            assert result["status"] == "ok" and actual.read_bytes() == source.read_bytes()
            assert "Undo unavailable" in result["message"] and not history(unsafe)
            passed("integrity", "unavailable-persistence-outcome"); stop(unsafe)

            # Public Emacs creates its own fresh native process in the profile
            # containing a committed group from a separate prior GUI process.
            workflow = base / "workflow"; os.environ["SUMI_EXPLORER_UNDO_DIR"] = str(workflow)
            before = start("before-emacs"); emacsout = root / "emacs-output"
            assert command(before, "copy", source, emacsout)["status"] == "ok"; stop(before)
            script = artifacts / "restart-inverse.el"
            script.write_text('(require \'sumi-explorer)\n'
                              f'(sumi-explorer-start {json.dumps(str(root))} {json.dumps(str(args.binary.resolve()))})\n'
                              '(unwind-protect (progn (unless (cdr (assoc "groups" (sumi-explorer-undo-history))) (error "History missing")) (sumi-explorer-undo nil 20)) (sumi-explorer-stop))\n')
            log = artifacts / "emacs-restart.log"
            with log.open("wb") as stream:
                result = subprocess.run(["emacs", "-Q", "--batch", "-L", str(ROOT / "explorer"), "-l", str(script)], stdout=stream, stderr=subprocess.STDOUT, timeout=45)
            assert result.returncode == 0 and not emacsout.exists(), log.read_text()[-2000:]
            evidence[script.name] = digest(script); evidence[log.name] = digest(log)
            passed("workflow", "emacs-restart-inverse")
            beforegui = start("before-gui"); guiout = root / "gui-output"
            assert command(beforegui, "copy", source, guiout)["status"] == "ok"; stop(beforegui)
            aftergui = start("after-gui"); aftergui.ui().click("Undo", "button")
            deadline = time.monotonic() + 20
            while guiout.exists() and time.monotonic() < deadline: time.sleep(.03)
            assert not guiout.exists() and source.read_bytes() == b"durable source\n"
            passed("workflow", "gui-restart-inverse"); stop(aftergui)
            replacement = root / "replace-after-restart"
            replacement.write_bytes(b"previous replacement bytes\n")
            before_replace = start("before-replacement")
            result = command(before_replace, "transfer", source, replacement, policy="replace", mode="copy")
            assert result["status"] == "ok" and replacement.read_bytes() == source.read_bytes()
            stop(before_replace)
            after_replace = start("after-replacement")
            assert command(after_replace, "undo", history(after_replace)[0]["id"])["status"] == "ok"
            assert replacement.read_bytes() == b"previous replacement bytes\n" and source.read_bytes() == b"durable source\n"
            passed("workflow", "replacement-restart-inverse"); stop(after_replace)
            merge_source, merge_destination = root / "merge-source", root / "merge-destination"
            merge_source.mkdir(); merge_destination.mkdir()
            (merge_source / "same").write_bytes(b"new")
            (merge_source / "added").write_bytes(b"added")
            (merge_destination / "same").write_bytes(b"old")
            (merge_destination / "unrelated").write_bytes(b"keep")
            before_merge = start("before-merge")
            result = command(before_merge, "transfer", merge_source, merge_destination, policy="merge-replace", mode="copy")
            assert result["status"] == "ok" and (merge_destination / "same").read_bytes() == b"new"
            stop(before_merge)
            after_merge = start("after-merge")
            assert command(after_merge, "undo", history(after_merge)[0]["id"])["status"] == "ok"
            assert (merge_destination / "same").read_bytes() == b"old" and not (merge_destination / "added").exists()
            assert (merge_destination / "unrelated").read_bytes() == b"keep" and (merge_source / "added").read_bytes() == b"added"
            passed("workflow", "merge-restart-inverse"); stop(after_merge)

            def fresh_profile(name):
                path = base / (name + "-profile")
                os.environ["SUMI_EXPLORER_UNDO_DIR"] = str(path)
                return path

            linkprofile = fresh_profile("link-audit")
            linked = start("linked-audit")
            linkout, linkrename = root / "link-out", root / "link-rename"
            assert command(linked, "copy", source, linkout)["status"] == "ok"
            assert command(linked, "rename", linkout, linkrename)["status"] == "ok"; stop(linked)
            journal = next(linkprofile.glob("*.journal")); raw = journal.read_bytes()
            _, links = wire_layout(raw); projection = next(fields[88] for parent, fields in links if not parent)
            for index, invalid in enumerate((b"f", b"f" * 15, b"g" * 16, b"f" * 17, b"f" * 16)):
                hostile = inject_string(raw, projection, invalid); journal.write_bytes(hostile)
                tested = start(f"projection-{index}")
                assert not history(tested) and journal.read_bytes() == hostile
                tested.wait(lambda state: "Undo unavailable" in state["message"]); stop(tested)
            passed("integrity", "malformed-projection-refusal")

            parentprofile = fresh_profile("parent-audit"); parented = start("parent-audit")
            folder = root / "guard-parent"
            assert command(parented, "mkdir", folder)["status"] == "ok"
            assert command(parented, "copy", source, folder / "child")["status"] == "ok"; stop(parented)
            journal = next(parentprofile.glob("*.journal")); raw = journal.read_bytes()
            _, links = wire_layout(raw); parentfields = next(fields for parent, fields in links if parent)
            nullable = bytearray(raw); nullable[parentfields[80][0]:parentfields[80][1]] = (0).to_bytes(8, "little")
            journal.write_bytes(inject_string(bytes(nullable), parentfields[72], None))
            tested = start("nullable-parent-guard"); assert len(history(tested)) == 2; stop(tested)
            incoherent = bytearray(raw); incoherent[parentfields[80][0]:parentfields[80][1]] = (1).to_bytes(8, "little")
            hostile = inject_string(bytes(incoherent), parentfields[72], None); journal.write_bytes(hostile)
            tested = start("incoherent-parent-guard"); assert not history(tested) and journal.read_bytes() == hostile; stop(tested)
            passed("integrity", "parent-link-guard-refusal")

            siblingprofile = fresh_profile("pending-siblings")
            pending = start("older-pending")
            assert command(pending, "copy", source, root / "older-pending-copy")["status"] == "ok"
            oldjournal = next(siblingprofile.glob("*.journal"))
            ordinary = start("newer-ordinary")
            raw = oldjournal.read_bytes(); intent = b"retained-backup-A"
            interrupted = inject_string(raw, (16, 24), intent); oldjournal.write_bytes(interrupted); stop(pending, kill=True)
            another = siblingprofile / f"j-{uuid.uuid4()}.journal"; another.write_bytes(inject_string(raw, (16, 24), b"retained-backup-B")); another.chmod(0o600)
            assert command(ordinary, "copy", source, root / "newer-ordinary-copy")["status"] == "ok"; stop(ordinary)
            tested = start("all-pending-recovery"); message = tested.wait(lambda state: "Interrupted operation" in state["message"])["message"]
            assert not history(tested) and all(value in message for value in (oldjournal.name, another.name, "retained-backup-A", "retained-backup-B"))
            assert oldjournal.read_bytes() == interrupted; stop(tested)
            passed("integrity", "all-pending-siblings-reported")

            sequenceprofile = fresh_profile("sequence-audit"); initial = start("sequence-initial")
            assert command(initial, "copy", source, root / "sequence-original")["status"] == "ok"
            firstid = int(history(initial)[0]["id"]); stop(initial)
            sequence = sequenceprofile / "sequence"; marker = sequenceprofile / "established"
            sequence_raw, marker_raw = sequence.read_bytes(), marker.read_bytes()
            sequence.unlink(); tested = start("deleted-sequence")
            assert not history(tested) and not sequence.exists(); tested.wait(lambda state: "Undo unavailable" in state["message"]); stop(tested)
            passed("integrity", "deleted-established-counter-refusal")
            sequence.write_bytes(sequence_raw); sequence.chmod(0o600); os.utime(sequence, ns=(0, 0))
            tested = start("valid-zero-time-sequence")
            assert command(tested, "copy", source, root / "zero-time-output")["status"] == "ok"
            assert int(history(tested)[0]["id"]) > firstid; stop(tested)
            sequence_raw = sequence.read_bytes(); sequence.chmod(0); os.utime(sequence, ns=(0, 0))
            tested = start("unreadable-zero-time-sequence"); assert not history(tested); stop(tested)
            sequence.chmod(0o600); assert sequence.read_bytes() == sequence_raw
            passed("integrity", "zero-time-counter-distinction")
            for index, which in enumerate((sequence, marker)):
                saved_raw = which.read_bytes(); which.write_bytes(b"invalid established sequence state")
                tested = start(f"invalid-sequence-{index}"); assert not history(tested) and which.read_bytes() == b"invalid established sequence state"; stop(tested)
                which.write_bytes(saved_raw)
            marker.unlink(); tested = start("missing-marker"); assert not history(tested); stop(tested)
            marker.write_bytes(marker_raw); marker.chmod(0o600)
            passed("integrity", "invalid-sequence-state-refusal")
            journal = next(sequenceprofile.glob("*.journal")); raw = journal.read_bytes(); ids, _ = wire_layout(raw)
            older = bytearray(raw[:-64]); high = int.from_bytes(sequence_raw[8:16], "little") + 1000
            older[8:16] = high.to_bytes(8, "little")
            for index, span in enumerate(ids): older[span[0]:span[1]] = (high - len(ids) + index + 1).to_bytes(8, "little")
            sibling = sequenceprofile / f"j-{uuid.uuid4()}.journal"; sibling.write_bytes(checksum(older)); sibling.chmod(0o600); os.utime(sibling, ns=(1, 1))
            tested = start("older-high-id"); assert not history(tested) and sequence.read_bytes() == sequence_raw; stop(tested)
            passed("integrity", "older-higher-id-refusal")

            # Inject an actual kernel fsync failure after inverse mutation. The
            # journal's own filesystem barriers remain unaffected.
            preload_source = artifacts / "fail-inverse-fsync.c"
            preload_source.write_text('''#define _GNU_SOURCE
#include <dlfcn.h>
#include <unistd.h>
#include <errno.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <sys/file.h>
int fsync(int fd) {
  const char *armed=getenv("SUMI_TEST_SYNC_ARM"), *target=getenv("SUMI_TEST_SYNC_TARGET");
  char name[64], path[16384]; snprintf(name,sizeof(name),"/proc/self/fd/%d",fd);
  ssize_t n=readlink(name,path,sizeof(path)-1);
  if(n>=0) { path[n]=0; if(armed && target && access(armed,F_OK)==0 && !strcmp(path,target)) { errno=EIO; return -1; } }
  int (*real_sync)(int)=dlsym(RTLD_NEXT,"fsync"); return real_sync(fd);
}
void *g_dir_open(const char *path, unsigned flags, void *error) {
  const char *target=getenv("SUMI_TEST_SCAN_TARGET"), *ready=getenv("SUMI_TEST_SCAN_READY"), *gate=getenv("SUMI_TEST_SCAN_GATE");
  static unsigned hits=0;
  if(target && ready && gate && !strcmp(path,target) && ++hits==2) {
    FILE *out=fopen(ready,"w"); if(out) { fputs("paused-after-sequence-read\\n",out); fclose(out); }
    for(unsigned i=0;i<1000 && access(gate,F_OK)!=0;++i) usleep(10000);
  }
  void *(*real_open)(const char *,unsigned,void *)=dlsym(RTLD_NEXT,"g_dir_open"); return real_open(path,flags,error);
}
int flock(int fd, int operation) {
  int (*real_lock)(int,int)=dlsym(RTLD_NEXT,"flock"); int result=real_lock(fd,operation), saved=errno;
  const char *target=getenv("SUMI_TEST_LOCK_TARGET"), *ready=getenv("SUMI_TEST_LOCK_READY");
  if(result<0 && (saved==EWOULDBLOCK || saved==EAGAIN) && target && ready) {
    char name[64],path[16384]; snprintf(name,sizeof(name),"/proc/self/fd/%d",fd);
    ssize_t n=readlink(name,path,sizeof(path)-1);
    if(n>=0) { path[n]=0; if(!strcmp(path,target)) { FILE *out=fopen(ready,"a"); if(out) { fprintf(out,"%ld\\n",(long)getpid()); fclose(out); } } }
  }
  errno=saved; return result;
}
''')
            preload = artifacts / "fail-inverse-fsync.so"
            subprocess.run(["cc", "-shared", "-fPIC", "-Wall", "-Wextra", "-Werror", str(preload_source), "-o", str(preload), "-ldl"], check=True)
            evidence[preload_source.name] = digest(preload_source); evidence[preload.name] = digest(preload)
            for mode in ("rename", "chmod"):
                faultprofile = fresh_profile("fault-" + mode); arm = base / ("arm-" + mode)
                original_preload = os.environ.get("LD_PRELOAD")
                os.environ.update(LD_PRELOAD=str(preload), SUMI_TEST_SYNC_ARM=str(arm), SUMI_TEST_SYNC_TARGET=str(root if mode == "rename" else source))
                fault = start("fault-" + mode)
                try:
                    if mode == "rename":
                        target = root / "fault-rename-output"; assert command(fault, "copy", source, target)["status"] == "ok"
                    else:
                        source.chmod(0o600); assert command(fault, "chmod", source, "0640")["status"] == "ok"
                    groupid = history(fault)[0]["id"]; arm.touch()
                    result = command(fault, "undo", groupid)
                    assert result["status"] == "error" and not history(fault)
                    if mode == "rename":
                        assert not target.exists()
                        retained = list(root.glob(".sumi-undo-*/item")); assert any(path.read_bytes() == source.read_bytes() for path in retained)
                    else: assert source.stat().st_mode & 0o777 == 0o600
                finally:
                    arm.unlink(missing_ok=True); stop(fault)
                    if original_preload is None: os.environ.pop("LD_PRELOAD", None)
                    else: os.environ["LD_PRELOAD"] = original_preload
                    os.environ.pop("SUMI_TEST_SYNC_ARM", None); os.environ.pop("SUMI_TEST_SYNC_TARGET", None)
                recovered = start("fault-recovery-" + mode)
                assert not history(recovered); recovered.wait(lambda state: "Interrupted operation" in state["message"]); stop(recovered)
                passed("integrity", "inverse-" + mode + "-sync-failure")

            def wait_file(path, predicate=lambda text: bool(text)):
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    if path.exists() and predicate(path.read_text()): return
                    time.sleep(.01)
                raise AssertionError(f"Timed out waiting for injected barrier {path.name}")

            def clear_test_environment(previous):
                for key, value in previous.items():
                    if value is None: os.environ.pop(key, None)
                    else: os.environ[key] = value

            # The restorer snapshots sequence=1, then pauses before opening its
            # history scan. A real live writer commits ID=2 and closes. The
            # decoded journal must be compared with a fresh locked sequence.
            scanprofile = fresh_profile("counter-scan-race"); writer = start("scan-live-writer")
            assert command(writer, "copy", source, root / "scan-before")["status"] == "ok"
            ready, gate = base / "scan-ready", base / "scan-gate"
            keys = ("LD_PRELOAD", "SUMI_TEST_SCAN_TARGET", "SUMI_TEST_SCAN_READY", "SUMI_TEST_SCAN_GATE")
            previous = {key: os.environ.get(key) for key in keys}
            os.environ.update(LD_PRELOAD=str(preload), SUMI_TEST_SCAN_TARGET=str(scanprofile), SUMI_TEST_SCAN_READY=str(ready), SUMI_TEST_SCAN_GATE=str(gate))
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(start, "scan-paused-restorer")
                try:
                    wait_file(ready)
                    assert command(writer, "copy", source, root / "scan-during")["status"] == "ok"
                    committed = history(writer)[0]["id"]; stop(writer)
                finally: gate.touch()
                restored = future.result(timeout=15)
            clear_test_environment(previous)
            assert history(restored)[0]["id"] == committed and len(history(restored)) == 2
            assert command(restored, "copy", source, root / "scan-after")["status"] == "ok"
            assert int(history(restored)[0]["id"]) > int(committed); stop(restored)
            passed("persistence", "commit-close-during-restore")

            lockprofile = fresh_profile("counter-busy"); seeded = start("busy-seed"); stop(seeded)
            ready = base / "busy-ready"; lockpath = lockprofile / "sequence.lock"
            keys = ("LD_PRELOAD", "SUMI_TEST_LOCK_TARGET", "SUMI_TEST_LOCK_READY")
            previous = {key: os.environ.get(key) for key in keys}
            os.environ.update(LD_PRELOAD=str(preload), SUMI_TEST_LOCK_TARGET=str(lockpath), SUMI_TEST_LOCK_READY=str(ready))
            with lockpath.open("r+b") as held, concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                fcntl.flock(held, fcntl.LOCK_EX)
                future = pool.submit(start, "busy-startup")
                try: wait_file(ready); assert not future.done()
                finally: fcntl.flock(held, fcntl.LOCK_UN)
                busy = future.result(timeout=15)
                assert command(busy, "copy", source, root / "busy-before")["status"] == "ok"
                firstid = int(history(busy)[0]["id"])
                ready.unlink(); fcntl.flock(held, fcntl.LOCK_EX)
                future = pool.submit(command, busy, "copy", source, root / "busy-during")
                try: wait_file(ready); assert not future.done()
                finally: fcntl.flock(held, fcntl.LOCK_UN)
                assert future.result(timeout=15)["status"] == "ok"
                assert int(history(busy)[0]["id"]) > firstid
                assert command(busy, "copy", source, root / "busy-after")["status"] == "ok"
                assert len(history(busy)) == 3; stop(busy)
            clear_test_environment(previous)
            passed("persistence", "transient-sequence-contention")

            concurrentprofile = fresh_profile("counter-concurrent")
            ready = base / "concurrent-busy-ready"; lockpath = concurrentprofile / "sequence.lock"
            previous = {key: os.environ.get(key) for key in keys}
            os.environ.update(LD_PRELOAD=str(preload), SUMI_TEST_LOCK_TARGET=str(lockpath), SUMI_TEST_LOCK_READY=str(ready))
            left = start("concurrent-left"); right = start("concurrent-right")
            with lockpath.open("r+b") as held, concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                fcntl.flock(held, fcntl.LOCK_EX)
                futures = [pool.submit(command, session, "copy", source, root / name) for session, name in ((left, "concurrent-left-output"), (right, "concurrent-right-output"))]
                try:
                    expected = {str(left.process.pid), str(right.process.pid)}
                    wait_file(ready, lambda text: expected <= set(text.splitlines()))
                    assert all(not future.done() for future in futures)
                finally: fcntl.flock(held, fcntl.LOCK_UN)
                assert all(future.result(timeout=15)["status"] == "ok" for future in futures)
            ids = [int(history(session)[0]["id"]) for session in (left, right)]
            assert len(set(ids)) == 2 and min(ids) > 0
            reserved = int.from_bytes((concurrentprofile / "sequence").read_bytes()[8:16], "little")
            assert reserved == max(ids)
            for session, name in ((left, "left-followup"), (right, "right-followup")):
                assert command(session, "copy", source, root / name)["status"] == "ok"
                assert int(history(session)[0]["id"]) > max(ids)
                stop(session)
            clear_test_environment(previous)
            passed("persistence", "concurrent-id-reservations")
        finally:
            for session in sessions:
                if not session.log.closed: session.stop()
            if saved is None: os.environ.pop("SUMI_EXPLORER_UNDO_DIR", None)
            else: os.environ["SUMI_EXPLORER_UNDO_DIR"] = saved
    record = {"version": 2, "binary": {"path": str(args.binary.resolve()), "sha256": digest(args.binary)},
              "sources": {name: digest(ROOT / name) for name in SOURCES}, "checks": checks,
              "evidence_root": str(artifacts), "evidence": evidence}
    receipt = artifacts / "durable-undo.json"; receipt.write_text(json.dumps(record, indent=2) + "\n")
    verify(receipt)
    if args.negative_controls:
        for label in ("missing-check", "false-check", "bad-binary", "bad-evidence", "missing-evidence-root", "bad-evidence-root"):
            bad = json.loads(json.dumps(record))
            if label == "missing-check": del bad["checks"]["persistence"]["restart-copy"]
            elif label == "false-check": bad["checks"]["persistence"]["restart-copy"] = False
            elif label == "bad-binary": bad["binary"]["sha256"] = "0" * 64
            elif label == "bad-evidence": bad["evidence"][next(iter(evidence))] = "0" * 64
            elif label == "missing-evidence-root": del bad["evidence_root"]
            else: bad["evidence_root"] = str(artifacts / "absent-evidence")
            path = artifacts / f"negative-{label}.json"; path.write_text(json.dumps(bad))
            try: verify(path)
            except (AssertionError, ValueError, KeyError, OSError): print(f"PASS negative control {label}")
            else: raise AssertionError(f"Verifier accepted {label}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path)
    parser.add_argument("--artifacts", type=Path)
    parser.add_argument("--verify", type=Path)
    parser.add_argument("--check", choices=["all", *CHECKS], default="all")
    parser.add_argument("--negative-controls", action="store_true")
    args = parser.parse_args()
    if args.verify: verify(args.verify, args.check)
    else:
        if not args.binary or not args.artifacts: parser.error("--binary and --artifacts are required")
        run(args)
