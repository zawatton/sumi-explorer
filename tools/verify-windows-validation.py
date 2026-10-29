#!/usr/bin/env python3
"""Check a historical Windows receipt against current source hashes.

This checks recorded evidence, not live Windows execution or manual ACL edits.
No network, compiler, private artifact, or Windows installation is required.
"""

import argparse
import hashlib
import json
from pathlib import Path
import re
import runpy
import sys


ROOT = Path(__file__).resolve().parents[1]
COMMON = runpy.run_path(str(ROOT / "tools/verify-validation.py"))
require = COMMON["require"]
exact_keys = COMMON["exact_keys"]
digest = COMMON["digest"]
SOURCES = {
    ".gitattributes",
    "explorer/build.el", "explorer/sumi-explorer-native.el", "explorer/sumi-explorer.el",
    "explorer/platform.c", "explorer/platform.h", "explorer/platform-zip.h",
    "explorer/platform-zip.inc", "tests/sumi-explorer-windows-test.el",
    "tests/explorer-platform-windows-test.c", "tests/explorer-platform-metadata-test.c",
    "tests/explorer-platform-zip-test.c", "tools/windows-native-check.ps1",
    ".github/workflows/windows-native.yml",
}
CHECKS = {"build": 1, "unicode": 1, "shell": 59, "metadata": 229,
          "zip_io": 63, "negative_controls": 7, "emacs_native": 4, "gui": 1}


def validate(record, root=ROOT):
    exact_keys(record, ("schema_version", "run", "compiler", "platform", "emacs",
                        "binary_sha256", "limits", "sources", "checks", "limitations"), "Windows record")
    require(type(record["schema_version"]) is int and record["schema_version"] == 1,
            "Unsupported Windows record schema")
    run = record["run"]
    exact_keys(run, ("repository", "id", "attempt", "commit", "conclusion"), "run")
    require(run["repository"] == "https://github.com/zawatton/sumi-explorer"
            and type(run["id"]) is int and run["id"] > 0
            and type(run["attempt"]) is int and run["attempt"] > 0
            and isinstance(run["commit"], str) and re.fullmatch(r"[0-9a-f]{40}", run["commit"])
            and run["conclusion"] == "success", "Nonpassing or invalid Windows run")
    exact_keys(record["compiler"], ("repository", "revision"), "compiler")
    require(record["compiler"] == {
        "repository": "https://github.com/zawatton/nelisp.git",
        "revision": "f3551799d210ed32f594383997843f0e1bf25e24"}, "Unexpected Windows compiler")
    require(record["platform"] == {"os": "windows", "architecture": "x86_64"},
            "Unexpected Windows platform")
    digest(record["binary_sha256"], "historical Windows binary")
    require(record["emacs"] == {
        "version": "30.2",
        "archive_sha256": "414d3a1a21147af257ebd98bdd15976fdcb5ed0563f6de89f76d4a4b5dad9c72",
        "executable_sha256": "24848ae901463e3c56dac17031ddd45d58220723aa35102fc778458d17ff2f0b"},
        "Unexpected GNU Emacs dependency")
    limits = record["limits"]
    exact_keys(limits, ("per_process_private_commit_bytes", "job_private_commit_bytes",
                       "timeout_seconds"), "limits")
    require(all(type(value) is int for value in limits.values()) and limits == {
        "per_process_private_commit_bytes": 8589934592,
        "job_private_commit_bytes": 8589934592, "timeout_seconds": 600}, "Invalid Windows limits")
    sources = record["sources"]
    exact_keys(sources, SOURCES, "Windows sources")
    root = Path(root).resolve()
    for name, expected in sources.items():
        digest(expected, name)
        path = root / name
        require(path.resolve().is_relative_to(root) and path.is_file() and not path.is_symlink(),
                f"Missing or unsafe Windows source: {name}")
        require(hashlib.sha256(path.read_bytes()).hexdigest() == expected,
                f"Stale Windows source: {name}")
    exact_keys(record["checks"], CHECKS, "Windows checks")
    for name, count in CHECKS.items():
        check = record["checks"][name]
        exact_keys(check, ("status", "exit_code", "cases", "log_sha256"), name)
        require(check["status"] == "pass" and type(check["exit_code"]) is int
                and check["exit_code"] == 0 and type(check["cases"]) is int
                and check["cases"] == count, f"Nonpassing or wrong-count Windows check: {name}")
        digest(check["log_sha256"], name)
    limitations = record["limitations"]
    require(isinstance(limitations, list) and limitations
            and all(isinstance(item, str) and item.strip() for item in limitations),
            "Missing Windows limitations")
    return list(CHECKS)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", type=Path, default=ROOT / "docs/windows-validation.json")
    args = parser.parse_args()
    try:
        with args.record.open("rb") as stream:
            data = stream.read(1048577)
        require(len(data) <= 1048576, "Oversized Windows record")
        record = json.loads(data.decode("utf-8"), object_pairs_hook=COMMON["unique_object"])
        names = validate(record)
    except (OSError, ValueError, TypeError) as error:
        print(f"FAIL historical Windows record: {error}", file=sys.stderr)
        return 1
    print("PASS historical Windows source binding; recorded checks: " + ", ".join(names))
    return 0


if __name__ == "__main__":
    sys.exit(main())
