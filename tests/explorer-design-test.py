#!/usr/bin/env python3
"""Real pointer/keyboard design acceptance and source-bound evidence verification.

Run under xvfb-run -a -s '-screen 0 1920x1080x24' dbus-run-session.
Verification validates a recorded run; it never claims to re-execute the GUI.
"""

from __future__ import annotations

import argparse
import ast
import configparser
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import resource
import re
import subprocess
import tempfile
import time
import traceback


ROOT = Path(__file__).resolve().parents[1]
GROUPS = {
    "layout": ["hierarchy", "sidebar", "no-permanent-destination"],
    "actions": ["common-controls", "sort-menu", "view-menu", "more-menu", "context-menu"],
    "viewports": ["1000x700", "1280x800", "1600x900"],
    "navigation": ["address", "back-forward-up", "filter", "tabs"],
    "operations": ["new-folder-cancel", "rename-cancel", "copy-cancel", "move-cancel", "zip-cancel", "permissions-cancel", "properties", "invalid-input-retained", "multi-trash-cancel"],
    "workflow": ["create", "rename", "copy-paste", "undo"],
}
BASELINE_ASSERTIONS = {
    "tests/explorer-advanced-test.py": 214, "tests/explorer-batches-test.py": 41,
    "tests/explorer-dnd-test.py": 19, "tests/explorer-everyday-test.py": 57,
    "tests/explorer-gui-test.py": 16, "tests/explorer-operations-test.py": 29,
    "tests/explorer-workspace-test.py": 44,
}
COMMON = ["New folder", "Cut", "Copy", "Paste", "Rename", "Trash", "Undo", "Sort", "View", "More"]
NAV = ["Back", "Forward", "Up", "Refresh", "Edit location", "Filter current folder"]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sources():
    paths = sorted(set(ROOT.glob("explorer/*")) | set(ROOT.glob("tests/*.py")))
    return {str(path.relative_to(ROOT)): digest(path) for path in paths if path.is_file()}


def assertion_counts():
    return {str(path.relative_to(ROOT)): sum(isinstance(node, ast.Assert) for node in ast.walk(ast.parse(path.read_text())))
            for path in sorted(ROOT.glob("tests/*.py"))}


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def verify(path, group="all"):
    receipt = json.loads(Path(path).read_text(), object_pairs_hook=unique_object)
    require(set(receipt) == {"schema", "kind", "sources", "assertions", "binary", "groups", "artifacts"}, "Receipt fields differ")
    require(type(receipt["schema"]) is int and receipt["schema"] == 1 and receipt["kind"] == "sumi-explorer-design-gui", "Receipt schema/kind differs")
    require(receipt["sources"] == sources(), "Recorded source hashes differ from current checkout")
    counts = assertion_counts()
    require(receipt["assertions"] == counts, "Recorded assertion counts differ")
    require(all(counts.get(name, 0) >= count for name, count in BASELINE_ASSERTIONS.items()), "Retained assertions were removed")
    require(set(receipt["groups"]) == set(GROUPS), "Missing/invalid groups")
    for name, checks in GROUPS.items():
        record = receipt["groups"][name]
        require(set(record) == {"status", "checks", "error"}, f"Invalid group fields: {name}")
        require(record["status"] in {"pass", "fail", "not-run"}, f"Invalid group status: {name}")
        require(type(record["checks"]) is list and all(type(item) is str for item in record["checks"]), "Invalid check list")
        require(len(record["checks"]) == len(set(record["checks"])) and set(record["checks"]).issubset(checks), "Duplicate/invalid checks")
        require(type(record["error"]) is str, "Invalid error field")
        if record["status"] == "pass":
            require(record["checks"] == checks and not record["error"], f"Incomplete passing group: {name}")
    require(group == "all" or group in GROUPS, "Unknown requested group")
    for name in GROUPS if group == "all" else [group]:
        require(receipt["groups"][name]["status"] == "pass", f"Recorded group did not pass: {name}")
    binary = receipt["binary"]
    require(set(binary) == {"path", "sha256"} and digest(binary["path"]) == binary["sha256"], "Binary differs/missing")
    artifacts = receipt["artifacts"]
    require(type(artifacts) is list and len(artifacts) >= 2, "Missing runtime artifacts")
    paths = []
    for artifact in artifacts:
        require(set(artifact) == {"path", "sha256", "kind"}, "Invalid artifact fields")
        require(artifact["kind"] in {"log", "screenshot"}, "Invalid artifact kind")
        require(digest(artifact["path"]) == artifact["sha256"], "Artifact differs/missing")
        paths.append(artifact["path"])
    require(len(paths) == len(set(paths)) and {item["kind"] for item in artifacts} == {"log", "screenshot"}, "Duplicate/missing artifacts")
    print(f"PASS recorded evidence verification: {group} (no GUI re-execution)")
    return receipt


def negative_controls(path):
    """Exercise the verifier with deliberately invalid receipts, without a GUI."""
    original = json.loads(Path(path).read_text())
    cases = []
    for mutate in [
        lambda r: r["groups"].pop("layout"),
        lambda r: r["groups"].update(unknown=r["groups"]["layout"]),
        lambda r: r["groups"]["layout"].update(status="pass", checks=[]),
        lambda r: r["groups"]["layout"].update(status="skip"),
        lambda r: r["sources"].update({"explorer/sumi-explorer-native.el": "0" * 64}),
        lambda r: r["assertions"].update({"tests/explorer-gui-test.py": 0}),
        lambda r: r["binary"].update(sha256="0" * 64),
        lambda r: r["artifacts"][0].update(sha256="0" * 64),
        lambda r: r.update(extra=True),
    ]:
        receipt = json.loads(json.dumps(original)); mutate(receipt)
        cases.append(json.dumps(receipt))
    cases.append(Path(path).read_text().replace('"schema": 1', '"schema": 1, "schema": 1', 1))
    with tempfile.TemporaryDirectory(prefix="sumi-design-verifier-") as temporary:
        bad = Path(temporary) / "bad.json"
        for index, value in enumerate(cases):
            bad.write_text(value)
            try:
                verify(bad)
            except (AssertionError, ValueError, KeyError, TypeError, OSError):
                continue
            raise AssertionError(f"Negative control {index} was accepted")
    print(f"PASS {len(cases)} corrupted receipt negative controls")


class DesignRun:
    def __init__(self, binary, artifacts, gui):
        self.binary, self.artifacts, self.gui = binary, artifacts, gui
        self.completed = []

    def check(self, name, condition=True):
        require(condition, name)
        self.completed.append(name)

    def wait(self, predicate, description, seconds=10):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if predicate():
                return
            time.sleep(0.04)
        raise AssertionError(description)

    def key(self, *keys):
        self.gui.xdo("key", "--clearmodifiers", *keys)

    def text(self, value):
        self.key("ctrl+a"); self.gui.xdo("type", "--clearmodifiers", "--", str(value))

    def read(self, name):
        parser = configparser.RawConfigParser(); parser.read(self.session.directory / name)
        return parser

    def address(self, path):
        self.key("ctrl+l")
        self.ui.click("Location", "text"); self.text(path); self.key("Return")
        self.wait(lambda: self.session.state()["directory"] == str(path), "Address navigation")

    def screenshot(self, name):
        path = self.artifacts / f"{name}.png"
        subprocess.run(["import", "-window", self.window, str(path)], check=True, timeout=10)

    def menu(self, name):
        self.ui.click(name, "button"); time.sleep(0.12)
        subprocess.run(["import", "-window", "root", str(self.artifacts / f"{self.group}-menu-{name.lower()}.png")], check=True, timeout=10)

    def dialog(self, title, primary):
        node = self.ui.find(title, "frame")
        # Dialog entries must be on screen; confirmations and cancellation fit.
        self.ui.find(primary, "button"); self.ui.find("Cancel", "button")
        self.within(node, self.ui.find(primary, "button")); self.within(node, self.ui.find("Cancel", "button"))
        window = self.gui.xdo("search", "--all", "--onlyvisible", "--pid", str(self.session.process.pid), "--name", "^" + re.escape(title) + "$").splitlines()[-1]
        subprocess.run(["import", "-window", window, str(self.artifacts / (self.group + "-dialog-" + re.sub(r"[^a-z0-9]+", "-", title.lower()) + ".png"))], check=True, timeout=10)
        return node

    def within(self, outer, inner):
        x, y, w, h = self.ui.rect(outer); a, b, c, d = self.ui.rect(inner)
        require(w > 0 and h > 0 and c > 0 and d > 0 and a >= x and b >= y and a+c <= x+w and b+d <= y+h,
                f"Clipped {inner.get_name()!r}: {(a,b,c,d)} outside {(x,y,w,h)}")

    def cancel(self, title, primary, escape=True):
        self.dialog(title, primary)
        if escape:
            self.key("Escape")
        else:
            self.ui.click("Cancel", "button")
        self.wait(lambda: all(n.get_name() != title for n in self.ui.nodes()), f"Dialog did not close: {title}")

    def run_group(self, group):
        self.group = group
        self.completed = []
        with tempfile.TemporaryDirectory(prefix=f"sumi-design-{group}-") as temporary:
            base = Path(temporary)
            self.root = base / (("Long folder " + "label "*24).rstrip() if group == "viewports" else "fixture")
            self.root.mkdir()
            self.child = self.root / "child"; self.child.mkdir()
            self.source = self.root / "alpha.txt"; self.source.write_bytes(b"GUI workflow bytes\n")
            (self.root / "beta.txt").write_bytes(b"second file\n")
            if group == "viewports":
                (self.root / ("long filename " + "word "*35 + ".txt")).write_bytes(b"long row label")
            session_path = base / "session"; session_path.mkdir(mode=0o700)
            previous = {key: os.environ.get(key) for key in ("XDG_CONFIG_HOME", "XDG_DATA_HOME")}
            os.environ.update(XDG_CONFIG_HOME=str(base / "config"), XDG_DATA_HOME=str(base / "data"))
            self.session = self.gui.Session(self.binary, self.root, session_path)
            self.ui = self.session.ui()
            try:
                self.window = self.gui.xdo("search", "--all", "--sync", "--onlyvisible", "--pid", str(self.session.process.pid), "--name", "^Sumi Explorer$").splitlines()[-1]
                self.gui.xdo("windowsize", self.window, "1280", "800")
                self.gui.xdo("windowfocus", "--sync", self.window)
                getattr(self, group)()
                self.screenshot(group)
            finally:
                # A failure screenshot is as valuable as a passing one.
                if hasattr(self, "window"):
                    try: self.screenshot(group + "-final")
                    except Exception: pass
                self.session.stop()
                (self.artifacts / f"{group}-host.log").write_bytes((session_path / "host.log").read_bytes())
                for key, value in previous.items():
                    if value is None: os.environ.pop(key, None)
                    else: os.environ[key] = value
        require(self.completed == GROUPS[group], f"Missing/out-of-order checks: {group}")

    def layout(self):
        frame = self.ui.find("Sumi Explorer", "frame")
        nav = [self.ui.find(name) for name in NAV]
        commands = [self.ui.find(name, "button") for name in COMMON]
        for node in nav + commands: self.within(frame, node)
        tab = self.ui.rect(self.ui.find("New tab", "button"))
        nav_rects = [self.ui.rect(node) for node in nav]
        command_rects = [self.ui.rect(node) for node in commands]
        require(max(r[1] for r in nav_rects) < min(r[1] for r in command_rects), "Navigation must precede command bar")
        require(tab[1] < min(r[1] for r in nav_rects), "Tab strip must precede navigation")
        require(max(r[1] for r in nav_rects)-min(r[1] for r in nav_rects) < 12, "Navigation is split across rows")
        self.check("hierarchy")
        home = self.ui.rect(self.ui.find("Home", "button")); row = self.ui.row_point(self.source)
        require(home[0]+home[2] < row[0] and home[1] > command_rects[0][1], "Home must occupy the left navigation pane")
        self.check("sidebar")
        texts = [n for n in self.ui.nodes() if n.get_role_name() == "text" and n.get_state_set().contains(self.ui.api.StateType.SHOWING)]
        require(len(texts) == 1 and {n.get_name() for n in texts} == {"Filter current folder"}, "Permanent destination/extra editor remains")
        self.check("no-permanent-destination")

    def actions(self):
        for name in COMMON + NAV + ["New tab", "Home"]: self.ui.find(name)
        self.check("common-controls")
        self.menu("Sort")
        require(any("Reverse" in n.get_name() and n.get_role_name() == "button" for n in self.ui.nodes()), "Sort direction control missing")
        self.ui.find(role="combo box")
        self.key("Escape"); self.check("sort-menu")
        self.menu("View"); self.ui.find("Hidden", "check box"); self.ui.find("Details", "check box")
        # GtkAccessible's grab_focus is unsupported here; use the actual
        # popover's keyboard focus chain and inspect its exposed focus state.
        for _ in range(12):
            if self.ui.find("Details", "check box").get_state_set().contains(self.ui.api.StateType.FOCUSED):
                break
            self.key("Tab")
        require(self.ui.find("Details", "check box").get_state_set().contains(self.ui.api.StateType.FOCUSED), "View Details not keyboard reachable")
        self.key("space")
        self.wait(lambda: self.read("view.ini")["View"]["view"] == "details", "View menu changes actual presentation")
        require(self.ui.find("Details", "check box").get_state_set().contains(self.ui.api.StateType.CHECKED), "View checked state is not exposed")
        self.key("Escape"); self.check("view-menu")
        self.menu("More")
        for name in ["Copy to…", "Move to…", "Properties", "Create ZIP", "Extract ZIP", "Permissions", "New window", "Close tab"]:
            self.ui.find(name, "button")
        self.key("Escape"); self.check("more-menu")
        self.ui.row_click(self.source, 3)
        self.wait(lambda: self.session.state()["selected"] == str(self.source), "Right-click selection")
        for name in ["Properties", "Create ZIP", "Extract ZIP", "Permissions", "Undo"]: self.ui.find(name, "button")
        self.key("Escape"); self.check("context-menu")

    def viewports(self):
        self.menu("More"); self.ui.click("+ Bookmark", "button")
        self.wait(lambda: self.read("bookmarks.ini").has_section("Bookmark0"), "GUI bookmark creation")
        for width, height in [(1000,700), (1280,800), (1600,900)]:
            self.gui.xdo("windowsize", self.window, str(width), str(height)); time.sleep(0.2)
            frame = self.ui.find("Sumi Explorer", "frame")
            for name in COMMON+NAV+["Home", "New tab"]: self.within(frame, self.ui.find(name))
            self.within(frame, self.ui.find(self.root.name, "button"))
            list_node = self.ui.find(role="list")
            self.within(frame, list_node)
            x,y,w,h = self.ui.rect(list_node)
            require(w >= width*0.55 and h >= height*0.5, f"Insufficient file area: {w}x{h}")
            self.ui.click("New folder", "button"); self.cancel("Sumi New Folder", "Apply")
            self.screenshot(f"viewport-{width}x{height}"); self.check(f"{width}x{height}")

    def navigation(self):
        self.address(self.child); self.check("address")
        self.ui.click("Back", "button"); self.wait(lambda: self.session.state()["directory"] == str(self.root), "Back")
        self.ui.click("Forward", "button"); self.wait(lambda: self.session.state()["directory"] == str(self.child), "Forward")
        self.ui.click("Up", "button"); self.wait(lambda: self.session.state()["directory"] == str(self.root), "Up")
        self.check("back-forward-up")
        self.ui.click("Filter current folder", "text"); self.text("alpha"); self.key("Return")
        self.wait(lambda: self.read("view.ini")["View"]["filter"] == "alpha", "Filter Enter")
        self.ui.find(self.source.name)
        require(not any(n.get_name() == "beta.txt" for n in self.ui.nodes()), "Filter did not hide unrelated row")
        self.text(""); self.key("Return"); self.check("filter")
        self.ui.click("New tab", "button")
        self.wait(lambda: self.read("workspace.ini")["Workspace"]["count"] == "2", "New tab")
        self.address(self.child)
        self.ui.find(self.child.name + " (active tab)", "button")
        self.ui.find(self.root.name, "button")
        self.menu("More"); self.ui.click("Close tab", "button")
        self.wait(lambda: self.read("workspace.ini")["Workspace"]["count"] == "1" and self.session.state()["directory"] == str(self.root), "Close tab restores previous folder")
        self.check("tabs")

    def operations(self):
        before = sorted(p.name for p in self.root.iterdir())
        self.ui.click("New folder", "button"); self.cancel("Sumi New Folder", "Apply"); self.check("new-folder-cancel")
        self.ui.row_click(self.source)
        self.ui.click("Rename", "button"); self.cancel("Sumi Rename", "Apply", False); self.check("rename-cancel")
        for action,title,primary,check in [
            ("Copy to…", "Copy to", "Copy", "copy-cancel"), ("Move to…", "Move to", "Move", "move-cancel"),
            ("Create ZIP", "Sumi Create ZIP", "Apply", "zip-cancel"), ("Permissions", "Sumi Permissions", "Apply", "permissions-cancel")]:
            self.menu("More"); self.ui.click(action, "button"); self.cancel(title, primary); self.check(check)
        self.ui.row_click(self.source, 3); self.ui.click("Properties", "button")
        self.ui.find("Sumi Properties", "frame"); self.key("Escape")
        require(sorted(p.name for p in self.root.iterdir()) == before and self.source.read_bytes() == b"GUI workflow bytes\n", "Cancel mutated fixture")
        self.check("properties")
        self.ui.row_click(self.source); self.ui.click("Rename", "button")
        self.dialog("Sumi Rename", "Apply"); self.text("../invalid")
        self.key("Return")
        self.wait(lambda: self.session.state()["status"] == "error", "Invalid Rename was not rejected")
        self.dialog("Sumi Rename", "Apply")
        entry = self.ui.find("Name", "text")
        length = self.ui.api.Text.get_character_count(entry)
        require(self.ui.api.Text.get_text(entry, 0, length) == "../invalid", "Rejected Rename lost its input")
        self.cancel("Sumi Rename", "Apply")
        require(self.source.exists() and not (self.root.parent / "invalid").exists(), "Rejected Rename mutated fixture")
        self.check("invalid-input-retained")
        self.ui.row_click(self.source)
        self.key("ctrl+a")
        self.ui.click("Trash", "button")
        self.dialog("Confirm Trash", "Trash")
        self.cancel("Confirm Trash", "Trash", False)
        require(sorted(p.name for p in self.root.iterdir()) == before, "Cancelled multiple selection Trash mutated files")
        self.check("multi-trash-cancel")

    def workflow(self):
        self.ui.click("New folder", "button"); self.dialog("Sumi New Folder", "Apply")
        self.text("created by GUI"); self.key("Return")
        created = self.root / "created by GUI"
        self.wait(created.is_dir, "GUI New folder filesystem effect"); self.check("create")
        self.ui.row_click(created); self.ui.click("Rename", "button"); self.dialog("Sumi Rename", "Apply")
        self.text("renamed by GUI"); self.key("Return")
        renamed = self.root / "renamed by GUI"
        self.wait(lambda: renamed.is_dir() and not created.exists(), "GUI Rename filesystem effect"); self.check("rename")
        self.ui.row_click(self.source); self.ui.click("Copy", "button")
        self.address(renamed); self.ui.click("Paste", "button")
        copied = renamed / self.source.name
        self.wait(copied.exists, "GUI Paste filesystem effect")
        require(copied.read_bytes() == self.source.read_bytes() and self.source.exists(), "Copy changed source/bytes")
        self.check("copy-paste")
        self.ui.click("Undo", "button")
        self.wait(lambda: not copied.exists(), "GUI Undo filesystem effect")
        require(self.source.read_bytes() == b"GUI workflow bytes\n" and renamed.is_dir(), "Undo touched unrelated fixture")
        self.check("undo")


def execute(args):
    require(os.environ.get("DISPLAY") and os.environ.get("DBUS_SESSION_BUS_ADDRESS"), "Use isolated xvfb-run + dbus-run-session")
    artifacts = args.artifacts.resolve(); artifacts.mkdir(parents=True, exist_ok=True)
    spec = importlib.util.spec_from_file_location("explorer_gui", ROOT / "tests/explorer-gui-test.py")
    gui = importlib.util.module_from_spec(spec); spec.loader.exec_module(gui)
    runner = DesignRun(args.binary.resolve(), artifacts, gui)
    receipt = {"schema": 1, "kind": "sumi-explorer-design-gui", "sources": sources(), "assertions": assertion_counts(),
               "binary": {"path": str(args.binary.resolve()), "sha256": digest(args.binary)},
               "groups": {name: {"status": "not-run", "checks": [], "error": ""} for name in GROUPS}, "artifacts": []}
    success = True
    for name in GROUPS if args.check == "all" else [args.check]:
        record = receipt["groups"][name]
        try:
            runner.run_group(name)
            record["status"] = "pass"
            print(f"PASS {name}: {len(runner.completed)} GUI checks", flush=True)
        except Exception as error:
            record.update(status="fail", error=str(error)); success = False
            (artifacts / f"{name}-failure.log").write_text(traceback.format_exc())
            print(f"FAIL {name}: {error}", flush=True)
        record["checks"] = runner.completed
    for path in sorted(artifacts.iterdir()):
        if path.suffix in {".png", ".log"}:
            receipt["artifacts"].append({"path": str(path), "sha256": digest(path), "kind": "screenshot" if path.suffix == ".png" else "log"})
    output = artifacts / "design-validation.json"
    output.write_text(json.dumps(receipt, indent=2) + "\n")
    print(f"Receipt: {output}")
    return 0 if success else 1


if __name__ == "__main__":
    resource.setrlimit(resource.RLIMIT_AS, (8*1024**3, 8*1024**3))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path)
    parser.add_argument("--artifacts", type=Path)
    parser.add_argument("--verify", type=Path)
    parser.add_argument("--negative-controls", type=Path)
    parser.add_argument("--check", choices=["all", *GROUPS], default="all")
    args = parser.parse_args()
    if args.verify:
        verify(args.verify, args.check)
    elif args.negative_controls:
        verify(args.negative_controls); negative_controls(args.negative_controls)
    else:
        require(args.binary and args.artifacts, "--binary and --artifacts are required for execution")
        raise SystemExit(execute(args))
