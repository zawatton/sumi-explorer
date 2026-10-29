#!/usr/bin/env python3
"""Disposable real-process mutation acceptance; run inside Xvfb."""
import argparse
import importlib.util
import os
from pathlib import Path
import resource
import shutil
import signal
import subprocess
import tempfile
import time


def wait(check, text, timeout=15):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = check()
        if value:
            return value
        time.sleep(0.025)
    raise AssertionError(f"Timed out: {text}")


def run(args):
    spec = importlib.util.spec_from_file_location("gui", Path(__file__).with_name("explorer-gui-test.py"))
    gui = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gui)
    artifacts = args.artifacts or args.binary.parent / "operations-evidence"
    artifacts.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="sumi-operations-") as temporary:
        base = Path(temporary)
        root = base / "日本語 root"
        root.mkdir()
        session_dir = base / "session"
        session_dir.mkdir(mode=0o700)
        xdg = base / "xdg"
        xdg.mkdir()
        old_xdg = os.environ.get("XDG_DATA_HOME")
        old_bus = os.environ.get("DBUS_SESSION_BUS_ADDRESS")
        os.environ["XDG_DATA_HOME"] = str(xdg)
        bus = None
        session = None
        try:
            # GVfs must inherit this fixture's isolated Trash directory.
            bus = subprocess.check_output(["dbus-daemon", "--session", "--fork", "--print-address=1", "--print-pid=1"], text=True, timeout=10).splitlines()
            os.environ["DBUS_SESSION_BUS_ADDRESS"] = bus[0]
            session = gui.Session(args.binary.resolve(), root, session_dir)
            def begin(command, path, destination=None):
                assert not (session_dir / "request.ini").exists()
                session.serial += 1
                text = f"[Explorer]\nversion=1\nrequest={session.serial}\ncommand={command}\npath={gui.escape(str(path))}\n"
                if destination is not None:
                    text += f"destination={gui.escape(str(destination))}\n"
                tmp = session_dir / "request.tmp"
                tmp.write_text(text)
                tmp.replace(session_dir / "request.ini")
                return session.serial

            def finish(serial, good=True, timeout=20):
                state = session.wait(lambda s: s["request"] == str(serial), seconds=timeout, response=True)
                assert state["status"] == ("ok" if good else "error"), state
                return state

            def command(name, path, destination=None, good=True):
                return finish(begin(name, path, destination), good)

            folder = root / "new 日本語 folder"
            command("mkdir", folder)
            assert folder.is_dir()
            print("PASS mkdir creates a real directory", flush=True)
            command("mkdir", folder, good=False)
            tree = root / "source tree"
            (tree / "nested 日本語").mkdir(parents=True)
            (tree / "nested 日本語" / "line\nfile.txt").write_bytes(b"tree bytes\0\xff")
            (tree / "relative-link").symlink_to("nested 日本語/line\nfile.txt")
            copied = root / "copied tree"
            command("copy", tree, copied)
            assert (copied / "nested 日本語" / "line\nfile.txt").read_bytes() == b"tree bytes\0\xff"
            assert (copied / "relative-link").is_symlink()
            assert os.readlink(copied / "relative-link") == os.readlink(tree / "relative-link")
            print("PASS Unicode recursive copy preserves nested bytes and symlink entries", flush=True)
            sentinel = copied / "sentinel"
            sentinel.write_bytes(b"existing survives")
            command("copy", tree, copied, good=False)
            assert sentinel.read_bytes() == b"existing survives"
            command("copy", tree, tree, good=False)
            command("copy", tree, tree / "descendant", good=False)
            alias = base / "outside-alias"
            alias.symlink_to(tree / "nested 日本語", target_is_directory=True)
            command("copy", tree, alias / "descendant", good=False)
            assert not (tree / "nested 日本語" / "descendant").exists()
            print("PASS collisions/self/descendant/destination-parent aliases reject without merging", flush=True)
            renamed = root / "renamed 日本語"
            command("rename", copied, renamed)
            assert renamed.is_dir() and not copied.exists()
            command("rename", renamed, folder / "different-parent", good=False)
            moved = folder / "moved tree"
            command("move", renamed, moved)
            assert moved.is_dir() and not renamed.exists()
            command("move", tree, moved, good=False)
            assert tree.exists() and moved.exists()
            print("PASS same-parent rename and native no-overwrite move", flush=True)
            trash_file = root / "trash 日本語.txt"
            trash_file.write_bytes(b"recoverable trash bytes")
            command("trash", trash_file)
            assert not trash_file.exists()
            assert any(p.is_file() and p.read_bytes() == b"recoverable trash bytes" for p in (xdg / "Trash" / "files").glob("*"))
            print("PASS GIO trash retains bytes in isolated XDG trash", flush=True)
            large = root / "cancel source"
            large.mkdir()
            for number in range(10000):
                (large / f"entry-{number:04d}").write_bytes(b"cancellation fixture" * 32)
            cancelled = root / "cancel destination"
            serial = begin("copy", large, cancelled)
            tmp = session_dir / "cancel.tmp"
            tmp.write_text(f"[Explorer]\nversion=1\nrequest={serial}\n")
            tmp.replace(session_dir / "cancel.ini")
            response = finish(serial, good=False)
            assert "cancel" in response["message"].lower(), response
            assert large.is_dir() and len(list(large.iterdir())) == 10000
            command("mkdir", root / "recovered")
            assert (root / "recovered").is_dir()
            print("PASS cooperative cancellation preserves source and next operation recovers", flush=True)
            in_flight = root / "in-flight destination"
            serial = begin("copy", large, in_flight)
            wait(lambda: in_flight.exists() and any(in_flight.iterdir()), "actual in-flight copy has created a child")
            tmp.write_text(f"[Explorer]\nversion=1\nrequest={serial}\n")
            tmp.replace(session_dir / "cancel.ini")
            response = finish(serial, good=False)
            assert "cancel" in response["message"].lower() and str(in_flight) in response["message"]
            assert len(list(large.iterdir())) == 10000
            command("mkdir", root / "recovered-in-flight")
            print("PASS real in-flight cancellation reports possible partial destination and recovers", flush=True)
            # Real pointer controls are exercised below after protocol checks.
            if not args.protocol_only:
                gui_checks(gui, session, root, wait, begin, finish)
                dnd_failure_checks(gui, args.binary.resolve(), base, artifacts)
        finally:
            try:
                if session is not None:
                    session.stop()
                    shutil.copyfile(session_dir / "host.log", artifacts / "host.log")
            finally:
                try:
                    if bus is not None:
                        try:
                            os.kill(int(bus[1]), signal.SIGTERM)
                        except ProcessLookupError:
                            pass
                finally:
                    if old_bus is None:
                        os.environ.pop("DBUS_SESSION_BUS_ADDRESS", None)
                    else:
                        os.environ["DBUS_SESSION_BUS_ADDRESS"] = old_bus
                    if old_xdg is None:
                        os.environ.pop("XDG_DATA_HOME", None)
                    else:
                        os.environ["XDG_DATA_HOME"] = old_xdg
        assert session.process.returncode == 0, session.process.returncode


def gui_checks(gui, session, root, wait, begin, finish):
    window = gui.xdo("search", "--sync", "--name", "^Sumi Explorer$").splitlines()[-1]
    gui.xdo("windowsize", window, "1000", "700")
    gui.xdo("windowmove", window, "0", "0")
    gui.xdo("windowfocus", window)
    time.sleep(0.15)

    def operation(action, text):
        ui = session.ui()
        if action in {"Copy to…", "Move to…"}:
            ui.click("More", "button"); ui.click(action, "button")
        else:
            ui.click(action, "button")
        ui.find("Destination path" if action in {"Copy to…", "Move to…"} else "Name", "text")
        gui.xdo("key", "ctrl+a")
        gui.xdo("type", "--clearmodifiers", "--", text)
        gui.xdo("key", "Return")

    source = root / "gui-source.txt"
    source.write_bytes(b"GUI bytes")
    session.command("refresh")
    session.command("select", str(source))
    durable = (session.directory / "response.ini").read_bytes()
    operation("Copy to…", "gui-copy.txt")
    copied = root / "gui-copy.txt"
    wait(lambda: copied.exists(), "pointer Copy")
    wait(lambda: session.state().get("message") == "", "GUI Copy completion")
    assert copied.read_bytes() == b"GUI bytes"
    assert (session.directory / "response.ini").read_bytes() == durable
    session.command("select", str(copied))
    operation("Rename", "gui-renamed.txt")
    renamed = root / "gui-renamed.txt"
    wait(lambda: renamed.exists() and not copied.exists(), "pointer Rename")
    wait(lambda: session.state().get("message") == "", "GUI Rename completion")
    operation("New folder", "gui-folder")
    folder = root / "gui-folder"
    wait(folder.is_dir, "pointer New Folder")
    wait(lambda: session.state().get("message") == "", "GUI mkdir completion")
    session.command("select", str(renamed))
    operation("Move to…", "gui-folder/gui-moved.txt")
    moved = folder / "gui-moved.txt"
    wait(lambda: moved.exists() and not renamed.exists(), "pointer Move")
    wait(lambda: session.state().get("message") == "", "GUI Move completion")
    session.command("open", str(folder))
    session.command("select", str(moved))
    session.ui().click("Trash", "button")
    confirm = gui.xdo("search", "--sync", "--name", "^Confirm Trash$").splitlines()[-1]
    assert moved.exists(), "Trash happened before confirmation"
    session.ui().click("Cancel", "button")
    assert moved.exists(), "Cancelling confirmation removed source"
    session.ui().click("Trash", "button")
    confirm = gui.xdo("search", "--sync", "--name", "^Confirm Trash$").splitlines()[-1]
    session.ui().click("Trash", "button")
    wait(lambda: not moved.exists(), "pointer-confirmed Trash")
    wait(lambda: session.state().get("message") == "", "GUI Trash completion")
    session.command("open", str(root))
    print("PASS pointer Copy/Move/Rename/New Folder and explicit Trash confirmation/cancel", flush=True)

    large = root / "cancel source"
    session.command("select", str(large))
    operation("Copy to…", "gui-long-copy")
    created = root / "gui-long-copy"
    wait(lambda: created.exists() and any(created.iterdir()), "GUI async copy starts")
    # Publish a future request and its cancellation while GUI ID0 is active.
    queued = root / "queued-must-not-exist"
    serial = begin("mkdir", queued)
    cancel = session.directory / "cancel.tmp"
    cancel.write_text(f"[Explorer]\nversion=1\nrequest={serial}\n")
    cancel.replace(session.directory / "cancel.ini")
    response = finish(serial, good=False)
    assert "cancel" in response["message"].lower() and not queued.exists()
    print("PASS queued protocol cancel survives active GUI task and prevents its mutation", flush=True)
    session.command("select", str(large))
    operation("Copy to…", "gui-cancel-copy")
    cancelled = root / "gui-cancel-copy"
    wait(lambda: cancelled.exists() and any(cancelled.iterdir()), "GUI copy starts before Cancel")
    session.ui().click("Cancel", "button")
    wait(lambda: session.state().get("status") == "error" and "cancel" in session.state().get("message", "").lower(), "GUI Cancel completion")
    assert len(list(large.iterdir())) == 10000
    session.command("refresh")
    print("PASS pointer Cancel cooperatively stops running GUI task without source deletion", flush=True)


def dnd_failure_checks(gui, binary, base, artifacts):
    """Real external file transfer: failure paths must retain target identity."""
    source = base / "dnd-source"
    target = base / "dnd-read-only"
    source.mkdir()
    target.mkdir()
    path = source / "DND 日本語.txt"
    path.write_bytes(b"source remains intact")
    sessions = []
    try:
        windows = []
        for index, root in enumerate((source, target)):
            previous = set(gui.xdo("search", "--name", "^Sumi Explorer$").splitlines())
            directory = base / f"dnd-session-{index}"
            directory.mkdir()
            peer = gui.Session(binary, root, directory)
            sessions.append(peer)
            # GTK publishes initial state before mapping its native window.
            def mapped():
                found = set(gui.xdo("search", "--name", "^Sumi Explorer$").splitlines()) - previous
                return next(iter(found)) if found else None
            window = wait(mapped, "peer native window")
            windows.append(window)
            gui.xdo("windowsize", window, "1000", "700")
            gui.xdo("windowmove", window, str(index * 1100), "0")
        sessions[0].command("select", str(path))
        target.chmod(0o500)

        def drag():
            gui.xdo("windowraise", windows[0])
            start = sessions[0].ui().row_point(path)
            finish = sessions[1].ui().point(role="list")
            gui.xdo("mousemove", *map(str, start))
            gui.xdo("mousedown", "1")
            try:
                for part in range(1, 9):
                    gui.xdo("mousemove", *(str(round(a+(b-a)*part/8)) for a,b in zip(start, finish)))
                    time.sleep(0.1)
                time.sleep(0.2)
            finally:
                gui.xdo("mouseup", "1")

        drag()
        def failed_state():
            state = sessions[1].state()
            return state if state.get("status") == "error" else None
        error = wait(failed_state, "real DND permission failure")
        destination = target / path.name
        assert str(destination) in error["message"] and "partial new destination" in error["message"], error
        assert not destination.exists() and path.read_bytes() == b"source remains intact"
        print("PASS real incoming DND failure reports possible partial destination path", flush=True)
        target.chmod(0o700)
        destination.write_bytes(b"pre-existing destination survives")
        sessions[1].command("refresh")
        drag()
        wait(lambda: sessions[1].state().get("status") == "error", "real DND collision")
        assert destination.read_bytes() == b"pre-existing destination survives"
        assert path.read_bytes() == b"source remains intact"
        print("PASS real incoming DND collision preserves prior destination bytes", flush=True)
    finally:
        target.chmod(0o700)
        for index, peer in enumerate(sessions):
            peer.stop()
            shutil.copyfile(peer.directory / "host.log", artifacts / f"dnd-host-{index}.log")


if __name__ == "__main__":
    resource.setrlimit(resource.RLIMIT_AS, (8 * 1024**3, 8 * 1024**3))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path)
    parser.add_argument("--protocol-only", action="store_true")
    run(parser.parse_args())
