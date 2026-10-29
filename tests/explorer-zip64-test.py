#!/usr/bin/env python3
"""Streamed ZIP64 GUI acceptance and strictly source-bound evidence receipts.

Run under Xvfb and a private D-Bus session, with an 8 GiB address-space cap.
"""
import argparse
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import resource
import struct
import tempfile
import zipfile
import zlib

ROOT = Path(__file__).resolve().parents[1]
GROUPS = {
    "parse": ["forced-local64", "forced-eocd64", "descriptor64-signed", "descriptor64-unsigned", "streaming-marker64", "classic-retained", "offset64-version45", "disk64-version45"],
    "safety": ["locator-disk", "locator-offset", "eocd64-overflow", "eocd64-count", "eocd64-size", "missing-extra", "truncated-extra", "duplicate-extra", "local-disagreement", "descriptor-disagreement", "central-overlap", "encrypted", "traversal", "crc", "resource-limit", "local-offset-int64-max", "offset64-version20", "disk64-version20"],
    "workflow": ["large-create", "large-independent-reader", "large-extract", "count-create", "count-independent-reader", "count-extract"],
}
SOURCES = ["explorer/sumi-explorer-native.el", "explorer/platform.c", "explorer/platform.h", "tests/explorer-gui-test.py", "tests/explorer-zip64-test.py"]


def require(value, message):
    if not value:
        raise AssertionError(message)


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def sources():
    return {name: digest(ROOT / name) for name in SOURCES}


def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "Duplicate JSON key")
        result[key] = value
    return result


def load(path):
    return json.loads(Path(path).read_text(), object_pairs_hook=unique)


def verify(path, check="all", quiet=False):
    record = load(path)
    require(type(record) is dict and set(record) == {"schema", "kind", "sources", "binary", "groups", "artifacts"}, "Invalid receipt fields")
    require(type(record["schema"]) is int and record["schema"] == 1 and record["kind"] == "sumi-zip64", "Invalid receipt schema")
    require(record["sources"] == sources(), "Current sources differ")
    binary = record["binary"]
    require(type(binary) is dict and set(binary) == {"path", "sha256"} and digest(binary["path"]) == binary["sha256"], "Binary differs")
    require(type(record["groups"]) is dict and set(record["groups"]) == set(GROUPS), "Invalid group set")
    for name, checks in GROUPS.items():
        group = record["groups"][name]
        require(type(group) is dict and set(group) == {"status", "checks", "error"}, "Invalid group fields")
        require(group["status"] in ("pass", "fail", "not-run") and type(group["error"]) is str, "Invalid status/error")
        require(type(group["checks"]) is list and group["checks"] == checks[:len(group["checks"])], "Unordered/duplicate checks")
        if group["status"] == "pass":
            require(group["checks"] == checks and group["error"] == "", "Incomplete passing group")
        elif group["status"] == "not-run":
            require(group["checks"] == [] and group["error"] == "", "Unrun group has evidence")
        else:
            require(bool(group["error"]), "Failed group lacks error")
    selected = list(GROUPS) if check == "all" else [check]
    require(all(record["groups"][name]["status"] == "pass" for name in selected), "Selected group did not pass")
    require(type(record["artifacts"]) is list, "Invalid artifact list")
    seen, paths = set(), set()
    for item in record["artifacts"]:
        require(type(item) is dict and set(item) == {"group", "kind", "path", "sha256"}, "Invalid artifact fields")
        name, kind = item["group"], item["kind"]
        require(name in GROUPS and kind in ("events", "log"), "Invalid artifact role")
        require((name, kind) not in seen and item["path"] not in paths, "Duplicate artifact")
        seen.add((name, kind)); paths.add(item["path"])
        require(digest(item["path"]) == item["sha256"], "Artifact hash differs")
        if kind == "events":
            require(load(item["path"]) == {"group": name, "sources": record["sources"], "binary": binary, **record["groups"][name]}, "Events differ")
        else:
            require(Path(item["path"]).stat().st_size > 0, "Empty runtime log")
            events = [json.loads(line, object_pairs_hook=unique) for line in Path(item["path"]).read_text().splitlines()]
            require([event["check"] for event in events if "check" in event] == record["groups"][name]["checks"], "Runtime log checks differ")
            require(all(type(event) is dict and (set(event) == {"check"} or set(event) == {"request", "command", "response"}) for event in events), "Runtime log fields differ")
    for name in selected:
        require(all((name, kind) in seen for kind in ("events", "log")), "Missing runtime artifact")
    if not quiet:
        print("PASS ZIP64 receipt", check)
    return record


def negative_controls(path, check="all"):
    original = verify(path, check, quiet=True)
    checked = next(iter(GROUPS)) if check == "all" else check
    mutations = []
    def change(fn):
        value = copy.deepcopy(original); fn(value); mutations.append(value)
    change(lambda r: r.update(schema=True))
    change(lambda r: r.update(kind="other"))
    change(lambda r: r["sources"].update({SOURCES[0]: "0" * 64}))
    change(lambda r: r["binary"].update(sha256="0" * 64))
    change(lambda r: r["groups"].pop("parse"))
    change(lambda r: r["groups"][checked]["checks"].pop())
    change(lambda r: r["groups"][checked]["checks"].reverse())
    change(lambda r: r["groups"][checked]["checks"].append(GROUPS[checked][0]))
    change(lambda r: r["groups"][checked].update(status="not-run", checks=[]))
    change(lambda r: r.update(artifacts=[]))
    change(lambda r: r["artifacts"].append(copy.deepcopy(r["artifacts"][0])))
    change(lambda r: r["artifacts"][0].update(sha256="0" * 64))
    change(lambda r: r.update(extra="untrusted"))
    with tempfile.TemporaryDirectory() as directory:
        bad = Path(directory) / "bad.json"
        encoded = [json.dumps(value) for value in mutations] + [Path(path).read_text().replace('"schema": 1', '"schema": 1, "schema": 1', 1)]
        for value in encoded:
            bad.write_text(value)
            try:
                verify(bad, quiet=True)
            except (AssertionError, ValueError, OSError, TypeError, KeyError):
                continue
            raise AssertionError("Corrupt receipt accepted")
    print("PASS", len(encoded), "corrupt ZIP64 receipt controls")


def forced(name=b"entry", payload=b"ZIP64 payload", descriptor=None, duplicate=False, marker=False):
    """Construct a real single-member stored ZIP64 with full raw extensions."""
    crc = zlib.crc32(payload)
    flags = 8 if descriptor is not None else 0
    extra = struct.pack("<HHQQ", 1, 16, 0 if flags else len(payload), 0 if flags else len(payload))
    if duplicate:
        extra *= 2
    if marker:
        extra = struct.pack("<HH", 1, 0)
    local = struct.pack("<IHHHHHIIIHH", 0x04034b50, 45, flags, 0, 0, 0, 0 if flags else crc, 0 if marker else 0xffffffff, 0 if marker else 0xffffffff, len(name), len(extra)) + name + extra
    trailer = b"" if descriptor is None else (b"PK\x07\x08" if descriptor else b"") + struct.pack("<IQQ", crc, len(payload), len(payload))
    central_extra = struct.pack("<HHQQQ", 1, 24, len(payload), len(payload), 0)
    if marker:
        central_extra = b""
    central = struct.pack("<IHHHHHHIIIHHHHHII", 0x02014b50, 45, 45, flags, 0, 0, 0, crc, len(payload) if marker else 0xffffffff, len(payload) if marker else 0xffffffff, len(name), len(central_extra), 0, 0, 0, 0, 0 if marker else 0xffffffff) + name + central_extra
    offset = len(local) + len(payload) + len(trailer)
    end64 = struct.pack("<IQHHIIQQQQ", 0x06064b50, 44, 45, 45, 0, 0, 1, 1, len(central), offset)
    locator = struct.pack("<IIQI", 0x07064b50, 0, offset + len(central), 1)
    end = struct.pack("<IHHHHIIH", 0x06054b50, 0, 0, 0xffff, 0xffff, 0xffffffff, 0xffffffff, 0)
    return local + payload + trailer + central + end64 + locator + end


def central_only(field, version=20):
    """Use only the selected central ZIP64 field, with ordinary local sizes."""
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("entry", b"ZIP64 payload")
    raw = bytearray(stream.getvalue())
    central = raw.index(b"PK\x01\x02")
    end = raw.index(b"PK\x05\x06")
    payload = struct.pack("<Q", 0) if field == "offset" else struct.pack("<I", 0)
    extra = struct.pack("<HH", 1, len(payload)) + payload
    struct.pack_into("<H", raw, central + 6, version)
    struct.pack_into("<H", raw, central + 30, len(extra))
    if field == "offset":
        struct.pack_into("<I", raw, central + 42, 0xffffffff)
    else:
        struct.pack_into("<H", raw, central + 34, 0xffff)
    struct.pack_into("<I", raw, end + 12, end - central + len(extra))
    return raw[:end] + extra + raw[end:]


class Runner:
    def __init__(self, binary, directory):
        spec = importlib.util.spec_from_file_location("gui", ROOT / "tests/explorer-gui-test.py")
        self.gui = importlib.util.module_from_spec(spec); spec.loader.exec_module(self.gui)
        self.directory = directory
        self.root = directory / "root"; self.root.mkdir()
        self.session_dir = directory / "session"; self.session_dir.mkdir()
        self.completed = []
        self.runtime_log = directory / "runtime.log"
        os.environ["SUMI_EXPLORER_UNDO_DIR"] = str(directory / "undo-profile")
        os.environ["XDG_CONFIG_HOME"] = str(directory / "config")
        self.session = self.gui.Session(binary, self.root, self.session_dir)

    def done(self, name):
        self.completed.append(name)
        with self.runtime_log.open("a") as output:
            output.write(json.dumps({"check": name}) + "\n")
        print("PASS", name, flush=True)

    def command(self, command, source="", destination=None, paths=None):
        s = self.session; s.serial += 1
        text = f"[Explorer]\nversion=1\nrequest={s.serial}\ncommand={command}\npath={self.gui.escape(str(source))}\n"
        if destination is not None:
            text += "destination=" + self.gui.escape(str(destination)) + "\n"
        if paths is not None:
            text += f"count={len(paths)}\n" + "".join(f"path{i}={self.gui.escape(str(p))}\n" for i, p in enumerate(paths))
        tmp = self.session_dir / "request.tmp"; tmp.write_text(text); tmp.replace(self.session_dir / "request.ini")
        result = s.wait(lambda state: state["request"] == str(s.serial), seconds=500, response=True)
        with self.runtime_log.open("a") as output:
            output.write(json.dumps({"request": s.serial, "command": command, "response": result}) + "\n")
        return result

    def extract(self, name, raw, expected=True):
        archive = self.root / (name + ".zip"); archive.write_bytes(raw)
        destination = self.root / (name + "-out")
        result = self.command("zip-extract", archive, destination)
        require((result["status"] == "ok") == expected, f"{name}: {result}")
        if expected:
            require((destination / "entry").read_bytes() == b"ZIP64 payload", "Extracted bytes differ")
        else:
            require(not destination.exists(), "Rejected archive published destination")
        self.done(name)

    def parse(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            with archive.open("entry", "w", force_zip64=True) as member:
                member.write(b"ZIP64 payload")
        self.extract("forced-local64", stream.getvalue())
        self.extract("forced-eocd64", forced())
        self.extract("descriptor64-signed", forced(descriptor=True))
        self.extract("descriptor64-unsigned", forced(descriptor=False))
        self.extract("streaming-marker64", forced(descriptor=True, marker=True))
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("entry", b"ZIP64 payload")
        self.extract("classic-retained", stream.getvalue())
        self.extract("offset64-version45", central_only("offset", 45))
        self.extract("disk64-version45", central_only("disk", 45))

    def safety(self):
        raw = forced(descriptor=True)
        central = raw.index(b"PK\x01\x02"); end64 = raw.index(b"PK\x06\x06"); locator = raw.index(b"PK\x06\x07")
        def patch(offset, fmt, value):
            data = bytearray(raw); struct.pack_into(fmt, data, offset, value); return data
        cases = [
            ("locator-disk", patch(locator + 4, "<I", 1)),
            ("locator-offset", patch(locator + 8, "<Q", 0xffffffffffffffff)),
            ("eocd64-overflow", patch(end64 + 48, "<Q", 0x7fffffffffffffff)),
            ("eocd64-count", patch(end64 + 32, "<Q", 2)),
            ("eocd64-size", patch(end64 + 4, "<Q", 43)),
            ("missing-extra", patch(central + 46 + 5, "<H", 2)),
            ("truncated-extra", patch(central + 46 + 5 + 2, "<H", 23)),
            ("duplicate-extra", forced(descriptor=True, duplicate=True)),
            ("local-disagreement", patch(30 + 5 + 4, "<Q", 9)),
            ("descriptor-disagreement", patch(central - 8, "<Q", 9)),
            ("central-overlap", patch(central + 46 + 5 + 20, "<Q", central)),
            ("encrypted", patch(central + 8, "<H", 9)),
            ("traversal", forced(name=b"../escape", descriptor=True)),
            ("crc", patch(30 + 5 + 20, "<B", 88)),
            ("resource-limit", patch(central + 46 + 5 + 4, "<Q", 17179869185)),
            ("local-offset-int64-max", patch(central + 46 + 5 + 20, "<Q", 0x7ffffffffffffff0)),
            ("offset64-version20", central_only("offset")),
            ("disk64-version20", central_only("disk")),
        ]
        for name, data in cases:
            self.extract(name, data, False)

    def workflow(self):
        large = self.root / "large.bin"
        logical = 4294967296 + 65537
        with large.open("wb") as output:
            output.truncate(logical)
            output.seek(logical - 4); output.write(b"tail")
        archive = self.root / "large.zip"
        require(self.command("zip-create", destination=archive, paths=[large])["status"] == "ok", "Large ZIP creation rejected")
        with zipfile.ZipFile(archive) as saved:
            info = saved.getinfo(large.name)
            require(info.file_size == logical and info.extract_version >= 45, "Not a real large ZIP64 member")
        self.done("large-create")
        expected = digest(large)
        with zipfile.ZipFile(archive) as saved, saved.open(large.name) as member:
            actual = hashlib.sha256(); count = 0
            for block in iter(lambda: member.read(1024 * 1024), b""):
                count += len(block); actual.update(block)
            require(count == logical and actual.hexdigest() == expected, "Independent Python ZIP64 stream differs")
        self.done("large-independent-reader")
        output = self.root / "large-out"
        result = self.command("zip-extract", archive, output)
        require(result["status"] == "ok", f"Large ZIP extraction rejected: {result}")
        require((output / large.name).stat().st_size == logical and digest(output / large.name) == expected, "Large extracted contents differ")
        self.done("large-extract")
        tree = self.root / "many"; tree.mkdir()
        for i in range(65536):
            (tree / f"f{i:05d}").touch()
        archive = self.root / "many.zip"
        require(self.command("zip-create", destination=archive, paths=[tree])["status"] == "ok", "Extended-count creation rejected")
        with zipfile.ZipFile(archive) as saved:
            require(len(saved.infolist()) == 65537, "Extended count differs")
        with archive.open("rb") as source:
            source.seek(-98, 2); require(b"PK\x06\x06" in source.read(), "EOCD64 missing")
        self.done("count-create")
        with zipfile.ZipFile(archive) as saved:
            require(saved.testzip() is None and set(saved.namelist()) == {"many/", *(f"many/f{i:05d}" for i in range(65536))}, "Independent count reader differs")
        self.done("count-independent-reader")
        output = self.root / "many-out"
        require(self.command("zip-extract", archive, output)["status"] == "ok", "Extended-count extraction rejected")
        require(len(list((output / "many").iterdir())) == 65536 and all(p.stat().st_size == 0 for p in (output / "many").iterdir()), "Extended-count extracted tree differs")
        self.done("count-extract")


def run(args):
    artifacts = args.artifacts.resolve(); artifacts.mkdir(parents=True, exist_ok=True)
    binary = {"path": str(args.binary.resolve()), "sha256": digest(args.binary)}
    receipt = {"schema": 1, "kind": "sumi-zip64", "sources": sources(), "binary": binary,
               "groups": {name: {"status": "not-run", "checks": [], "error": ""} for name in GROUPS}, "artifacts": []}
    success = True
    for name in GROUPS if args.check == "all" else [args.check]:
        directory = artifacts / name; directory.mkdir()
        runner = None; record = receipt["groups"][name]
        try:
            runner = Runner(args.binary.resolve(), directory)
            getattr(runner, name)()
            require(runner.completed == GROUPS[name], "Incomplete ordered runtime checks")
            record["status"] = "pass"
        except Exception as error:
            record.update(status="fail", error=str(error)); success = False
            print("FAIL", name, str(error), flush=True)
        finally:
            if runner is not None:
                runner.session.stop(); record["checks"] = runner.completed
        events = directory / "events.json"
        events.write_text(json.dumps({"group": name, "sources": receipt["sources"], "binary": binary, **record}, indent=2) + "\n")
        for kind, path in [("events", events), ("log", directory / "runtime.log")]:
            if path.exists():
                receipt["artifacts"].append({"group": name, "kind": kind, "path": str(path), "sha256": digest(path)})
    require(receipt["sources"] == sources() and digest(args.binary) == binary["sha256"], "Inputs changed during execution")
    output = artifacts / "receipt.json"; output.write_text(json.dumps(receipt, indent=2) + "\n")
    print("ZIP64 receipt", output)
    return 0 if success else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path)
    parser.add_argument("--artifacts", type=Path)
    parser.add_argument("--verify", type=Path)
    parser.add_argument("--negative-controls", type=Path)
    parser.add_argument("--check", choices=["all", *GROUPS], default="all")
    args = parser.parse_args()
    soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    require(soft != resource.RLIM_INFINITY and soft <= 8 * 1024 ** 3, "Run with ulimit -v 8388608")
    if args.verify:
        verify(args.verify, args.check); return 0
    if args.negative_controls:
        negative_controls(args.negative_controls, args.check); return 0
    require(args.binary is not None and args.artifacts is not None, "--binary and --artifacts are required")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
