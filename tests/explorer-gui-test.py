#!/usr/bin/env python3
"""Exercise the compiled Sumi explorer and its data-only local protocol."""

from __future__ import annotations

import argparse
import configparser
import os
from pathlib import Path
import re
import resource
import subprocess
import tempfile
import time


def escape(value: str) -> str:
    return "".join({"\\": "\\\\", "\n": "\\n", "\r": "\\r", "\t": "\\t", " ": "\\s"}.get(c, c) for c in value)


def unescape(value: str) -> str:
    result = []
    index = 0
    codes = {"\\": "\\", "n": "\n", "r": "\r", "t": "\t", "s": " "}
    while index < len(value):
        char = value[index]
        if char == "\\":
            index += 1
            if index == len(value) or value[index] not in codes:
                raise AssertionError("Invalid key-file escape")
            char = codes[value[index]]
        result.append(char)
        index += 1
    return "".join(result)


class Session:
    def __init__(self, binary: Path, root: Path, session: Path):
        self.directory = session
        self.serial = 0
        self.log = (session / "host.log").open("wb")
        environment = dict(os.environ, GTK_A11Y="atspi", SUMI_EXPLORER_ROOT=str(root), SUMI_EXPLORER_SESSION=str(session))
        environment.setdefault("SUMI_EXPLORER_UNDO_DIR", str(session / "undo-profile"))
        # A previous GUI inspection may have pinned another temporary bus.
        # GTK must discover the provider belonging to this child's session.
        environment.pop("AT_SPI_BUS_ADDRESS", None)
        self.process = subprocess.Popen(
            [str(binary)],
            env=environment,
            stdout=self.log,
            stderr=subprocess.STDOUT,
        )
        try:
            self.wait(lambda state: state["request"] == "0")
            # Initial state can precede X11 mapping. Return only after this
            # process owns its visible main window, including under load.
            xdo("search", "--sync", "--all", "--onlyvisible", "--pid", str(self.process.pid), "--name", "^Sumi Explorer$")
        except BaseException:
            self.stop()
            raise

    def state(self, response: bool = False) -> dict[str, str]:
        path = self.directory / ("response.ini" if response else "state.ini")
        if not path.exists():
            return {}
        raw = path.read_bytes()
        assert len(raw) <= 1048576 and b"\0" not in raw, "Invalid state size/NUL"
        parser = configparser.RawConfigParser(strict=True, empty_lines_in_values=False)
        parser.read_string(raw.decode("utf-8"))
        assert parser.sections() == ["Explorer"], "Unexpected state groups"
        values = {key: unescape(value) for key, value in parser["Explorer"].items()}
        assert set(values) == {"version", "request", "status", "directory", "selected", "message"}
        assert values["version"] == "1" and re.fullmatch(r"[0-9]+", values["request"])
        assert values["status"] in {"ok", "error"}
        return values

    def wait(self, predicate, seconds: float = 8, response: bool = False) -> dict[str, str]:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            state = self.state(response)
            if state and predicate(state):
                return state
            if self.process.poll() is not None:
                raise AssertionError(f"Host exited {self.process.returncode}; see {self.directory / 'host.log'}")
            time.sleep(0.03)
        raise AssertionError(f"Timed out; last state={self.state(response)!r}")

    def command(self, command: str, path: str = "", version: int = 1) -> dict[str, str]:
        assert not (self.directory / "request.ini").exists(), "Outstanding request was not consumed"
        self.serial += 1
        text = f"[Explorer]\nversion={version}\nrequest={self.serial}\ncommand={escape(command)}\npath={escape(path)}\n"
        temporary = self.directory / "request.tmp"
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(self.directory / "request.ini")
        return self.wait(lambda state: state["request"] == str(self.serial), response=True)

    def stop(self):
        if self.process.poll() is None:
            try:
                self.command("quit")
                self.process.wait(timeout=3)
            except Exception:
                self.process.terminate()
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=3)
        self.log.close()

    def ui(self):
        """Return PID-scoped accessibility geometry for real pointer interaction."""
        return AccessibleUI(self.process.pid)


class AccessibleUI:
    """Discover GTK geometry; activation always uses actual mouse/keyboard input."""

    def __init__(self, pid):
        import gi
        from gi.repository import GLib, Gio
        address = os.environ.get("DBUS_SESSION_BUS_ADDRESS")
        if not address:
            raise AssertionError("Accessibility inspection requires an isolated D-Bus session")
        # Resolve before importing Atspi: loading its library may establish the
        # global connection before the explicit init() call below.
        connection = Gio.DBusConnection.new_for_address_sync(
            address, Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
            None, None)
        try:
            reply = connection.call_sync("org.a11y.Bus", "/org/a11y/bus", "org.a11y.Bus", "GetAddress",
                None, GLib.VariantType.new("(s)"), Gio.DBusCallFlags.NONE, 5000, None)
            a11y_address = reply.unpack()[0]
        finally:
            connection.close_sync(None)
        os.environ["AT_SPI_BUS_ADDRESS"] = a11y_address
        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi
        self.api, self.context, self.pid = Atspi, GLib.MainContext.default(), pid
        # The harness is dynamically imported under several module names, but
        # libatspi owns one process-global connection. Keep its ownership on the
        # shared GI module rather than on this repeatedly reloaded Python class.
        if hasattr(Atspi, "_sumi_bus_address") and Atspi._sumi_bus_address != address:
            # libatspi in this image cannot safely reinitialize its desktop
            # caches after exit(). Refuse before querying a dead connection.
            raise AssertionError("Accessibility client bus changed; run the GUI phase in a fresh Python process")
        if not hasattr(Atspi, "_sumi_bus_address"):
            # init() is lazy: keep this address pinned through get_desktop()
            # and subsequent calls, rather than restoring the stale X11 path.
            Atspi.init()
            Atspi._sumi_bus_address = address

    def nodes(self):
        # Deliver AT-SPI lifecycle events so destroyed dialogs and old names
        # cannot remain in libatspi's client-side cache between interactions.
        while self.context.pending():
            self.context.iteration(False)
        desktop = self.api.get_desktop(0)
        applications = [desktop.get_child_at_index(i) for i in range(desktop.get_child_count())]
        stack = [app for app in applications if app.get_process_id() == self.pid]
        result = []
        while stack:
            node = stack.pop()
            node.clear_cache()
            result.append(node)
            if len(result) > 4096:
                raise AssertionError("Accessibility tree exceeded bounded discovery")
            stack.extend(node.get_child_at_index(i) for i in range(node.get_child_count()))
        return result

    def rect(self, node):
        # GTK 4.18 reports (0,0) for SCREEN coordinates under Xvfb. WINDOW
        # coordinates are accurate; add the actual PID-scoped X11 origin.
        bounds = node.get_component_iface().get_extents(self.api.CoordType.WINDOW)
        frame = node
        while frame.get_role_name() != "frame" and frame.get_parent() is not None:
            frame = frame.get_parent()
        if frame.get_role_name() != "frame":
            raise AssertionError(f"No owning window for {node.get_name()!r}")
        windows = xdo("search", "--all", "--onlyvisible", "--pid", str(self.pid), "--name", "^" + re.escape(frame.get_name()) + "$").splitlines()
        geometries = [dict(line.split("=", 1) for line in xdo("getwindowgeometry", "--shell", window).splitlines()) for window in windows]
        origin = max(geometries, key=lambda g: int(g["WIDTH"])*int(g["HEIGHT"]))
        return bounds.x+int(origin["X"]), bounds.y+int(origin["Y"]), bounds.width, bounds.height

    def find(self, name=None, role=None, visible=True, seconds=5):
        end = time.monotonic() + seconds
        while True:
            for node in self.nodes():
                if ((name is None or node.get_name() == name)
                        and (role is None or node.get_role_name() == role)):
                    if not visible or node.get_state_set().contains(self.api.StateType.SHOWING):
                        return node
            if time.monotonic() >= end:
                raise AssertionError(f"No accessible {name!r} role={role!r} for PID {self.pid}")
            time.sleep(0.05)

    def point(self, name=None, role=None):
        node = self.find(name, role)
        x, y, width, height = self.rect(node)
        if width <= 0 or height <= 0:
            raise AssertionError(f"Invisible geometry: {name!r}")
        # GTK's accessible allocation omits CSS content insets. The lower
        # interior remains inside the actual button under the supported theme,
        # including the dialog's 18px outer margin (a 34px button is typical).
        return x + width // 2, y + (height * 3 // 4 if node.get_role_name() == "button" else height // 2)

    def click(self, name=None, role=None, button=1):
        xdo("mousemove", *map(str, self.point(name, role)))
        xdo("click", str(button))

    def row_point(self, path):
        """Find a row through its filename label, then use the row's geometry."""
        node = self.find(Path(path).name)
        while node.get_parent() is not None:
            if node.get_role_name() in {"list item", "table row"}:
                break
            node = node.get_parent()
        x, y, width, height = self.rect(node)
        if width <= 0 or height <= 0:
            raise AssertionError(f"File row is not visible: {path}")
        return x + min(width // 2, 80), y + height // 2

    def row_click(self, path, button=1):
        xdo("mousemove", *map(str, self.row_point(path)))
        xdo("click", str(button))


def xdo(*arguments: str) -> str:
    return subprocess.check_output(["xdotool", *arguments], text=True, timeout=8).strip()


def run(binary: Path, group: str):
    with tempfile.TemporaryDirectory(prefix="sumi-explorer-check-") as temporary:
        base = Path(temporary)
        root = base / "日本語 folder"
        root.mkdir()
        child = root / "subfolder"
        child.mkdir()
        paths = [root / "日本語.txt", root / "line\nbreak.txt", root / "spaces and 'quotes';$(x).txt"]
        for path in paths:
            path.write_text("fixture\n", encoding="utf-8")
        session_dir = base / "session"
        session_dir.mkdir(mode=0o700)
        session = Session(binary, root, session_dir)
        try:
            assert session.state()["directory"] == str(root)
            for path in paths:
                state = session.command("select", str(path))
                assert state["status"] == "ok" and state["selected"] == str(path), state
            assert session.command("open", str(child))["directory"] == str(child)
            assert session.command("up")["directory"] == str(root)
            if group in {"all", "errors"}:
                for command, path, version in [
                    ("open", str(root / "missing"), 1),
                    ("select", str(root / "missing.txt"), 1),
                    ("eval", '(write-region "bad" nil "sentinel")', 1),
                    ("copy", str(paths[0]), 1),
                    ("refresh", "", 99),
                ]:
                    state = session.command(command, path, version)
                    assert state["status"] == "error" and state["directory"] == str(root), state
                assert not (root / "sentinel").exists()
                assert session.command("refresh")["status"] == "ok"
            if group in {"all", "widgets"}:
                window = xdo("search", "--sync", "--name", "^Sumi Explorer$").splitlines()[-1]
                session.command("select", str(child))
                xdo("windowfocus", "--sync", window)
                xdo("key", "Return")
                session.wait(lambda state: state["directory"] == str(child))
                session.command("up")
                session.command("select", str(paths[0]))
                state = session.command("properties")
                assert state["status"] == "ok", state
                assert xdo("search", "--sync", "--name", "^Sumi Properties$")
            print(f"PASS {group}: real native process, Unicode paths, selection, navigation" +
                  (", rejection controls" if group in {"all", "errors"} else "") +
                  (", widget activation and Properties" if group in {"all", "widgets"} else ""))
        finally:
            session.stop()
        assert session.process.returncode == 0, f"Host exit: {session.process.returncode}"


if __name__ == "__main__":
    resource.setrlimit(resource.RLIMIT_AS, (8 * 1024**3, 8 * 1024**3))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=Path(__file__).resolve().parents[1] / "target/sumi-explorer")
    parser.add_argument("--group", choices=["all", "protocol", "errors", "widgets"], default="all")
    arguments = parser.parse_args()
    if not arguments.binary.is_file():
        parser.error(f"Missing compiled explorer: {arguments.binary}")
    run(arguments.binary.resolve(), arguments.group)
