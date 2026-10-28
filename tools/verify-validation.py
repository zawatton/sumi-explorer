#!/usr/bin/env python3
"""Validate a historical Linux run record and its current source hashes.

This does not run tests, rebuild a binary, or independently prove past execution.
No compiler, binary, private log, or network access is required.
"""

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys


CHECKS = {
    "build": 1, "byte_compile": 3, "client": 42, "emacs_native": 3,
    "gui": 1, "batches": 15, "operations": 12, "dnd": 7,
}
SOURCES = {
    "explorer/build.el", "explorer/sumi-explorer-native.el", "explorer/sumi-explorer.el",
    "tests/sumi-explorer-client-test.el", "tests/sumi-explorer-integration-test.el",
    "tests/explorer-gui-test.py", "tests/explorer-batches-test.py",
    "tests/explorer-operations-test.py", "tests/explorer-dnd-test.py", "tools/check.sh",
}
SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def exact_keys(value, keys, name):
    require(isinstance(value, dict) and set(value) == set(keys), f"Invalid {name} keys")


def digest(value, name):
    require(isinstance(value, str) and SHA256.fullmatch(value), f"Invalid {name} SHA256")


def validate(record, root, requested=()):
    """Validate RECORD against ROOT; return named recorded checks, not executions."""
    exact_keys(record, ("schema_version", "compiler", "platform", "binary_sha256",
                        "sources", "checks", "limitations"), "record")
    require(type(record["schema_version"]) is int and record["schema_version"] == 1,
            "Unsupported validation schema")
    compiler = record["compiler"]
    exact_keys(compiler, ("repository", "revision"), "compiler")
    require(compiler["repository"] == "https://github.com/zawatton/nelisp.git",
            "Unexpected compiler repository")
    require(isinstance(compiler["revision"], str)
            and re.fullmatch(r"[0-9a-f]{40}", compiler["revision"]), "Invalid compiler revision")
    platform = record["platform"]
    exact_keys(platform, ("os", "architecture"), "platform")
    require(platform["os"] == "linux" and platform["architecture"] in ("x86_64", "aarch64"),
            "This record schema covers Linux runtime checks only")
    digest(record["binary_sha256"], "historical binary")
    limitations = record["limitations"]
    require(isinstance(limitations, list) and limitations
            and all(isinstance(item, str) and item.strip() for item in limitations),
            "Missing validation limitations")
    sources = record["sources"]
    require(isinstance(sources, dict), "Invalid source mapping")
    root = Path(root).resolve()
    for name, expected in sources.items():
        require(isinstance(name, str) and name and "\\" not in name and ":" not in name,
                "Unsafe source path")
        relative = PurePosixPath(name)
        require(not relative.is_absolute() and name == relative.as_posix()
                and all(part not in (".", "..") for part in relative.parts), "Unsafe source path")
        path = root / name
        require(path.resolve().is_relative_to(root) and path.is_file() and not path.is_symlink(),
                f"Missing or unsafe source: {name}")
        digest(expected, name)
        require(hashlib.sha256(path.read_bytes()).hexdigest() == expected, f"Stale source: {name}")
    require(set(sources) == SOURCES, "Source coverage must include exactly the app, build, six tests, and helper")
    checks = record["checks"]
    exact_keys(checks, CHECKS, "checks")
    for name, cases in CHECKS.items():
        check = checks[name]
        require(isinstance(check, dict)
                and set(check) in ({"status", "exit_code", "cases"},
                                   {"status", "exit_code", "cases", "log_sha256"}),
                f"Invalid check fields: {name}")
        require(check["status"] == "pass" and type(check["exit_code"]) is int
                and check["exit_code"] == 0 and type(check["cases"]) is int
                and check["cases"] == cases, f"Nonpassing or wrong-count recorded check: {name}")
        if "log_sha256" in check:
            digest(check["log_sha256"], f"{name} historical log")
    require(all(name in CHECKS for name in requested), "Unknown requested check")
    return list(requested or CHECKS)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="append", choices=CHECKS, default=[])
    parser.add_argument("--record", type=Path, help="Historical record; default docs/validation.json")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        with (args.record or root / "docs/validation.json").open("rb") as stream:
            data = stream.read(1048577)
        require(len(data) <= 1048576, "Oversized validation record")
        record = json.loads(data.decode("utf-8"), object_pairs_hook=unique_object)
        names = validate(record, root, args.check)
    except (OSError, ValueError, TypeError) as error:
        print(f"FAIL historical validation record: {error}", file=sys.stderr)
        return 1
    print("PASS historical source binding; recorded checks: " + ", ".join(names))
    return 0


if __name__ == "__main__":
    sys.exit(main())
