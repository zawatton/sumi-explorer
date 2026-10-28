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
        self.process = subprocess.Popen(
            [str(binary)],
            env=dict(os.environ, SUMI_EXPLORER_ROOT=str(root), SUMI_EXPLORER_SESSION=str(session)),
            stdout=self.log,
            stderr=subprocess.STDOUT,
        )
        try:
            self.wait(lambda state: state["request"] == "0")
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
