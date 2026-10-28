#!/bin/sh
# Run retained suites without downloads or a parent workspace dependency.
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$root"
ulimit -v 8388608
client_scratch=
native_scratch=
cleanup() {
    if [ -n "$client_scratch" ]; then rm -r -- "$client_scratch"; fi
    if [ -n "$native_scratch" ]; then rm -r -- "$native_scratch"; fi
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM

need() {
    command -v "$1" >/dev/null 2>&1 || { echo "Missing dependency: $1" >&2; exit 1; }
}

client() {
    for dependency in emacs python3 cat true mktemp cp; do need "$dependency"; done
    client_scratch=$(mktemp -d)
    cp explorer/sumi-explorer.el tests/sumi-explorer-client-test.el tests/sumi-explorer-integration-test.el "$client_scratch/"
    emacs -Q --batch -L explorer --eval '(setq byte-compile-error-on-warn t)' \
        -f batch-byte-compile "$client_scratch/sumi-explorer.el" "$client_scratch/sumi-explorer-client-test.el" "$client_scratch/sumi-explorer-integration-test.el"
    emacs -Q --batch -L explorer -l tests/sumi-explorer-client-test.el -f ert-run-tests-batch-and-exit
}

native() {
    # Focused developer selectors must never reduce the public acceptance suite.
    unset SUMI_EXPLORER_TEST_SELECTOR
    for dependency in emacs python3 xvfb-run xdotool xwininfo import xclip pkg-config dbus-run-session nautilus mktemp mkdir; do need "$dependency"; done
    need "${CC:-cc}"
    if [ -n "${NELISP_REPO_ROOT:-}" ]; then
        compiler=$NELISP_REPO_ROOT
    elif [ -d "$root/target/deps/nelisp" ]; then
        compiler="$root/target/deps/nelisp"
    else
        compiler="$root/../nelisp"
    fi
    case "$compiler" in /*) ;; *) compiler="$root/$compiler" ;; esac
    if [ ! -f "$compiler/lisp/nelisp-aot-compiler.el" ] && [ ! -f "$compiler/src/nelisp-aot-compiler.el" ]; then
        echo "Missing NeLisp AOT compiler: $compiler (set NELISP_REPO_ROOT to an existing checkout)" >&2
        exit 1
    fi
    NELISP_REPO_ROOT=$(CDPATH= cd -- "$compiler" && pwd)
    export NELISP_REPO_ROOT
    binary=${SUMI_EXPLORER_TEST_BIN:-"$root/target/sumi-explorer"}
    case "$binary" in /*) ;; *) binary="$root/$binary" ;; esac
    [ -x "$binary" ] || { echo "Missing executable: $binary (build first)" >&2; exit 1; }
    export SUMI_EXPLORER_TEST_BIN="$binary"
    native_scratch=$(mktemp -d)
    native_scratch=$(CDPATH= cd -- "$native_scratch" && pwd)
    mkdir "$native_scratch/config"
    XDG_CONFIG_HOME="$native_scratch/config"
    export XDG_CONFIG_HOME
    xvfb-run -a python3 tests/explorer-gui-test.py --binary "$binary" --group all
    xvfb-run -a dbus-run-session -- python3 tests/explorer-dnd-test.py --binary "$binary"
    xvfb-run -a emacs -Q --batch -L explorer -l tests/sumi-explorer-integration-test.el
    xvfb-run -a python3 tests/explorer-operations-test.py --binary "$binary"
    xvfb-run -a dbus-run-session -- python3 tests/explorer-batches-test.py --binary "$binary"
    xvfb-run -a dbus-run-session -- python3 tests/explorer-workspace-test.py --binary "$binary"
    xvfb-run -a dbus-run-session -- python3 tests/explorer-everyday-test.py --binary "$binary"
}

case "${1:-all}" in
    client) client ;;
    native) native ;;
    all) client; native ;;
    *) echo "Usage: sh tools/check.sh [client|native|all]" >&2; exit 2 ;;
esac
