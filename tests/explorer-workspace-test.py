#!/usr/bin/env python3
"""Real stage-1 workspace acceptance; run under Xvfb with a private D-Bus session."""
import argparse
import configparser
import importlib.util
import os
from pathlib import Path
import resource
import shutil
import signal
import subprocess
import tempfile
import time


def wait(check, description, seconds=15):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        value = check()
        if value:
            return value
        time.sleep(0.025)
    raise AssertionError(f"Timed out: {description}")


def run(args):
    spec = importlib.util.spec_from_file_location("gui", Path(__file__).with_name("explorer-gui-test.py"))
    gui = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gui)
    artifacts = args.artifacts or args.binary.resolve().parent / "workspace-evidence"
    artifacts.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="sumi workspace 日本語 ") as temporary:
        base = Path(temporary)
        root = base / "root folder"
        root.mkdir()
        a, b, c = [root / name for name in ("日本語 A", "B space", "C;literal")]
        for directory in (a, b, c):
            directory.mkdir()
            (directory / "selected 日本語.txt").write_bytes(b"identity")
        session_dir = base / "session"
        session_dir.mkdir(mode=0o700)
        private_tmp = base / "child sessions"
        private_tmp.mkdir()
        old_env = {key: os.environ.get(key) for key in ("TMPDIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME")}
        os.environ.update(TMPDIR=str(private_tmp), XDG_CONFIG_HOME=str(base / "config"), XDG_DATA_HOME=str(base / "data"))
        session = gui.Session(args.binary.resolve(), root, session_dir)
        children = []
        try:
            def workspace(directory=session_dir):
                path = directory / "workspace.ini"
                if not path.exists():
                    return None
                assert path.stat().st_size <= 1048576
                parser = configparser.RawConfigParser(strict=True)
                parser.read_string(path.read_text())
                assert parser.sections()[0] == "Workspace", parser.sections()
                assert set(parser["Workspace"]) == {"version", "active", "count"}
                assert parser["Workspace"]["version"] == "1"
                count = int(parser["Workspace"]["count"])
                assert 1 <= count <= 64
                assert parser.sections() == ["Workspace"] + [f"Tab{i}" for i in range(count)]
                tabs = []
                for i in range(count):
                    tab = {key: gui.unescape(value) for key, value in parser[f"Tab{i}"].items()}
                    assert set(tab) == {"id", "directory", "back", "forward"}
                    assert 0 < int(tab["id"]) <= 9223372036854775807
                    assert Path(tab["directory"]).is_absolute()
                    assert tab["back"] in {"0", "1"} and tab["forward"] in {"0", "1"}
                    tabs.append(tab)
                assert len({tab["id"] for tab in tabs}) == count
                active = parser["Workspace"]["active"]
                assert active in {tab["id"] for tab in tabs}
                return active, tabs

            def command(name, path="", good=True):
                state = session.command(name, str(path))
                assert state["status"] == ("ok" if good else "error"), state
                return state

            command("new-tab", a)
            active, tabs = workspace()
            assert len(tabs) == 2 and tabs[-1]["directory"] == str(a)
            assert active == tabs[-1]["id"]
            print("PASS new-tab creates independent stable-ID workspace snapshot", flush=True)
            first, second = tabs[0]["id"], tabs[1]["id"]
            selected = a / "selected 日本語.txt"
            command("select", selected)
            command("activate-tab", first)
            assert session.state()["directory"] == str(root)
            command("activate-tab", second)
            assert session.state()["selected"] == str(selected)
            command("open", b)
            command("open", c)
            command("back")
            assert session.state()["directory"] == str(b)
            command("back")
            assert session.state()["directory"] == str(a)
            command("forward")
            assert session.state()["directory"] == str(b)
            before = workspace()
            command("open", root / "missing", good=False)
            assert workspace() == before and session.state()["directory"] == str(b)
            command("open", a)
            command("forward", good=False)
            assert session.state()["directory"] == str(a)
            print("PASS per-tab selection and validated Back/Forward branch semantics", flush=True)
            command("activate-tab", "0", good=False)
            command("activate-tab", "01", good=False)
            command("activate-tab", "9223372036854775808", good=False)
            command("back", "unexpected", good=False)
            command("close-tab")
            assert len(workspace()[1]) == 1
            command("close-tab", good=False)
            print("PASS strict command paths/IDs and last-tab close rejection", flush=True)
            command("new-tab", a)
            third = workspace()[0]
            assert int(third) > int(second)
            command("select", selected)
            command("activate-tab", first)
            selected.unlink()
            state = command("activate-tab", third)
            assert state["selected"] == "" and "discarded" in state["message"]
            command("new-tab", root / "missing", good=False)
            assert workspace()[0] == third and len(workspace()[1]) == 2
            command("close-tab")
            print("PASS tab IDs never reuse and vanished selections/failed creation preserve workspace", flush=True)

            command("open", a)
            before = workspace()
            command("refresh")
            assert workspace() == before
            for i in range(132):
                command("open", b if i % 2 == 0 else c)
            for i in range(127):
                command("back")
            command("back", good=False)
            for i in range(127):
                command("forward")
            command("forward", good=False)
            command("home")
            assert session.state()["directory"] == os.path.expanduser("~")
            command("open", root)
            print("PASS bounded128 history and F5/Home behavior", flush=True)

            for i in range(63):
                command("new-tab", a if i % 2 else b)
            active, tabs = workspace()
            assert len(tabs) == 64
            command("new-tab", c, good=False)
            assert workspace() == (active, tabs)
            for i in range(63):
                command("close-tab")
            assert workspace()[0] == first and len(workspace()[1]) == 1
            print("PASS 64-tab cap, stable IDs and adjacent close selection", flush=True)

            (a / "keyboard-row").touch()
            window = gui.xdo("search", "--sync", "--name", "^Sumi Explorer$").splitlines()[-1]
            gui.xdo("windowsize", window, "1000", "700")
            gui.xdo("windowmove", window, "0", "0")
            gui.xdo("windowfocus", window)
            command("open", root)
            session.ui().row_click(a)
            gui.xdo("key", "ctrl+t")
            wait(lambda: len(workspace()[1]) == 2, "Ctrl+T actual tab creation")
            newest = workspace()[0]
            gui.xdo("key", "ctrl+Tab")
            wait(lambda: workspace()[0] == first, "Ctrl+Tab")
            gui.xdo("key", "ctrl+shift+Tab")
            wait(lambda: workspace()[0] == newest, "Ctrl+Shift+Tab")
            gui.xdo("key", "ctrl+l")
            gui.xdo("key", "ctrl+a")
            gui.xdo("type", "--clearmodifiers", "--", str(a))
            gui.xdo("key", "Return")
            wait(lambda: session.state()["directory"] == str(a), "Ctrl+L editable location")
            # Editing shortcuts must not create/close tabs while entry owns focus.
            gui.xdo("key", "ctrl+w")
            assert len(workspace()[1]) == 2
            session.ui().row_click(a / "keyboard-row")
            gui.xdo("key", "alt+Left")
            wait(lambda: session.state()["directory"] == str(root), "Alt+Left")
            gui.xdo("key", "alt+Right")
            wait(lambda: session.state()["directory"] == str(a), "Alt+Right")
            gui.xdo("key", "alt+Up")
            wait(lambda: session.state()["directory"] == str(root), "Alt+Up")
            before = workspace()
            gui.xdo("key", "F5")
            time.sleep(0.1)
            assert workspace() == before, ("F5 changed workspace", before, workspace())
            gui.xdo("key", "ctrl+w")
            wait(lambda: len(workspace()[1]) == 1, "Ctrl+W closes tab")
            print("PASS actual GTK workspace shortcuts and normal editing shortcuts", flush=True)

            # A blocked auxiliary snapshot must not falsify a completed mutation.
            workspace_path = session_dir / "workspace.ini"
            workspace_path.unlink()
            workspace_path.mkdir()
            source = root / "copy source;日本語"
            source.write_bytes(b"actual completion")
            destination = root / "copy destination"
            serial = session.serial + 1
            session.serial = serial
            request = session_dir / "request.tmp"
            request.write_text(f"[Explorer]\nversion=1\nrequest={serial}\ncommand=copy\npath={gui.escape(str(source))}\ndestination={gui.escape(str(destination))}\n")
            request.replace(session_dir / "request.ini")
            result = session.wait(lambda s: s["request"] == str(serial), response=True)
            assert result["status"] == "ok" and destination.read_bytes() == b"actual completion", result
            assert "workspace" in result["message"].lower()
            command("activate-tab", first, good=False)
            workspace_path.rmdir()
            command("refresh")
            assert workspace()[0] == first
            print("PASS workspace publication failure is explicit without rewriting file-operation success", flush=True)

            large = root / "busy source"
            large.mkdir()
            for i in range(10000):
                (large / f"item-{i}").write_bytes(b"busy guard")
            command("refresh")
            command("select", large)
            busy_before = workspace()
            serial = session.serial + 1
            session.serial = serial
            output = root / "busy output"
            request.write_text(f"[Explorer]\nversion=1\nrequest={serial}\ncommand=copy\npath={gui.escape(str(large))}\ndestination={gui.escape(str(output))}\n")
            request.replace(session_dir / "request.ini")
            wait(lambda: output.exists() and any(output.iterdir()), "actual task owns context")
            gui.xdo("key", "ctrl+t", "alt+Left", "F5")
            assert workspace() == busy_before
            session.serial += 1
            queued = session.serial
            request.write_text(f"[Explorer]\nversion=1\nrequest={queued}\ncommand=new-tab\npath=\n")
            request.replace(session_dir / "request.ini")
            state = session.wait(lambda s: s["request"] == str(queued), response=True, seconds=30)
            assert state["status"] == "error" and "operation" in state["message"].lower(), state
            assert workspace() == busy_before and len(list(output.iterdir())) == 10000
            command("refresh")
            print("PASS busy GUI and queued navigation preserve owning operation and tab/history", flush=True)
            failed_spawn(gui, args.binary.resolve(), base, artifacts)

            command("new-window", a)
            child_dir = wait(lambda: next((p for p in private_tmp.glob("sumi-explorer-*") if (p / "workspace.ini").exists()), None), "independent child session")
            child_workspace = workspace(child_dir)
            assert len(child_workspace[1]) == 1 and child_workspace[1][0]["directory"] == str(a)
            child_window = wait(lambda: next((w for w in gui.xdo("search", "--name", "^Sumi Explorer$").splitlines() if w != window), None), "first mapped child window")
            child_pid = int(gui.xdo("getwindowpid", child_window))
            children.append((child_pid, child_dir))
            assert child_dir != session_dir and child_workspace[0] == "1"
            (child_dir / "request.ini").write_text("[Explorer]\nversion=1\nrequest=1\ncommand=quit\npath=\n")
            wait(lambda: not child_dir.exists(), "first child cleanup")
            wait(lambda: not Path(f"/proc/{child_pid}").exists(), "child PID reaped while parent stays alive")
            children.clear()
            print("PASS shell-free independent child window/session and prompt process reaping", flush=True)

            command("new-window", b)
            child_dir = wait(lambda: next((p for p in private_tmp.glob("sumi-explorer-*") if (p / "workspace.ini").exists()), None), "second child session")
            child_window = wait(lambda: next((w for w in gui.xdo("search", "--name", "^Sumi Explorer$").splitlines() if w != window), None), "second mapped child window")
            child_pid = int(gui.xdo("getwindowpid", child_window))
            children.append((child_pid, child_dir))
            command("quit")
            session.process.wait(timeout=5)
            assert session.process.returncode == 0 and workspace(child_dir)[1][0]["directory"] == str(b)
            (child_dir / "request.ini").write_text("[Explorer]\nversion=1\nrequest=1\ncommand=new-tab\npath=\n")
            wait(lambda: len(workspace(child_dir)[1]) == 2, "child still responds after parent exits")
            print("PASS child window outlives parent and keeps an independent functioning workspace", flush=True)
        finally:
            for pid, directory in children:
                try:
                    (directory / "request.ini").write_text("[Explorer]\nversion=1\nrequest=999999\ncommand=quit\npath=\n")
                    wait(lambda: not directory.exists(), "child graceful cleanup", 5)
                except (OSError, AssertionError):
                    try:
                        os.kill(pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
            session.stop()
            shutil.copyfile(session_dir / "host.log", artifacts / "host.log")
            for key, value in old_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


def failed_spawn(gui, binary, base, artifacts):
    """A deleted private executable must produce a synchronous spawn error."""
    executable = base / "executable 日本語 with spaces"
    shutil.copy2(binary, executable)
    root = base / "failed spawn root"
    root.mkdir()
    directory = base / "failed spawn session"
    directory.mkdir(mode=0o700)
    peer = gui.Session(executable, root, directory)
    try:
        executable.unlink()
        result = peer.command("new-window")
        assert result["status"] == "error" and result["message"], result
        assert result["directory"] == str(root)
        print("PASS failed current-executable spawn reports an explicit error", flush=True)
    finally:
        peer.stop()
        shutil.copyfile(directory / "host.log", artifacts / "failed-spawn-host.log")


if __name__ == "__main__":
    resource.setrlimit(resource.RLIMIT_AS, (8 * 1024**3, 8 * 1024**3))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path)
    run(parser.parse_args())
