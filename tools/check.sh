#!/bin/sh
# Run retained suites without downloads or a parent workspace dependency.
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$root"
ulimit -v 8388608

need() {
    command -v "$1" >/dev/null 2>&1 || { echo "Missing dependency: $1" >&2; exit 1; }
}

client() {
    for dependency in emacs python3 cat true mktemp cp; do need "$dependency"; done
    scratch=$(mktemp -d)
    trap 'rm -r -- "$scratch"' EXIT
    trap 'exit 1' HUP INT TERM
    cp explorer/sumi-explorer.el tests/sumi-explorer-client-test.el tests/sumi-explorer-integration-test.el "$scratch/"
    emacs -Q --batch -L explorer --eval '(setq byte-compile-error-on-warn t)' \
        -f batch-byte-compile "$scratch/sumi-explorer.el" "$scratch/sumi-explorer-client-test.el" "$scratch/sumi-explorer-integration-test.el"
    emacs -Q --batch -L explorer -l tests/sumi-explorer-client-test.el -f ert-run-tests-batch-and-exit
}

native() {
    for dependency in emacs python3 xvfb-run xdotool dbus-run-session nautilus; do need "$dependency"; done
    binary=${SUMI_EXPLORER_TEST_BIN:-"$root/target/sumi-explorer"}
    case "$binary" in /*) ;; *) binary="$root/$binary" ;; esac
    [ -x "$binary" ] || { echo "Missing executable: $binary (build first)" >&2; exit 1; }
    export SUMI_EXPLORER_TEST_BIN="$binary"
    xvfb-run -a python3 tests/explorer-gui-test.py --binary "$binary" --group all
    xvfb-run -a dbus-run-session -- python3 tests/explorer-dnd-test.py --binary "$binary"
    xvfb-run -a emacs -Q --batch -L explorer -l tests/sumi-explorer-integration-test.el
    xvfb-run -a python3 tests/explorer-operations-test.py --binary "$binary"
    xvfb-run -a dbus-run-session -- python3 tests/explorer-batches-test.py --binary "$binary"
}

case "${1:-all}" in
    client) client ;;
    native) native ;;
    all) client; native ;;
    *) echo "Usage: sh tools/check.sh [client|native|all]" >&2; exit 2 ;;
esac
