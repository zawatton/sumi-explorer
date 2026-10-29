#!/usr/bin/env python3
"""Real GTK batches; run inside xvfb-run -a dbus-run-session -- python3 ... ."""
import argparse
import configparser
import importlib.util
import os
from pathlib import Path
import resource
import shutil
import subprocess
import tempfile
import time


def wait(check, description, seconds=20):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        value = check()
        if value:
            return value
        time.sleep(0.025)
    raise AssertionError(f"Timed out: {description}")


def run(args):
    spec = importlib.util.spec_from_file_location("gui", Path(__file__).with_name("explorer-gui-test.py"))
    gui = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gui)
    artifacts = args.artifacts or args.binary.resolve().parent / "batch-evidence"
    artifacts.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="sumi-batches-") as tmp:
        base = Path(tmp)
        root = base / "root"
        root.mkdir()
        paths = [root / "日本語.txt", root / "line\nsemi;slash\\.txt", root / "third.txt"]
        for i, path in enumerate(paths):
            path.write_bytes(f"source-{i}".encode())
        directory = base / "session"
        directory.mkdir()
        xdg = base / "xdg"
        xdg.mkdir()
        old_xdg = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = str(xdg)
        session = gui.Session(args.binary.resolve(), root, directory)
        try:
            def begin(command, items, destination=None, extra=""):
                session.serial += 1
                text = f"[Explorer]\nversion=1\nrequest={session.serial}\ncommand={command}\npath=\ncount={len(items)}\n"
                text += "".join(f"path{i}={gui.escape(str(path))}\n" for i, path in enumerate(items))
                if destination is not None:
                    text += f"destination={gui.escape(str(destination))}\n"
                text += extra
                temporary = directory / "request.tmp"
                temporary.write_text(text)
                temporary.replace(directory / "request.ini")
                return session.serial

            def finish(serial, good=True):
                state = session.wait(lambda s: s["request"] == str(serial), response=True, seconds=30)
                assert state["status"] == ("ok" if good else "error"), state
                return state

            def command(name, items, destination=None, good=True, extra=""):
                return finish(begin(name, items, destination, extra), good)

            def read(name):
                parser = configparser.RawConfigParser(strict=True)
                parser.read_string((directory / name).read_text())
                return {group: {k: gui.unescape(v) for k, v in parser[group].items()} for group in parser.sections()}

            command("select-many", paths[:2])
            selection = read("selection.ini")["Selection"]
            assert selection["count"] == "2" and {selection["path0"], selection["path1"]} == set(map(str, paths[:2]))
            before = (directory / "selection.ini").read_bytes()
            command("select-many", [paths[0], root / "missing"], good=False)
            assert (directory / "selection.ini").read_bytes() == before
            command("select-many", [paths[0], paths[0]], good=False)
            assert (directory / "selection.ini").read_bytes() == before
            command("select-many", paths[:1], good=False, extra="path0=duplicate\n")
            assert (directory / "selection.ini").read_bytes() == before
            print("PASS Unicode/indexed selection snapshot and all-or-nothing invalid/duplicate rollback", flush=True)
            command("select-many", [])
            assert read("selection.ini")["Selection"]["count"] == "0"
            session.command("select", str(paths[0]))
            assert read("selection.ini")["Selection"]["count"] == "1"
            ys = selection_checks(gui, session, paths, command, read)
            destination = root / "copies"
            destination.mkdir()
            command("copy-many", paths, destination)
            results = read("batch-result.ini")
            assert results["Batch"]["count"] == "3"
            for i, path in enumerate(paths):
                assert (destination / path.name).read_bytes() == path.read_bytes()
                assert results[f"Item{i}"]["status"] == "ok"
            print("PASS serial Unicode batch copy and per-item result manifest", flush=True)
            command("copy-many", paths, destination, good=False)
            results = read("batch-result.ini")
            assert [results[f"Item{i}"]["status"] for i in range(3)] == ["error", "skipped", "skipped"]
            assert all((destination / p.name).read_bytes() == p.read_bytes() for p in paths)
            print("PASS complete preflight rejects collision before any mutation", flush=True)
            moved = root / "moved"
            moved.mkdir()
            copied_paths = [destination / path.name for path in paths]
            command("move-many", copied_paths, moved)
            assert all(not path.exists() and (moved / path.name).exists() for path in copied_paths)
            command("trash-many", [moved / path.name for path in paths])
            assert not list(moved.iterdir())
            assert len(list((xdg / "Trash" / "files").iterdir())) == 3
            print("PASS batch native move and isolated GIO trash preserve per-item outcomes", flush=True)

            tree = root / "tree"
            (tree / "nested").mkdir(parents=True)
            child = tree / "nested" / "child"
            child.write_bytes(b"nested")
            alias = base / "alias"
            alias.symlink_to(tree / "nested", target_is_directory=True)
            clean = root / "preflight-empty"
            clean.mkdir()
            for items in ([tree, child], [tree, alias / "child"], [paths[0], paths[0]], [Path("/")]):
                command("copy-many", items, clean, good=False)
                assert not list(clean.iterdir())
            command("move-many", [paths[0], tree], tree / "nested", good=False)
            assert paths[0].exists() and not (tree / "nested" / paths[0].name).exists()
            print("PASS duplicate/overlap/root/physical-alias/global-move dependencies reject before mutation", flush=True)
            fifo_tree = root / "unsupported-tree"
            fifo_tree.mkdir()
            os.mkfifo(fifo_tree / "fifo")
            command("copy-many", [paths[0], fifo_tree], clean, good=False)
            fifo_result = read("batch-result.ini")
            assert [fifo_result[f"Item{i}"]["status"] for i in range(2)] == ["skipped", "error"]
            assert not list(clean.iterdir())
            before_selection = (directory / "selection.ini").read_bytes()
            session.serial += 1
            malformed = directory / "request.tmp"
            malformed.write_text(f"[Explorer]\nversion=1\nrequest={session.serial}\ncommand=refresh\npath=\\q\n")
            malformed.replace(directory / "request.ini")
            finish(session.serial, good=False)
            assert (directory / "selection.ini").read_bytes() == before_selection
            assert session.state()["directory"] == str(root)
            print("PASS unsupported descendants and unused malformed escapes reject without mutation/state changes", flush=True)
            for invalid in (b"bad-\xff", b"bad\0NUL", b"x" * 1048576):
                session.serial += 1
                header = f"[Explorer]\nversion=1\nrequest={session.serial}\ncommand=refresh\npath=".encode()
                malformed.write_bytes(header + invalid + b"\n")
                malformed.replace(directory / "request.ini")
                finish(session.serial, good=False)
                assert session.state()["directory"] == str(root)
            print("PASS invalid UTF-8/NUL/oversized requests reject through bounded parser", flush=True)

            budget_source = root / "budget-source"
            budget_source.mkdir()
            budget_paths = []
            for i in range(256):
                path = budget_source / f"file-{i}"
                path.touch()
                budget_paths.append(path)
            long_dir = base / "long"
            long_dir.mkdir()
            for i in range(15):
                long_dir = long_dir / (str(i) + "x" * 198)
                long_dir.mkdir()
            response = command("copy-many", budget_paths, long_dir, good=False)
            assert "result" in response["message"].lower() and "1 MiB" in response["message"]
            assert not list(long_dir.iterdir())
            print("PASS valid small request with expanded result budget rejects before copying", flush=True)

            result_path = directory / "batch-result.ini"
            result_path.unlink()
            result_path.mkdir()
            persistence = root / "persistence"
            persistence.mkdir()
            response = command("copy-many", paths[:1], persistence, good=False)
            assert (persistence / paths[0].name).read_bytes() == paths[0].read_bytes()
            assert "persist" in response["message"].lower() and "1" in response["message"]
            result_path.rmdir()
            print("PASS result publication failure reports actual completed count without false success", flush=True)

            large = root / "large"
            large.mkdir()
            for i in range(10000):
                (large / f"item-{i}").write_bytes(b"bounded cancellation fixture")
            partial = root / "partial"
            partial.mkdir()
            serial = begin("copy-many", [paths[0], large, paths[2]], partial)
            wait(lambda: (partial / large.name).exists() and any((partial / large.name).iterdir()), "actual second-item copy")
            temporary = directory / "cancel.tmp"
            temporary.write_text(f"[Explorer]\nversion=1\nrequest={serial}\n")
            temporary.replace(directory / "cancel.ini")
            finish(serial, good=False)
            result = read("batch-result.ini")
            assert [result[f"Item{i}"]["status"] for i in range(3)] == ["ok", "error", "skipped"]
            assert result["Item1"]["message"].startswith("Partial new destination")
            assert result["Item1"]["destination"] == str(partial / large.name)
            assert len(list(large.iterdir())) == 10000 and not (partial / paths[2].name).exists()
            session.command("refresh")
            print("PASS real mid-batch cancellation keeps completed/error/skipped results and source/recovery", flush=True)
            gui_cancel = root / "gui-cancel"
            gui_cancel.mkdir()
            command("select-many", [paths[0], large, paths[2]])
            durable = (directory / "batch-result.ini").read_bytes()
            session.ui().click("More", "button"); session.ui().click("Copy to…", "button")
            session.ui().find("Destination path", "text")
            gui.xdo("key", "ctrl+a")
            gui.xdo("type", "--clearmodifiers", "--", "gui-cancel")
            gui.xdo("key", "Return")
            wait(lambda: (gui_cancel / large.name).exists() and any((gui_cancel / large.name).iterdir()), "GUI batch partial copy")
            session.ui().click("Cancel", "button")
            wait(lambda: session.state().get("status") == "error" and "partial new destination" in session.state().get("message", ""), "GUI batch partial warning")
            assert str(gui_cancel / large.name) in session.state()["message"]
            assert (directory / "batch-result.ini").read_bytes() == durable
            session.command("refresh")
            print("PASS cancelled GUI batch reports exact possible partial path without replacing protocol results", flush=True)
            gui_batch_trash(gui, session, root, paths, command, read)
            selection_limits(gui, session, root, read)
            multi_dnd(gui, session, root, paths, ys, command, artifacts)
        finally:
            session.stop()
            shutil.copyfile(directory / "host.log", artifacts / "host.log")
            if old_xdg is None:
                os.environ.pop("XDG_DATA_HOME", None)
            else:
                os.environ["XDG_DATA_HOME"] = old_xdg


def selection_checks(gui, session, paths, command, read):
    window = gui.xdo("search", "--sync", "--name", "^Sumi Explorer$").splitlines()[-1]
    gui.xdo("windowsize", window, "1000", "700")
    gui.xdo("windowmove", window, "0", "0")
    gui.xdo("windowfocus", window)
    ys = {}
    for path in paths:
        command("select-many", [])
        time.sleep(0.3)
        point = session.ui().row_point(path)
        gui.xdo("mousemove", *map(str, point)); gui.xdo("click", "1")
        wait(lambda: read("selection.ini")["Selection"]["count"] == "1", "pointer row selection")
        selected = read("selection.ini")["Selection"]
        if selected["count"] == "1" and selected["path0"] == str(path):
            ys[path] = point
        assert path in ys, path
    ordered = sorted(paths, key=lambda path: ys[path][1])
    command("select-many", [])
    time.sleep(0.3)
    gui.xdo("mousemove", *map(str, ys[ordered[0]]))
    gui.xdo("click", "1")
    gui.xdo("keydown", "ctrl")
    try:
        gui.xdo("mousemove", *map(str, ys[ordered[1]]))
        gui.xdo("click", "1")
    finally:
        gui.xdo("keyup", "ctrl")
    wait(lambda: read("selection.ini")["Selection"]["count"] == "2", "real Ctrl selection")
    gui.xdo("click", "3")
    time.sleep(0.15)
    assert read("selection.ini")["Selection"]["count"] == "2"
    gui.xdo("key", "Escape")
    command("select-many", [])
    time.sleep(0.3)
    gui.xdo("mousemove", *map(str, ys[ordered[0]]))
    gui.xdo("click", "1")
    gui.xdo("keydown", "shift")
    try:
        gui.xdo("mousemove", *map(str, ys[ordered[-1]]))
        gui.xdo("click", "1")
    finally:
        gui.xdo("keyup", "shift")
    wait(lambda: read("selection.ini")["Selection"]["count"] == "3", "real Shift range")
    print("PASS real Ctrl/Shift multiple selection, right-click membership preservation and empty clear", flush=True)
    return ys


def gui_batch_trash(gui, session, root, paths, command, read):
    a, b, c = [root / name for name in ("gui-a", "gui-b", "gui-c")]
    for path in (a, b, c):
        path.write_bytes(b"GUI frozen identity")
    session.command("refresh")
    command("select-many", [a, b])
    durable = (session.directory / "batch-result.ini").read_bytes()
    session.ui().click("Trash", "button")
    confirm = gui.xdo("search", "--sync", "--name", "^Confirm Trash$").splitlines()[-1]
    assert a.exists() and b.exists()
    command("select-many", [c])
    gui.xdo("windowfocus", confirm)
    session.ui().click("Trash", "button")
    wait(lambda: not a.exists() and not b.exists(), "frozen GUI batch Trash")
    assert c.exists()
    assert (session.directory / "batch-result.ini").read_bytes() == durable
    print("PASS confirmed GUI batch Trash uses frozen paths and cannot overwrite protocol result", flush=True)


def selection_limits(gui, session, root, read):
    limit = root / "selection-limit"
    limit.mkdir()
    for i in range(257):
        (limit / f"item-{i:03d}").touch()
    session.command("open", str(limit))
    session.command("select", str(limit / "item-000"))
    window = gui.xdo("search", "--name", "^Sumi Explorer$").splitlines()[-1]
    gui.xdo("windowfocus", window)
    gui.xdo("key", "ctrl+a")
    wait(lambda: read("selection.ini")["Selection"]["status"] == "error", "257-row compact error")
    snapshot = read("selection.ini")["Selection"]
    assert snapshot["count"] == "0" and not any(key.startswith("path") for key in snapshot)
    bad = root / "invalid-byte-name"
    bad.mkdir()
    descriptor = os.open(os.fsencode(bad) + b"/bad-\xff", os.O_WRONLY | os.O_CREAT, 0o600)
    os.close(descriptor)
    session.command("open", str(bad))
    gui.xdo("mousemove", *map(str, session.ui().point(role="list item")))
    gui.xdo("click", "1")
    wait(lambda: read("selection.ini")["Selection"]["status"] == "error", "invalid filename compact error")
    assert read("selection.ini")["Selection"]["count"] == "0"
    session.command("open", str(root))
    print("PASS 257-row and invalid-byte filename selections publish compact errors instead of truncation", flush=True)


def multi_dnd(gui, session, root, paths, ys, command, artifacts):
    if not shutil.which("nautilus") or not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        raise RuntimeError("UNAVAILABLE: Nautilus in isolated dbus-run-session is required")
    target = root.parent / "external-receiver"
    target.mkdir()
    log = (artifacts / "nautilus.log").open("w")
    receiver = subprocess.Popen(["nautilus", "--new-window", str(target)], stdout=log, stderr=log)
    try:
        window = gui.xdo("search", "--sync", "--onlyvisible", "--class", "org.gnome.Nautilus").splitlines()[-1]
        gui.xdo("windowsize", window, "650", "500")
        gui.xdo("windowmove", window, "1100", "0")
        sender = gui.xdo("search", "--name", "^Sumi Explorer$").splitlines()[-1]
        gui.xdo("windowsize", sender, "1000", "700")
        gui.xdo("windowmove", sender, "0", "0")
        command("select-many", paths[:2])
        gui.xdo("windowraise", sender)
        start = session.ui().row_point(paths[0])
        finish = (1400, 220)
        gui.xdo("mousemove", *map(str, start))
        gui.xdo("mousedown", "1")
        try:
            for part in range(1, 9):
                gui.xdo("mousemove", *(str(round(a+(b-a)*part/8)) for a,b in zip(start, finish)))
                time.sleep(0.13)
            time.sleep(0.3)
        finally:
            gui.xdo("mouseup", "1")
        wait(lambda: all((target / path.name).exists() for path in paths[:2]), "real multiple outbound DND without Ctrl")
        assert all((target / path.name).read_bytes() == path.read_bytes() for path in paths[:2])
        assert all(path.exists() for path in paths[:2])
        if shutil.which("import"):
            subprocess.run(["import", "-window", "root", str(artifacts / "multiple-dnd.png")], check=True, timeout=8)
        print("PASS real outgoing multi-file DND to Nautilus with no drag modifier and retained sources", flush=True)
    finally:
        receiver.terminate()
        try:
            receiver.wait(timeout=5)
        except subprocess.TimeoutExpired:
            receiver.kill()
            receiver.wait(timeout=3)
        log.close()


if __name__ == "__main__":
    resource.setrlimit(resource.RLIMIT_AS, (8 * 1024**3, 8 * 1024**3))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path)
    run(parser.parse_args())
