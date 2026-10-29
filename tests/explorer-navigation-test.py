#!/usr/bin/env python3
"""Pointer/keyboard folder navigation acceptance with source-bound run receipts.

Execute under xvfb-run -a -s '-screen 0 1920x1080x24' dbus-run-session.
--verify validates retained execution evidence; it does not execute the GUI.
"""

from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import resource
import struct
import subprocess
import tempfile
import time
import traceback


ROOT = Path(__file__).resolve().parents[1]


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tests" / filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


design = module("sumi_navigation_design", "explorer-design-test.py")
require, digest = design.require, design.digest
GROUPS = {
    "breadcrumbs": ["default-breadcrumbs", "parent-click", "root-click", "refresh-rebuild", "unicode-ancestry", "viewport-1000x700", "viewport-1280x800", "viewport-1600x900"],
    "address": ["edit-button", "selected-current-path", "enter-restores-breadcrumbs", "ctrl-l", "escape-restores-current", "invalid-entry-retained"],
    "workspace": ["back", "forward", "tab-switch", "tab-history", "close-tab"],
    "folders": ["standard-folder-order", "localized-configured-destinations", "shortcut-history", "bookmarks-unchanged"],
    "omissions": ["missing-omitted", "home-alias-omitted", "duplicate-omitted", "existing-retained", "no-guessed-directories"],
    "workflow": ["create", "rename", "breadcrumb-parent", "copy-paste", "undo"],
}
STANDARD = ["Desktop", "Documents", "Downloads", "Pictures", "Music", "Videos"]
VIEWPORTS = ["1000x700", "1280x800", "1600x900"]
SOURCE_SUFFIXES = {".py", ".el", ".c", ".h", ".inc", ".sh", ".ps1"}
BASELINE_ASSERTION_SHA256 = {
    "tests/explorer-advanced-test.py": "a9b3c7dfad553349aab8d8615ad87bb4b8b6b0f90c349b70764922f4c1190c75",
    "tests/explorer-batches-test.py": "d7c72c1deba65cf4857b3c12511e7408b1f4e5fdb44dfa6863339b03b558939e",
    "tests/explorer-dnd-test.py": "2e6d0540f7e870c2eab57739814e44bcde29466c95b9f74741719431269638f2",
    "tests/explorer-everyday-test.py": "00badb99809c6c69f28c8835b9a38ff690c814b59db30001f98a14dd921e6d53",
    "tests/explorer-gui-test.py": "f07cfea9c5b1c14053900c1249e833daee74eb3cbd352a7332e846e4a9af05e0",
    "tests/explorer-operations-test.py": "885ef71af950be844fa3487071173ee8dd60a26cd820388202239e742e7ee7f2",
    "tests/explorer-workspace-test.py": "6568fd65866b8e870122320a6583f4e7fa6a82f6a6e9666434953079c73d3fde",
}
DESIGN_CHECKS_SHA256 = "66d425a887b4242cb1bcb59a833abed79ac517ac1e8b0059ed1c26c4a999afc3"


def sources():
    paths = [p for directory in ("explorer", "tests", "tools") for p in (ROOT / directory).rglob("*")
             if p.is_file() and p.suffix in SOURCE_SUFFIXES]
    paths += [ROOT / "docs/folder-navigation.md"]
    return {str(p.relative_to(ROOT)): digest(p) for p in sorted(paths)}


def source_digest(values):
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def retained_assertions():
    """Pin the existing assertion predicates, independently of run receipts."""
    for name, expected in BASELINE_ASSERTION_SHA256.items():
        nodes = ast.walk(ast.parse((ROOT / name).read_text()))
        predicates = [ast.dump(node) for node in nodes if isinstance(node, ast.Assert)]
        require(hashlib.sha256(json.dumps(predicates).encode()).hexdigest() == expected, f"Retained assertion predicates changed: {name}")
    require(source_digest(design.GROUPS) == DESIGN_CHECKS_SHA256, "The 28 design check IDs changed")


def stored_path(path):
    path = Path(path).resolve()
    return str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)


def resolve(path):
    require(type(path) is str and bool(path), "Invalid evidence path")
    candidate = Path(path)
    return candidate if candidate.is_absolute() else ROOT / candidate


def png_size(path):
    header = path.read_bytes()[:24]
    require(len(header) == 24 and header[:8] == b"\x89PNG\r\n\x1a\n" and header[12:16] == b"IHDR", "Invalid screenshot PNG")
    width, height = struct.unpack(">II", header[16:24])
    require(900 <= width <= 1920 and 600 <= height <= 1080, "Screenshot dimensions outside supported display")
    decoded = subprocess.run(["identify", "-regard-warnings", "-format", "%m %w %h", str(path)],
                             text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10, check=True)
    require(decoded.stdout == f"PNG {width} {height}", "Screenshot cannot be decoded as one PNG image")
    return width, height


def verify(path, group="all", quiet=False):
    receipt = json.loads(Path(path).read_text(), object_pairs_hook=design.unique_object)
    require(type(receipt) is dict and set(receipt) == {"schema", "kind", "sources", "assertions", "binary", "groups", "artifacts"}, "Receipt fields differ")
    require(type(receipt["schema"]) is int and receipt["schema"] == 1 and receipt["kind"] == "sumi-explorer-navigation-gui", "Receipt schema/kind differs")
    require(receipt["sources"] == sources(), "Recorded source hashes differ from current checkout")
    counts = design.assertion_counts()
    require(receipt["assertions"] == counts, "Recorded assertion counts differ")
    require(all(counts.get(name) == count for name, count in design.BASELINE_ASSERTIONS.items()) and sum(design.BASELINE_ASSERTIONS.values()) == 420,
            "The 420 retained behavioral assertions changed")
    require(sum(map(len, design.GROUPS.values())) == 28, "The 28 design checks changed")
    retained_assertions()
    require(group == "all" or group in GROUPS, "Unknown requested group")
    require(type(receipt["groups"]) is dict and set(receipt["groups"]) == set(GROUPS), "Missing/invalid groups")
    for name, checks in GROUPS.items():
        record = receipt["groups"][name]
        require(type(record) is dict and set(record) == {"status", "checks", "error"}, f"Invalid group fields: {name}")
        require(type(record["status"]) is str and record["status"] in {"pass", "fail", "not-run"}, "Invalid status")
        require(type(record["checks"]) is list and record["checks"] == checks[:len(record["checks"])], f"Invalid/duplicate/out-of-order checks: {name}")
        require(type(record["error"]) is str, "Invalid error")
        if record["status"] == "pass":
            require(record["checks"] == checks and record["error"] == "", f"Incomplete passing group: {name}")
        elif record["status"] == "not-run":
            require(record["checks"] == [] and record["error"] == "", "Unrun group contains evidence")
        else:
            require(bool(record["error"]), "Failed group has no error")
    selected = list(GROUPS) if group == "all" else [group]
    for name in selected:
        require(receipt["groups"][name]["status"] == "pass", f"Recorded group did not pass: {name}")
    binary = receipt["binary"]
    require(type(binary) is dict and set(binary) == {"path", "sha256"}, "Invalid binary record")
    require(digest(resolve(binary["path"])) == binary["sha256"], "Binary differs/missing")
    artifacts, seen = receipt["artifacts"], set()
    require(type(artifacts) is list, "Invalid artifacts")
    for artifact in artifacts:
        require(type(artifact) is dict and set(artifact) == {"path", "sha256", "kind", "group"}, "Invalid artifact fields")
        name, kind = artifact["group"], artifact["kind"]
        require(name in GROUPS and kind in {"log", "screenshot", "events", *("viewport-" + size for size in VIEWPORTS)}, "Invalid artifact group/kind")
        require(not kind.startswith("viewport-") or name == "breadcrumbs", "Viewport evidence belongs to breadcrumbs")
        key = (name, kind)
        require(key not in seen, "Duplicate artifact group/kind"); seen.add(key)
        item = resolve(artifact["path"])
        require(digest(item) == artifact["sha256"], "Artifact differs/missing")
        if kind == "screenshot" or kind.startswith("viewport-"):
            size = png_size(item)
            if kind.startswith("viewport-"):
                require("x".join(map(str, size)) == kind.removeprefix("viewport-"), "Viewport screenshot dimensions differ")
        elif kind == "events":
            events = json.loads(item.read_text(), object_pairs_hook=design.unique_object)
            require(events == {"group": name, "source_sha256": source_digest(receipt["sources"]), "binary_sha256": binary["sha256"], **receipt["groups"][name]}, "Runtime events differ from receipt")
        else:
            require(item.stat().st_size > 0, "Empty runtime log")
    paths = [item["path"] for item in artifacts]
    require(len(paths) == len(set(paths)), "Duplicate artifact paths")
    for name in selected:
        require(all((name, kind) in seen for kind in ("log", "screenshot", "events")), f"Missing runtime artifacts: {name}")
        if name == "breadcrumbs":
            require(all((name, "viewport-" + size) in seen for size in VIEWPORTS), "Missing viewport screenshots")
    if not quiet:
        print(f"PASS recorded navigation evidence: {group} (no GUI re-execution)")
    return receipt


def negative_controls(path):
    original = verify(path, quiet=True)
    cases = {
        "missing-group": lambda r: r["groups"].pop("breadcrumbs"),
        "unknown-group": lambda r: r["groups"].update(unknown=r["groups"]["address"]),
        "missing-check": lambda r: r["groups"]["breadcrumbs"]["checks"].pop(),
        "duplicate-check": lambda r: r["groups"]["address"]["checks"].append("ctrl-l"),
        "reordered-checks": lambda r: r["groups"]["address"]["checks"].reverse(),
        "false-pass": lambda r: r["groups"]["workflow"].update(status="fail", error="broken"),
        "skipped": lambda r: r["groups"]["folders"].update(status="skip"),
        "source": lambda r: r["sources"].update({"explorer/sumi-explorer-native.el": "0" * 64}),
        "missing-source": lambda r: r["sources"].pop("explorer/platform.c"),
        "assertions": lambda r: r["assertions"].update({"tests/explorer-gui-test.py": 0}),
        "binary": lambda r: r["binary"].update(sha256="0" * 64),
        "artifact-hash": lambda r: r["artifacts"][0].update(sha256="0" * 64),
        "missing-artifacts": lambda r: r.update(artifacts=[]),
        "duplicate-artifact": lambda r: r["artifacts"].append(r["artifacts"][0]),
        "extra-field": lambda r: r.update(extra=True),
    }
    encoded = []
    for name, mutate in cases.items():
        broken = copy.deepcopy(original); mutate(broken)
        encoded.append((name, json.dumps(broken)))
    encoded.append(("duplicate-json-key", json.dumps(original).replace('"schema": 1', '"schema": 1, "schema": 1', 1)))
    with tempfile.TemporaryDirectory(prefix="sumi-navigation-controls-") as temporary:
        bad = Path(temporary) / "bad.json"
        screenshot = next(item for item in original["artifacts"] if item["kind"] == "screenshot")
        corrupt = Path(temporary) / "truncated.png"
        corrupt.write_bytes(resolve(screenshot["path"]).read_bytes()[:24])
        broken = copy.deepcopy(original)
        replacement = next(item for item in broken["artifacts"] if item["kind"] == "screenshot")
        replacement.update(path=str(corrupt), sha256=digest(corrupt))
        encoded.append(("truncated-png-with-correct-hash", json.dumps(broken)))
        log = Path(temporary) / "empty.log"; log.write_bytes(b"")
        broken = copy.deepcopy(original)
        next(item for item in broken["artifacts"] if item["kind"] == "log").update(path=str(log), sha256=digest(log))
        encoded.append(("empty-log-with-correct-hash", json.dumps(broken)))
        events = Path(temporary) / "events.json"; events.write_text("{}\n")
        broken = copy.deepcopy(original)
        next(item for item in broken["artifacts"] if item["kind"] == "events").update(path=str(events), sha256=digest(events))
        encoded.append(("events-with-correct-hash", json.dumps(broken)))
        for name, value in encoded:
            bad.write_text(value)
            try:
                verify(bad, quiet=True)
            except (AssertionError, ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError):
                continue
            raise AssertionError(f"Broken evidence accepted: {name}")
    print(f"PASS {len(encoded)} corrupted navigation receipt controls")


class NavigationRun(design.DesignRun):
    def text(self, value):
        self.key("ctrl+a")
        self.gui.xdo("type", "--delay", "20", "--clearmodifiers", "--", str(value))

    def shown(self, name, role=None):
        return [n for n in self.ui.nodes() if n.get_name() == name and (role is None or n.get_role_name() == role)
                and n.get_state_set().contains(self.ui.api.StateType.SHOWING)]

    def entry_text(self):
        node = self.ui.find("Location", "text")
        return self.ui.api.Text.get_text(node, 0, self.ui.api.Text.get_character_count(node))

    def breadcrumb(self, path, visible=True):
        name = Path(path).name or str(Path(path).anchor)
        edit = self.ui.rect(self.ui.find("Edit location", "button"))
        candidates = [n for n in self.ui.nodes() if n.get_name() == name and n.get_role_name() == "button"]
        for node in candidates:
            if visible and not node.get_state_set().contains(self.ui.api.StateType.SHOWING):
                continue
            x, y, w, h = self.ui.rect(node)
            if abs(y - edit[1]) < 14 and w > 0 and h > 0:
                return node
        raise AssertionError(f"No breadcrumb for {path}")

    def click_node(self, node):
        x, y, width, height = self.ui.rect(node)
        self.gui.xdo("mousemove", str(x + width//2), str(y + height*3//4)); self.gui.xdo("click", "1")

    def click_crumb(self, path):
        self.click_node(self.breadcrumb(path))
        self.wait(lambda: self.session.state()["directory"] == str(path), f"Breadcrumb destination: {path}")

    def default_mode(self):
        self.ui.find("Edit location", "button")
        require(not self.shown("Location", "text"), "Default Location entry remains visible")
        self.breadcrumb(Path(self.session.state()["directory"]))

    def fixtures(self, base, omissions):
        self.home = base / "user home 日本語"; self.home.mkdir()
        self.root = self.home / "作業 workspace"; self.root.mkdir()
        self.child = self.root / "子 folder"; self.child.mkdir()
        self.source = self.root / "文書 alpha.txt"; self.source.write_bytes(b"navigation workflow bytes\n")
        self.special = {name: self.home / label for name, label in zip(STANDARD, ["机 Desktop 日本語", "資料 Documents", "取得 Downloads", "写真 Pictures", "音楽 Music", "動画 Videos"])}
        for directory in self.special.values(): directory.mkdir()
        if omissions:
            self.special["Documents"].rmdir()
            self.special["Downloads"].rmdir(); self.special["Downloads"] = self.home
            self.special["Pictures"].rmdir(); self.special["Pictures"] = self.special["Desktop"]
        config = base / "config"; config.mkdir()
        names = {"Desktop": "DESKTOP", "Documents": "DOCUMENTS", "Downloads": "DOWNLOAD", "Pictures": "PICTURES", "Music": "MUSIC", "Videos": "VIDEOS"}
        (config / "user-dirs.dirs").write_text("".join(f'XDG_{names[name]}_DIR="$HOME/{path.relative_to(self.home)}"\n' if path != self.home else f'XDG_{names[name]}_DIR="$HOME"\n' for name, path in self.special.items()))
        (config / "user-dirs.locale").write_text("ja_JP\n")
        return {"HOME": str(self.home), "XDG_CONFIG_HOME": str(config), "XDG_DATA_HOME": str(base / "data"), "XDG_CACHE_HOME": str(base / "cache")}

    def run_group(self, group):
        self.group, self.completed = group, []
        with tempfile.TemporaryDirectory(prefix=f"sumi-navigation-{group}-") as temporary:
            base = Path(temporary)
            environment = self.fixtures(base, group == "omissions")
            previous = {key: os.environ.get(key) for key in environment}
            os.environ.update(environment)
            session_path = base / "session"; session_path.mkdir(mode=0o700)
            self.session = self.gui.Session(self.binary, self.root, session_path)
            try:
                self.ui = self.session.ui()
                self.window = self.gui.xdo("search", "--all", "--sync", "--onlyvisible", "--pid", str(self.session.process.pid), "--name", "^Sumi Explorer$").splitlines()[-1]
                self.gui.xdo("windowsize", self.window, "1280", "800")
                self.gui.xdo("windowmove", self.window, "37", "29")
                self.gui.xdo("windowfocus", "--sync", self.window)
                getattr(self, "address_group" if group == "address" else group)()
                require(self.completed == GROUPS[group], f"Incomplete checks: {group}")
            finally:
                if hasattr(self, "window"):
                    try: self.screenshot(group)
                    except Exception: pass
                self.session.stop()
                (self.artifacts / f"{group}-host.log").write_bytes(
                    f"Native GUI PID={self.session.process.pid} exit={self.session.process.returncode}\n".encode()
                    + (session_path / "host.log").read_bytes())
                for key, value in previous.items():
                    if value is None: os.environ.pop(key, None)
                    else: os.environ[key] = value
        require(self.session.process.returncode == 0, f"Host exit: {self.session.process.returncode}")

    def breadcrumbs(self):
        self.default_mode(); self.check("default-breadcrumbs")
        self.address(self.child); self.click_crumb(self.root); self.check("parent-click")
        self.address(self.home); self.click_crumb(Path("/")); self.default_mode(); self.check("root-click")
        self.address(self.child); self.ui.click("Refresh", "button")
        self.default_mode(); self.click_crumb(self.root); self.check("refresh-rebuild")
        deep = self.root
        for index in range(7):
            deep /= f"階層 {index} 日本語 " + "長い名前" * 9
            deep.mkdir()
        self.address(deep)
        # Offscreen ancestry must still own distinct basename and path data.
        names = {n.get_name() for n in self.ui.nodes() if n.get_role_name() == "button"}
        require(all(p.name in names for p in [deep, *deep.parents] if p.name), "Long Unicode ancestry missing segments")
        self.check("unicode-ancestry")
        for width, height in [(1000, 700), (1280, 800), (1600, 900)]:
            self.gui.xdo("windowsize", self.window, str(width), str(height)); time.sleep(0.2)
            actual = dict(line.split("=", 1) for line in self.gui.xdo("getwindowgeometry", "--shell", self.window).splitlines())
            require(int(actual["WIDTH"]) == width and int(actual["HEIGHT"]) == height, "Long ancestry enlarged window")
            frame = self.ui.find("Sumi Explorer", "frame")
            for name in design.COMMON + design.NAV + ["New tab", "Home"]: self.within(frame, self.ui.find(name))
            listing = self.ui.find(role="list"); self.within(frame, listing)
            require(self.ui.rect(listing)[2] >= width * 0.55 and self.ui.rect(listing)[3] >= height * 0.5, "Long ancestry displaced file area")
            # GTK may omit the scroll container from its accessibility tree.
            # Its real allocation lies between these exposed adjacent buttons;
            # both geometries include the actual X11 window origin.
            refresh = self.ui.rect(self.ui.find("Refresh", "button"))
            edit = self.ui.rect(self.ui.find("Edit location", "button"))
            sx, sy = refresh[0] + refresh[2], edit[1]
            sw, sh = edit[0] - sx, edit[3]
            current = self.ui.rect(self.breadcrumb(deep))
            require(current[2] >= 90 and current[0] >= sx - 2 and current[0] + current[2] <= sx + sw + 2,
                    f"Current long Unicode breadcrumb is unreadable/clipped: {current}, viewport={(sx,sy,sw,sh)}")
            require(self.ui.rect(self.breadcrumb(Path("/"), visible=False))[0] < sx,
                    "Deep ancestry was compressed instead of overflowing horizontally")
            self.screenshot(f"breadcrumbs-{width}x{height}")
            if width == 1000:
                # X11 buttons 6/7 are actual horizontal wheel events. Scroll
                # the viewport to the otherwise offscreen filesystem root.
                self.gui.xdo("mousemove", str(sx + sw//2), str(sy + sh//2))
                self.gui.xdo("click", "--repeat", "50", "--delay", "15", "6")
                self.wait(lambda: self.ui.rect(self.breadcrumb(Path("/"), visible=False))[0] >= sx - 2,
                          "Horizontal wheel did not reveal the offscreen root breadcrumb")
                self.click_crumb(Path("/")); self.default_mode()
                self.address(deep)
            self.check(f"viewport-{width}x{height}")

    def address(self, path):
        self.key("ctrl+l"); self.ui.click("Location", "text"); self.text(path)
        actual, expected = self.entry_text(), str(path)
        mismatch = next((index for index, pair in enumerate(zip(actual, expected)) if pair[0] != pair[1]), min(len(actual), len(expected)))
        require(actual == expected, f"Pointer/keyboard input differs at {mismatch}; lengths {len(actual)}/{len(expected)}; actual={actual[max(0,mismatch-12):mismatch+30]!r}; expected={expected[max(0,mismatch-12):mismatch+30]!r}")
        self.key("Return")
        try:
            self.wait(lambda: self.session.state()["directory"] == str(path), "Address navigation")
        except AssertionError as error:
            raise AssertionError(f"{error}: {self.session.state()}") from error
        self.wait(lambda: not self.shown("Location", "text"), "Successful address did not return to breadcrumbs")

    def address_group(self):
        self.default_mode(); self.ui.click("Edit location", "button")
        entry = self.ui.find("Location", "text")
        require(entry.get_state_set().contains(self.ui.api.StateType.FOCUSED), "Edit button did not focus Location")
        require(self.entry_text() == str(self.root), "Edit button lost current path")
        self.check("edit-button")
        # Typing without Ctrl+A proves the current location was selected.
        self.gui.xdo("type", "--clearmodifiers", "--", str(self.child))
        require(self.entry_text() == str(self.child), "Location was not fully selected")
        self.check("selected-current-path")
        self.key("Return"); self.wait(lambda: self.session.state()["directory"] == str(self.child), "Enter did not navigate")
        self.default_mode(); self.check("enter-restores-breadcrumbs")
        self.key("ctrl+l"); require(self.entry_text() == str(self.child), "Ctrl+L path differs")
        require(self.ui.find("Location", "text").get_state_set().contains(self.ui.api.StateType.FOCUSED), "Ctrl+L did not focus")
        self.check("ctrl-l")
        self.text("discarded input"); self.key("Escape"); self.default_mode()
        require(self.session.state()["directory"] == str(self.child), "Escape navigated")
        listing = self.ui.find(role="list")
        require(listing.get_state_set().contains(self.ui.api.StateType.FOCUSED), "Escape did not focus file list")
        self.key("ctrl+l"); require(self.entry_text() == str(self.child), "Escape did not restore current path")
        self.check("escape-restores-current")
        for invalid in (str(self.child / "不存在 missing"), "relative-nonexistent-location"):
            # Each rejection must transition from a successful state; the
            # second input cannot inherit the first input's error evidence.
            self.address(self.child)
            require(self.session.state()["status"] == "ok", "Valid address did not clear prior error")
            self.key("ctrl+l")
            self.text(invalid); self.key("Return")
            self.wait(lambda: self.session.state()["status"] == "error", "Invalid location did not report error")
            require(self.entry_text() == invalid and self.session.state()["directory"] == str(self.child), "Invalid input was lost or navigated")
            require(bool(self.session.state()["message"]), "Invalid location omitted existing error feedback")
        self.check("invalid-entry-retained")

    def workspace(self):
        self.address(self.child)
        self.ui.click("Back", "button"); self.wait(lambda: self.session.state()["directory"] == str(self.root), "Back")
        self.default_mode(); self.check("back")
        self.ui.click("Forward", "button"); self.wait(lambda: self.session.state()["directory"] == str(self.child), "Forward")
        self.default_mode(); self.check("forward")
        self.ui.click("New tab", "button"); self.wait(lambda: self.read("workspace.ini")["Workspace"]["count"] == "2", "New tab")
        self.address(self.home)
        self.ui.click(self.child.name, "button"); self.wait(lambda: self.session.state()["directory"] == str(self.child), "Tab switch")
        self.default_mode(); self.click_crumb(self.root); self.check("tab-switch")
        self.ui.click("Back", "button"); self.wait(lambda: self.session.state()["directory"] == str(self.child), "Tab history")
        self.default_mode(); self.check("tab-history")
        self.menu("More"); self.ui.click("Close tab", "button")
        self.wait(lambda: self.read("workspace.ini")["Workspace"]["count"] == "1" and self.session.state()["directory"] == str(self.home), "Close tab")
        self.default_mode(); self.check("close-tab")

    def sidebar(self, name):
        home = self.ui.rect(self.ui.find("Home", "button"))
        for node in self.shown(name, "button"):
            x, y, w, h = self.ui.rect(node)
            if abs(x - home[0]) < 12 and y >= home[1]: return node
        raise AssertionError(f"No sidebar shortcut: {name}")

    def folders(self):
        nodes = [self.sidebar(name) for name in STANDARD]
        ys = [self.ui.rect(node)[1] for node in nodes]
        home_y = self.ui.rect(self.ui.find("Home", "button"))[1]
        bookmarks = self.ui.find("Bookmarks")
        require(home_y < min(ys) and ys == sorted(set(ys)) and max(ys) < self.ui.rect(bookmarks)[1], "Standard folder order differs")
        self.check("standard-folder-order")
        before = (self.session.directory / "bookmarks.ini").read_bytes() if (self.session.directory / "bookmarks.ini").exists() else None
        for name in STANDARD:
            self.click_node(self.sidebar(name))
            self.wait(lambda: self.session.state()["directory"] == str(self.special[name]), f"Configured {name} destination")
            self.default_mode()
        self.check("localized-configured-destinations")
        self.ui.click("Back", "button"); self.wait(lambda: self.session.state()["directory"] == str(self.special["Music"]), "Shortcut Back")
        self.ui.click("Forward", "button"); self.wait(lambda: self.session.state()["directory"] == str(self.special["Videos"]), "Shortcut Forward")
        self.check("shortcut-history")
        after = (self.session.directory / "bookmarks.ini").read_bytes() if (self.session.directory / "bookmarks.ini").exists() else None
        require(before == after, "Shortcuts changed bookmarks"); self.check("bookmarks-unchanged")

    def omissions(self):
        require(not self.shown("Documents", "button"), "Missing Documents is exposed"); self.check("missing-omitted")
        require(not self.shown("Downloads", "button"), "Home alias is exposed"); self.check("home-alias-omitted")
        require(not self.shown("Pictures", "button"), "Duplicate Desktop destination is exposed"); self.check("duplicate-omitted")
        for name in ("Desktop", "Music", "Videos"):
            self.click_node(self.sidebar(name)); self.wait(lambda: self.session.state()["directory"] == str(self.special[name]), f"Existing {name}")
        self.check("existing-retained")
        self.ui.click("Home", "button"); self.wait(lambda: self.session.state()["directory"] == str(self.home), "Home")
        require(not self.special["Documents"].exists() and all(not (self.home / name).exists() for name in STANDARD), "Application invented standard directories")
        self.check("no-guessed-directories")

    def workflow(self):
        self.ui.click("New folder", "button"); self.dialog("Sumi New Folder", "Apply")
        self.text("作成 GUI"); self.key("Return"); created = self.root / "作成 GUI"
        self.wait(created.is_dir, "Mkdir filesystem effect"); self.check("create")
        self.ui.row_click(created); self.ui.click("Rename", "button"); self.dialog("Sumi Rename", "Apply")
        self.text("変更 GUI"); self.key("Return"); renamed = self.root / "変更 GUI"
        self.wait(lambda: renamed.is_dir() and not created.exists(), "Rename filesystem effect"); self.check("rename")
        self.address(renamed); self.click_crumb(self.root); self.check("breadcrumb-parent")
        self.ui.row_click(self.source); self.ui.click("Copy", "button")
        self.address(renamed); self.ui.click("Paste", "button"); copied = renamed / self.source.name
        self.wait(copied.exists, "Copy/Paste filesystem effect")
        require(copied.read_bytes() == self.source.read_bytes(), "Copy bytes differ"); self.check("copy-paste")
        self.ui.click("Undo", "button"); self.wait(lambda: not copied.exists(), "Undo filesystem effect")
        require(self.source.read_bytes() == b"navigation workflow bytes\n" and renamed.is_dir(), "Undo changed unrelated files")
        self.check("undo")


def execute(args):
    require(os.environ.get("DISPLAY") and os.environ.get("DBUS_SESSION_BUS_ADDRESS"), "Use isolated xvfb-run + dbus-run-session")
    retained_assertions()
    artifacts = args.artifacts.resolve(); artifacts.mkdir(parents=True, exist_ok=True)
    require(not any(artifacts.iterdir()), "Use a fresh empty --artifacts directory; stale GUI evidence cannot be reused")
    runner = NavigationRun(args.binary.resolve(), artifacts, module("sumi_navigation_gui", "explorer-gui-test.py"))
    receipt = {"schema": 1, "kind": "sumi-explorer-navigation-gui", "sources": sources(), "assertions": design.assertion_counts(),
               "binary": {"path": stored_path(args.binary), "sha256": digest(args.binary)},
               "groups": {name: {"status": "not-run", "checks": [], "error": ""} for name in GROUPS}, "artifacts": []}
    success = True
    for name in GROUPS if args.check == "all" else [args.check]:
        record = receipt["groups"][name]
        try:
            runner.run_group(name)
            record["status"] = "pass"
            print(f"PASS {name}: {len(runner.completed)} pointer/keyboard checks", flush=True)
        except Exception as error:
            record.update(status="fail", error=str(error)); success = False
            (artifacts / f"{name}-failure.log").write_text(traceback.format_exc())
            print(f"FAIL {name}: {error}", flush=True)
        record["checks"] = runner.completed
        events = {"group": name, "source_sha256": source_digest(receipt["sources"]), "binary_sha256": receipt["binary"]["sha256"], **record}
        (artifacts / f"{name}-events.json").write_text(json.dumps(events, indent=2) + "\n")
        for kind, suffix in [("log", "-host.log"), ("screenshot", ".png"), ("events", "-events.json")]:
            path = artifacts / (name + suffix)
            if path.exists(): receipt["artifacts"].append({"path": stored_path(path), "sha256": digest(path), "kind": kind, "group": name})
        if name == "breadcrumbs":
            for size in VIEWPORTS:
                path = artifacts / f"breadcrumbs-{size}.png"
                if path.exists(): receipt["artifacts"].append({"path": stored_path(path), "sha256": digest(path), "kind": "viewport-" + size, "group": name})
    require(receipt["sources"] == sources(), "Sources changed during GUI execution; rerun against a stable build")
    require(receipt["binary"]["sha256"] == digest(args.binary), "Binary changed during GUI execution")
    output = artifacts / "navigation-validation.json"
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
        negative_controls(args.negative_controls)
    else:
        require(args.binary and args.artifacts, "--binary and --artifacts are required for execution")
        raise SystemExit(execute(args))
