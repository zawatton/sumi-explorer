#!/usr/bin/env python3
"""Real Linux pointer-driven COPY and portable context-menu acceptance.

Run under heavy-run.sh and xvfb-run -a dbus-run-session -- python3 ... .
Nautilus and xdotool are required; their absence is an explicit failure.
Python controls native applications only; no application logic is substituted.
"""
import argparse
import importlib.util
import os
from pathlib import Path
import resource
import shutil
import subprocess
import tempfile
import time


def wait_for(check, description, seconds=8):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.05)
    raise AssertionError(f"Timed out: {description}")


def run(args):
    for program in ("xdotool", "nautilus"):
        if not shutil.which(program):
            raise RuntimeError(f"UNAVAILABLE: required real application {program}")
    if not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        raise RuntimeError("Run inside an isolated dbus-run-session")
    with tempfile.TemporaryDirectory(prefix="sumi-no-display-") as temporary:
        env = os.environ.copy()
        for key in ("DISPLAY", "WAYLAND_DISPLAY", "SUMI_EXPLORER_SESSION"):
            env.pop(key, None)
        env.update(TMPDIR=temporary, GDK_BACKEND="x11")
        result = subprocess.run([str(args.binary)], env=env, capture_output=True, timeout=5)
        assert result.returncode != 0
        assert not list(Path(temporary).iterdir()), "Failed display initialization leaked a session"
        print("PASS no-display startup fails without allocating a session", flush=True)
    spec = importlib.util.spec_from_file_location("explorer_gui", args.gui_harness)
    gui = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gui)
    artifacts = args.artifacts or args.binary.parent / "explorer-dnd-evidence"
    artifacts.mkdir(parents=True, exist_ok=True)
    sessions = []
    nautilus = None
    environment_log = (artifacts / "nautilus.log").open("w")
    with tempfile.TemporaryDirectory(prefix="sumi-dnd-check-") as directory:
        base = Path(directory)
        roots = [base / "source", base / "destination"]
        for root in roots:
            root.mkdir()
        source = roots[0] / "日本語 external.txt"
        source.write_bytes(b"real external COPY\n")
        folder = roots[0] / "unsupported-directory"
        folder.mkdir()
        (folder / "keep.txt").write_bytes(b"must not copy recursively")
        try:
            windows = []
            for i, root in enumerate(roots):
                session_dir = base / f"session-{i}"
                session_dir.mkdir(mode=0o700)
                previous = set(gui.xdo("search", "--name", "^Sumi Explorer$").splitlines()) if i else set()
                session = gui.Session(args.binary, root, session_dir)
                sessions.append(session)
                session.state()
                found = set(gui.xdo("search", "--sync", "--name", "^Sumi Explorer$").splitlines()) - previous
                assert len(found) == 1, found
                windows.append(found.pop())
                gui.xdo("windowsize", windows[-1], "500", "500")
                gui.xdo("windowmove", windows[-1], str(i * 600), "0")
            time.sleep(0.3)

            def drag(start, finish):
                time.sleep(0.4)  # Avoid interpreting a preceding discovery click as double-click.
                gui.xdo("mousemove", *map(str, start))
                gui.xdo("mousedown", "1")
                try:
                    time.sleep(0.15)
                    for part in range(1, 9):
                        point = [round(a + (b - a) * part / 8) for a, b in zip(start, finish)]
                        gui.xdo("mousemove", *map(str, point))
                        time.sleep(0.1)
                    time.sleep(0.25)
                finally:
                    gui.xdo("mouseup", "1")

            # Row focus plus pointer drag verifies selection identity separately
            # from the first row's display order.
            sessions[0].command("select", str(source))
            gui.xdo("windowfocus", windows[0])
            gui.xdo("key", "Home")
            # GIO order is not stable: the selected row's Y is discovered by
            # actual clicks and published state, rather than assuming sorting.
            def row_y(path):
                for y in range(45, 200, 10):
                    time.sleep(0.4)
                    gui.xdo("mousemove", "80", str(y))
                    gui.xdo("click", "1")
                    time.sleep(0.06)
                    if sessions[0].state()["selected"] == str(path):
                        return y
                raise AssertionError(f"No visible row for {path}")

            sessions[0].command("select", str(folder))
            failed = sessions[0].command("open", str(roots[0] / "missing-directory"))
            assert failed["status"] == "error"
            response = sessions[0].directory / "response.ini"
            wait_for(lambda: response.exists() and b"status=error" in response.read_bytes(), "durable error response")
            durable = response.read_bytes()
            file_y = row_y(source)
            wait_for(lambda: sessions[0].state()["status"] == "ok", "mouse selection updates live state")
            assert response.read_bytes() == durable, "GUI selection overwrote command failure"
            print("PASS failed command response remains immutable after real mouse selection", flush=True)
            drag((80, file_y), (800, 200))
            destination = roots[1] / source.name
            wait_for(destination.exists, "interprocess destination creation")
            assert destination.read_bytes() == source.read_bytes()
            assert source.exists(), "COPY deleted source"
            print("PASS real Sumi -> separate Sumi process file-list COPY", flush=True)

            destination.write_bytes(b"existing destination must survive")
            drag((80, file_y), (800, 200))
            wait_for(lambda: sessions[1].state()["status"] == "error", "collision error")
            assert destination.read_bytes() == b"existing destination must survive"
            assert "exist" in sessions[1].state()["message"].lower()
            print("PASS collision error preserves existing destination bytes", flush=True)

            sessions[1].command("refresh")
            directory_y = row_y(folder)
            drag((80, directory_y), (800, 200))
            wait_for(lambda: sessions[1].state()["status"] == "error", "directory-drop rejection")
            assert not (roots[1] / folder.name).exists()
            assert sessions[1].state()["message"]
            print("PASS unsupported directory drop reports error without mutation", flush=True)

            # Right-click must select the row under the pointer, not stale state.
            sessions[0].command("select", str(source))
            gui.xdo("mousemove", "80", str(directory_y))
            gui.xdo("click", "3")
            wait_for(lambda: sessions[0].state()["selected"] == str(folder), "right-click row selection")
            gui.xdo("key", "Tab", "Return")
            properties = gui.xdo("search", "--sync", "--name", "^Sumi Properties$")
            assert properties
            print("PASS real right-click row selection and context Properties window", flush=True)
            gui.xdo("key", "Escape")
            sessions[0].stop()
            sessions[1].stop()

            # Independent desktop application receiver, not another callback or
            # a mock content provider. Isolated D-Bus prevents shared desktop use.
            external = base / "nautilus-destination"
            external.mkdir()
            fresh = base / "fresh-session"
            fresh.mkdir()
            sender = gui.Session(args.binary, roots[0], fresh)
            sessions.append(sender)
            sender.state()
            w = gui.xdo("search", "--sync", "--name", "^Sumi Explorer$").splitlines()[-1]
            gui.xdo("windowsize", w, "500", "500")
            gui.xdo("windowmove", w, "0", "0")
            nautilus = subprocess.Popen(["nautilus", "--new-window", str(external)],
                                        stdout=environment_log, stderr=environment_log)
            nw = gui.xdo("search", "--sync", "--onlyvisible", "--class", "org.gnome.Nautilus").splitlines()[-1]
            gui.xdo("windowsize", nw, "650", "500")
            gui.xdo("windowmove", nw, "600", "0")
            time.sleep(0.5)
            sender.command("select", str(source))
            # Discover current sender row by real clicks as above.
            sessions[0] = sender
            file_y = row_y(source)
            drag((80, file_y), (900, 220))
            wait_for(lambda: (external / source.name).exists(), "Nautilus external COPY")
            assert (external / source.name).read_bytes() == source.read_bytes()
            assert source.exists()
            if shutil.which("import"):
                subprocess.run(["import", "-window", "root", str(artifacts / "external-copy.png")], check=True, timeout=8)
            print("PASS real Sumi -> Nautilus COPY with identical bytes and retained source", flush=True)
        finally:
            for i, session in enumerate(sessions):
                session.stop()
                log = session.directory / "host.log"
                if log.exists():
                    shutil.copyfile(log, artifacts / f"host-{i}.log")
            if nautilus is not None:
                nautilus.terminate()
                try:
                    nautilus.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    nautilus.kill()
                    nautilus.wait(timeout=3)
            environment_log.close()


if __name__ == "__main__":
    resource.setrlimit(resource.RLIMIT_AS, (8 * 1024**3, 8 * 1024**3))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=Path(__file__).resolve().parents[1] / "target" / "sumi-explorer")
    parser.add_argument("--gui-harness", type=Path, default=Path(__file__).with_name("explorer-gui-test.py"))
    parser.add_argument("--artifacts", type=Path)
    run(parser.parse_args())
