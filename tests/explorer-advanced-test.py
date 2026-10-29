#!/usr/bin/env python3
"""Real ZIP/Undo engine acceptance; run inside Xvfb with the 8 GiB/heavy guard."""
import argparse
import configparser
import importlib.util
import json
import os
from pathlib import Path
import resource
import shlex
import shutil
import signal
import stat
import struct
import subprocess
import tempfile
import time
import zipfile


def build_aot_fixture(fixture, artifacts, compiler, extra_source=None):
    """Compile the actual full DSL and accepted native bridge with public flags."""
    obj, exe, driver = (artifacts / name for name in (fixture.stem + ".o", fixture.stem, fixture.stem + "-build.el"))
    platform = Path(__file__).resolve().parents[1] / "explorer/platform.c"
    platform_obj = artifacts / (fixture.stem + "-platform.o")
    quote = lambda value: json.dumps(str(value), ensure_ascii=False)
    driver.write_text(f'''(add-to-list 'load-path (expand-file-name "lisp" {quote(compiler)}))
(add-to-list 'load-path (expand-file-name "src" {quote(compiler)}))
(require 'nelisp-aot-compiler)
(with-temp-buffer (insert-file-contents {quote(fixture)}) (goto-char (point-min))
 (nelisp-aot-compile-to-object (read (current-buffer)) {quote(obj)} :format 'elf :arch (if (string-prefix-p "aarch64" system-configuration) 'aarch64 'x86_64)))
''')
    with (artifacts / (fixture.stem + "-build.log")).open("wb") as log:
        subprocess.run(["emacs", "-Q", "--batch", "-l", str(driver)], check=True, timeout=600, stdout=log, stderr=subprocess.STDOUT)
        cc = os.environ.get("CC", "cc")
        flags = shlex.split(subprocess.check_output(["pkg-config", "--cflags", "gtk4", "libarchive"], text=True))
        libs = shlex.split(subprocess.check_output(["pkg-config", "--libs", "gtk4", "libarchive"], text=True))
        subprocess.run([cc, "-std=c11", "-Wall", "-Wextra", *flags, "-c", str(platform), "-o", str(platform_obj)], check=True, timeout=60, stdout=log, stderr=subprocess.STDOUT)
        extra_objects = []
        if extra_source is not None:
            extra_obj = artifacts / (fixture.stem + "-extra.o")
            subprocess.run([cc, "-std=c11", "-Wall", "-Wextra", *flags, "-c", str(extra_source), "-o", str(extra_obj)], check=True, timeout=60, stdout=log, stderr=subprocess.STDOUT)
            extra_objects.append(str(extra_obj))
        subprocess.run([cc, str(obj), str(platform_obj), *extra_objects, "-o", str(exe), *libs], check=True, timeout=60, stdout=log, stderr=subprocess.STDOUT)
    return exe


def zip_provider_units(args):
    """Exercise opaque Windows-provider glue on Linux, not the Win32 runtime."""
    artifacts = (args.artifacts or args.binary.resolve().parent / "advanced-evidence").resolve() / "zip-provider"
    artifacts.mkdir(parents=True, exist_ok=True)
    source = args.unit_source or Path(__file__).resolve().parents[1] / "explorer/sumi-explorer-native.el"
    text = source.read_text()
    for name in ("path_query", "open_source", "new_snapshot", "io_read", "io_write_all", "io_seek", "io_query", "io_close"):
        text = text.replace("extern-call sumi_explorer_zip_" + name, "extern-call sumi_test_zip_" + name)
    text = text.replace("extern-call sumi_explorer_platform_is_windows", "extern-call sumi_test_zip_windows")
    stub = artifacts / "provider.c"
    stub.write_text(r'''
#include <gio/gio.h>
#include <string.h>
typedef struct { guint64 magic; gint64 position; gboolean snapshot; GByteArray *bytes; } TestIO;
static guint64 *job; static unsigned opens, closes; static gboolean failed;
static const guint64 magic = G_GUINT64_CONSTANT(0x934a287a5591);
int sumi_test_zip_windows(void) { return 1; }
void sumi_test_zip_job(void *value) { job = value; }
static gboolean valid(TestIO *io, GCancellable *cancel, GError **error) {
  g_assert(io && io->magic == magic);
  return !g_cancellable_set_error_if_cancelled(cancel, error);
}
static TestIO *new_io(gboolean snapshot) {
  TestIO *io = g_new0(TestIO, 1); io->magic = magic; io->snapshot = snapshot;
  io->bytes = g_byte_array_new(); opens++;
  if (!snapshot) { g_byte_array_set_size(io->bytes, 150003); for (guint i=0;i<io->bytes->len;i++) io->bytes->data[i] = i % 251; }
  return io;
}
GFileInfo *sumi_test_zip_path_query(const char *path, GFileQueryInfoFlags flags, GCancellable *cancel, GError **error) {
  (void)flags; if (g_cancellable_set_error_if_cancelled(cancel,error)) return NULL;
  g_assert(g_path_is_absolute(path)); GFileInfo *info = g_file_info_new();
  g_file_info_set_attribute_string(info,"id::filesystem","native-volume");
  g_file_info_set_attribute_string(info,"id::file","native-full-object");
  g_file_info_set_file_type(info,G_FILE_TYPE_REGULAR); g_file_info_set_size(info,150003);
  g_file_info_set_attribute_string(info,"etag::value","native-full-metadata"); return info;
}
TestIO *sumi_test_zip_open_source(const char *path, GCancellable *cancel, GError **error) {
  g_assert(g_path_is_absolute(path)); return g_cancellable_set_error_if_cancelled(cancel,error) ? NULL : new_io(FALSE);
}
TestIO *sumi_test_zip_new_snapshot(GCancellable *cancel, GError **error) {
  return g_cancellable_set_error_if_cancelled(cancel,error) ? NULL : new_io(TRUE);
}
gssize sumi_test_zip_io_read(TestIO *io, void *buffer, gsize size, GCancellable *cancel, GError **error) {
  if (!valid(io,cancel,error)) return -1;
  if (g_getenv("SUMI_PROVIDER_READ_ERROR")) { g_set_error_literal(error,G_IO_ERROR,G_IO_ERROR_FAILED,"injected read"); return -1; }
  gsize count = MIN(MIN(size,8191),io->bytes->len-io->position);
  memcpy(buffer,io->bytes->data+io->position,count); io->position += count; return count;
}
gboolean sumi_test_zip_io_write_all(TestIO *io,const void *buffer,gsize size,GCancellable *cancel,GError **error) {
  if (!valid(io,cancel,error)) return FALSE;
  g_assert(io->snapshot); g_byte_array_append(io->bytes,buffer,size); io->position += size; return TRUE;
}
gint64 sumi_test_zip_io_seek(TestIO *io,gint64 offset,GSeekType whence,GCancellable *cancel,GError **error) {
  if (!valid(io,cancel,error)) return -1;
  gint64 next = offset + (whence == G_SEEK_CUR ? io->position : whence == G_SEEK_END ? io->bytes->len : 0);
  g_assert(next >= 0 && next <= io->bytes->len); return io->position = next;
}
GFileInfo *sumi_test_zip_io_query(TestIO *io,GCancellable *cancel,GError **error) {
  return valid(io,cancel,error) ? sumi_test_zip_path_query("/native",0,cancel,error) : NULL;
}
gboolean sumi_test_zip_io_close(TestIO *io,GError **error) {
  g_assert(io && io->magic == magic);
  if (io->snapshot) { g_assert(job[26] == 0 && job[16] == 0); }
  io->magic = 0; g_byte_array_unref(io->bytes); g_free(io); closes++;
  if (g_getenv("SUMI_PROVIDER_CLOSE_ERROR")) { if (error) g_set_error_literal(error,G_IO_ERROR,G_IO_ERROR_FAILED,"injected close"); return FALSE; }
  return TRUE;
}
int sumi_test_zip_verify(void *data,gsize size) {
  const guint8 *bytes=data; for(gsize i=0;i<size;i++) if(bytes[i] != i%251) failed=TRUE;
  return !failed && opens == closes;
}
''')
    main = r'''
 (defun main (argc argv)
   (let ((job (extern-call g_malloc0 320)) (file 0) (input 0) (snapshot 0) (buffer (extern-call g_malloc 150004))
         (info 0) (before 0) (after 0) (good 1) (error 0))
    (seq
     (extern-call sumi_test_zip_job job) (ptr-write-u64 job 24 (extern-call g_cancellable_new))
     (setq file (extern-call g_file_new_for_path (ptr-read-u64 argv 8)))
     (setq info (sumi_explorer_zip_path_info job file (data-addr zip_info_attrs) 1))
     (setq before (sumi_explorer_zip_fingerprint job info)) (extern-call g_object_unref info)
     (setq input (sumi_explorer_zip_source_open job file))
     (setq info (sumi_explorer_zip_input_info job input))
     (setq after (sumi_explorer_zip_fingerprint job info)) (extern-call g_object_unref info)
     (setq good (and before after (sumi_explorer_same before after)))
     (if (sumi_explorer_zip_input_read_all job input buffer 150004)
       (seq
        (setq good (and good (= (ptr-read-u64 job 152) 150003)))
        (setq snapshot (extern-call sumi_test_zip_new_snapshot (ptr-read-u64 job 24) (+ job 32)))
        (ptr-write-u64 job 208 snapshot) (ptr-write-u64 job 128 snapshot)
        (setq good (and good (sumi_explorer_zip_snapshot_write job snapshot buffer 150003)
          (= (sumi_explorer_zip_input_seek job snapshot -3 2) 150000)
          (= (sumi_explorer_zip_input_seek job snapshot -1 0) 149999)
          (= (sumi_explorer_zip_input_seek job snapshot 0 1) 0)
          (sumi_explorer_zip_input_read_all job snapshot buffer 150004) (= (ptr-read-u64 job 152) 150003)))
        (setq error (ptr-read-u64 job 32))
        (sumi_explorer_job_fail job (data-addr zip_failure)) (setq error (ptr-read-u64 job 32))
        (sumi_explorer_zip_snapshot_close job)
        (setq good (and good (= error (ptr-read-u64 job 32)) (= (ptr-read-u64 job 208) 0) (= (ptr-read-u64 job 128) 0)))
        (if (extern-call g_getenv (data-addr test_close)) (setq good (and good (ptr-read-u64 job 248))))
        (extern-call g_error_free (ptr-read-u64 job 32)) (ptr-write-u64 job 32 0))
       (seq (setq good (and good (ptr-read-u64 job 32)))
            (extern-call g_error_free (ptr-read-u64 job 32)) (ptr-write-u64 job 32 0)))
     (sumi_explorer_zip_input_close job input)
     (if (ptr-read-u64 job 32) (seq (extern-call g_error_free (ptr-read-u64 job 32)) (ptr-write-u64 job 32 0)))
     (extern-call g_cancellable_cancel (ptr-read-u64 job 24))
     (setq good (and good (= (sumi_explorer_zip_source_open job file) 0) (ptr-read-u64 job 32)))
     (extern-call g_error_free (ptr-read-u64 job 32))
     (setq good (and good (extern-call sumi_test_zip_verify buffer (if snapshot 150003 0))))
     (extern-call g_free before) (extern-call g_free after) (extern-call g_free buffer)
     (extern-call g_free (ptr-read-u64 job 248)) (extern-call g_object_unref (ptr-read-u64 job 24))
     (extern-call g_object_unref file) (extern-call g_free job) (if good 0 1))))
)
'''
    fixture = artifacts / "provider.el"
    fixture.write_text(text[:text.index(" (defun main ")] + '\n (data-blob test_close "SUMI_PROVIDER_CLOSE_ERROR\\0" rodata)\n' + main)
    binary = build_aot_fixture(fixture, artifacts, Path(os.environ["NELISP_REPO_ROOT"]).resolve(), extra_source=stub)
    with (artifacts / "provider.log").open("wb") as log:
        for mode in ({}, {"SUMI_PROVIDER_READ_ERROR": "1"}, {"SUMI_PROVIDER_CLOSE_ERROR": "1"}):
            subprocess.run([str(binary), str(artifacts / "source")], env=dict(os.environ, G_DEBUG="fatal-warnings", **mode),
                           check=True, timeout=30, stdout=log, stderr=subprocess.STDOUT)
    print("PASS: opaque ZIP provider glue (3 controls; no Windows runtime claim)")


def audit_units(args):
    """Exercise actual AOT cleanup/extraction helpers at deterministic race points."""
    artifacts = (args.artifacts or args.binary.resolve().parent / "advanced-evidence").resolve()
    artifacts.mkdir(parents=True, exist_ok=True)
    compiler = Path(os.environ["NELISP_REPO_ROOT"]).resolve()
    source = args.unit_source or Path(__file__).resolve().parents[1] / "explorer/sumi-explorer-native.el"
    text = source.read_text()
    start = text.index(" (defun main ")
    # main is the last definition in the outer seq; retain only that final close.
    main = r'''
 (data-blob sumi_explorer_unit_member "entry\0" rodata)
 (defun main (argc argv)
   (let ((job (extern-call g_malloc0 264)) (source 0) (root 0) (foreign 0) (path 0)
         (info 0) (identity 0) (good 1) (mode (ptr-read-u8 (ptr-read-u64 argv 24) 0)))
     (seq
      (ptr-write-u64 job 0 10)
      (ptr-write-u64 job 8 (extern-call g_strdup (ptr-read-u64 argv 8)))
      (ptr-write-u64 job 16 (extern-call g_strdup (ptr-read-u64 argv 16)))
      (ptr-write-u64 job 24 (extern-call g_cancellable_new))
      (ptr-write-u64 job 80 (extern-call g_ptr_array_new_with_free_func (addr-of sumi_explorer_zip_entry_free)))
      (ptr-write-u64 job 64 (extern-call g_hash_table_new_full (addr-of sumi_explorer_string_hash) (addr-of sumi_explorer_string_equal) (addr-of sumi_explorer_free_path) 0))
      (ptr-write-u64 job 160 (extern-call g_hash_table_new_full (addr-of sumi_explorer_string_hash) (addr-of sumi_explorer_string_equal) (addr-of sumi_explorer_free_path) 0))
      (ptr-write-u64 job 176 (extern-call g_hash_table_new_full (addr-of sumi_explorer_string_hash) (addr-of sumi_explorer_string_equal) (addr-of sumi_explorer_free_path) (addr-of sumi_explorer_free_path)))
      (ptr-write-u64 job 192 (extern-call g_hash_table_new_full (addr-of sumi_explorer_string_hash) (addr-of sumi_explorer_string_equal) (addr-of sumi_explorer_free_path) 0))
      (ptr-write-u64 job 232 (extern-call g_hash_table_new_full (addr-of sumi_explorer_string_hash) (addr-of sumi_explorer_string_equal) (addr-of sumi_explorer_free_path) 0))
      (ptr-write-u64 job 256 (extern-call g_hash_table_new_full (addr-of sumi_explorer_string_hash) (addr-of sumi_explorer_string_equal) (addr-of sumi_explorer_free_path) (addr-of sumi_explorer_free_path)))
      (ptr-write-u64 job 136 (extern-call g_malloc 65536)) (ptr-write-u64 job 200 (extern-call g_malloc 65536))
      (setq source (extern-call g_file_new_for_path (ptr-read-u64 job 8)))
      (setq good (sumi_explorer_zip_snapshot job source)) (extern-call g_object_unref source)
      (if (= mode 117)
        (seq
         (setq good (and good (ptr-read-u64 job 240)))
         (setq foreign (extern-call g_object_ref (ptr-read-u64 job 216)))
         (setq path (extern-call g_file_get_path foreign))
         (extern-call g_file_set_contents path (data-addr zip_con) 3 0)
         (extern-call g_free path))
        (seq
         (if good (setq good (sumi_explorer_zip_raw job)))
         (if good (setq good (sumi_explorer_zip_read_pass job 0)))
         (setq root (extern-call g_file_new_for_path (ptr-read-u64 job 16)))
         (if good (setq good (extern-call g_file_make_directory root 0 0)))
         (if good
           (seq
            (setq info (extern-call g_file_query_info root (data-addr id_attrs) 1 0 0))
            (setq identity (sumi_explorer_identity info job)) (extern-call g_object_unref info)
            (extern-call g_hash_table_insert (ptr-read-u64 job 256) (extern-call g_strdup (ptr-read-u64 job 16)) identity)
            (setq foreign (extern-call g_file_get_child root (data-addr sumi_explorer_unit_member)))
            (if (= mode 100) (extern-call g_file_make_directory foreign 0 0)
              (seq (setq path (extern-call g_file_get_path foreign))
                   (extern-call g_file_set_contents path (data-addr zip_con) 3 0) (extern-call g_free path)))
            (setq good (= (sumi_explorer_zip_read_pass job 1) 0))))
         (extern-call g_object_unref root)))
      (sumi_explorer_zip_job_free job)
      (setq good (and good (extern-call g_file_query_exists foreign 0)))
      (extern-call g_object_unref foreign)
      (if good 0 1))))
)
'''
    fixture = artifacts / "zip-audit-unit.el"
    fixture.write_text(text[:start] + main)
    exe = build_aot_fixture(fixture, artifacts, compiler)
    with tempfile.TemporaryDirectory(prefix="sumi ZIP helper ") as temporary:
        base = Path(temporary)
        env = dict(os.environ, TMPDIR=str(base), G_DEBUG="fatal-warnings")
        for mode in ("u", "f", "d"):
            archive = base / f"{mode}.zip"
            with zipfile.ZipFile(archive, "w") as saved:
                saved.writestr("entry/" if mode == "d" else "entry", b"")
            subprocess.run([str(exe), str(archive), str(base / mode), mode], check=True, timeout=30, env=env)
            if mode == "f":
                assert (base / mode / "entry").read_bytes() == b"con"
            print({"f": "PASS actual extraction rejects a foreign empty-member file without overwrite",
                   "d": "PASS actual extraction rejects a foreign directory without merging",
                   "u": "PASS actual job destruction preserves replacement at an already-unlinked snapshot name"}[mode], flush=True)


def run(args):
    spec = importlib.util.spec_from_file_location("gui", Path(__file__).with_name("explorer-gui-test.py"))
    gui = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gui)
    artifacts = (args.artifacts or args.binary.resolve().parent / "advanced-evidence").resolve()
    artifacts.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="sumi ZIP 日本語 ") as temporary:
        base = Path(temporary)
        root, session_path = base / "root", base / "session"
        root.mkdir()
        session_path.mkdir()
        snapshot_tmp = base / "private snapshot temp"
        snapshot_tmp.mkdir()
        previous_tmpdir = os.environ.get("TMPDIR")
        os.environ["TMPDIR"] = str(snapshot_tmp)
        tree = root / "nested 日本語"
        tree.mkdir()
        (tree / "line\nname.txt").write_bytes(b"nested archive bytes")
        (tree / "compressible.bin").write_bytes(b"compressible archive contents\n" * 40000)
        regular = root / "spaces and ;.txt"
        regular.write_bytes(b"frozen selected bytes")
        session = gui.Session(args.binary.resolve(), root, session_path)
        try:
            def command(name, source="", destination=None, paths=None, cancel=False, cancel_running=False):
                session.serial += 1
                serial = session.serial
                text = f"[Explorer]\nversion=1\nrequest={serial}\ncommand={name}\npath={gui.escape(str(source))}\n"
                if paths is not None:
                    text += f"count={len(paths)}\n" + "".join(f"path{i}={gui.escape(str(path))}\n" for i, path in enumerate(paths))
                if destination is not None:
                    text += f"destination={gui.escape(str(destination))}\n"
                if cancel:
                    (session_path / "cancel.ini").write_text(f"[Explorer]\nversion=1\nrequest={serial}\n")
                temporary_request = session_path / "request.tmp"
                temporary_request.write_text(text)
                temporary_request.replace(session_path / "request.ini")
                if cancel_running:
                    end = time.monotonic() + 15
                    while not Path(destination).exists() and time.monotonic() < end:
                        time.sleep(0.005)
                    assert Path(destination).exists(), "Archive output must exist before in-flight cancellation"
                    assert session.state(response=True).get("request") != str(serial)
                    (session_path / "cancel.ini").write_text(f"[Explorer]\nversion=1\nrequest={serial}\n")
                return session.wait(lambda state: state["request"] == str(serial), seconds=60, response=True)

            legacy = base / "retained copy.txt"
            assert command("copy", regular, legacy)["status"] == "ok"
            assert legacy.read_bytes() == regular.read_bytes()
            print("PASS retained single-operation GTask dispatch and completion still copy real bytes", flush=True)
            archive = base / "created 日本語.zip"
            result = command("zip-create", destination=archive, paths=[regular, tree])
            assert result["status"] == "ok", result
            with zipfile.ZipFile(archive) as saved:
                assert saved.read(regular.name) == regular.read_bytes()
                assert saved.read(tree.name + "/line\nname.txt") == b"nested archive bytes"
                assert saved.read(tree.name + "/compressible.bin") == (tree / "compressible.bin").read_bytes()
                assert saved.getinfo(tree.name + "/compressible.bin").compress_type == zipfile.ZIP_DEFLATED
                raw = archive.read_bytes()
                central = struct.unpack_from("<I", raw, raw.rfind(b"PK\x05\x06") + 16)[0]
                for info in saved.infolist():
                    assert raw[central:central + 4] == b"PK\x01\x02"
                    flags = struct.unpack_from("<H", raw, central + 8)[0]
                    length, extra, comment = struct.unpack_from("<3H", raw, central + 28)
                    assert raw[central + 46:central + 46 + length] == info.filename.encode("utf-8")
                    local = info.header_offset
                    assert raw[local:local + 4] == b"PK\x03\x04"
                    local_flags = struct.unpack_from("<H", raw, local + 6)[0]
                    local_length = struct.unpack_from("<H", raw, local + 26)[0]
                    assert raw[local + 30:local + 30 + local_length] == info.filename.encode("utf-8")
                    if any(ord(character) > 127 for character in info.filename):
                        assert flags & 0x800 and local_flags & 0x800
                    central += 46 + length + extra + comment
            assert archive.stat().st_size < (tree / "compressible.bin").stat().st_size // 10
            assert regular.exists() and tree.exists()
            shutil.copyfile(archive, artifacts / "created.zip")
            print("PASS frozen indexed ZIP creation preserves Unicode tree and source bytes", flush=True)
            extracted = base / "extracted 日本語"
            result = command("zip-extract", archive, extracted)
            assert result["status"] == "ok", result
            assert (extracted / tree.name / "line\nname.txt").read_bytes() == b"nested archive bytes"
            assert (extracted / regular.name).read_bytes() == regular.read_bytes()
            assert (extracted / tree.name / "compressible.bin").read_bytes() == (tree / "compressible.bin").read_bytes()
            alias_output = str(base) + "/./noncanonical destination/"
            alias_result = command("zip-extract", archive, alias_output)
            assert alias_result["status"] == "ok", {"response": alias_result, "root_created": Path(alias_output).is_dir()}
            assert (Path(alias_output) / regular.name).read_bytes() == regular.read_bytes()
            print("PASS real libarchive ZIP extraction creates an unused directory", flush=True)
            prior = archive.read_bytes()
            assert command("zip-create", destination=archive, paths=[regular])["status"] == "error"
            assert archive.read_bytes() == prior
            assert command("zip-extract", archive, extracted)["status"] == "error"
            print("PASS existing archive and directory collisions never overwrite or merge", flush=True)
            cases = [
                [("../escape", b"bad", None)], [("/absolute", b"bad", None)],
                [("C:drive", b"bad", None)], [("a\\b", b"bad", None)],
                [("NUL.txt", b"bad", None)], [("trailing. ", b"bad", None)],
                [("COM¹.txt", b"bad", None)], [("lpt²", b"bad", None)], [("CoM³.bin", b"bad", None)],
                [("A", b"one", None), ("a", b"two", None)],
                [("e\u0301", b"one", None), ("é", b"two", None)],
                [("tree", b"one", None), ("tree/child", b"two", None)],
                [("link", b"target", stat.S_IFLNK | 0o777)],
                [("fifo", b"", stat.S_IFIFO | 0o600)],
            ]
            for index, entries in enumerate(cases):
                hostile = base / f"hostile-{index}.zip"
                with zipfile.ZipFile(hostile, "w") as saved:
                    for name, data, mode in entries:
                        info = zipfile.ZipInfo(name)
                        if mode is not None:
                            info.create_system = 3
                            info.external_attr = mode << 16
                        saved.writestr(info, data)
                output = base / f"rejected-{index}"
                result = command("zip-extract", hostile, output)
                assert result["status"] == "error" and not output.exists(), (index, result)
            assert not (base / "escape").exists()
            print("PASS traversal, portable aliases, conflicting paths, links and special entries reject before output", flush=True)
            ordinary = base / "ordinary.zip"
            with zipfile.ZipFile(ordinary, "w", compression=zipfile.ZIP_DEFLATED) as saved:
                saved.writestr("safe", b"CRC and metadata fixture")
            raw = ordinary.read_bytes()
            central = raw.index(b"PK\x01\x02")
            mutations = []
            changed = bytearray(raw)
            changed[30] = 0
            changed[central + 46] = 0
            mutations.append(changed)
            changed = bytearray(raw)
            changed[30:34] = b"evil"
            mutations.append(changed)
            changed = bytearray(raw)
            struct.pack_into("<H", changed, 6, 1)
            struct.pack_into("<H", changed, central + 8, 1)
            mutations.append(changed)
            changed = bytearray(raw)
            struct.pack_into("<I", changed, central + 24, 1073741825)
            mutations.append(changed)
            mutations.append(raw[:-7])
            changed = bytearray(raw)
            changed[34] ^= 0x7f
            mutations.append(changed)
            for index, payload in enumerate(mutations):
                damaged = base / f"damaged-{index}.zip"
                damaged.write_bytes(payload)
                output = base / f"damaged-output-{index}"
                result = command("zip-extract", damaged, output)
                assert result["status"] == "error" and not output.exists(), (index, result)
            print("PASS raw NUL/name mismatches, encryption, oversized metadata and truncated ZIP reject before output", flush=True)
            zip64 = base / "unsupported ZIP64.zip"
            with zipfile.ZipFile(zip64, "w") as saved:
                with saved.open("entry", "w", force_zip64=True) as entry:
                    entry.write(b"ZIP64 local header")
            output = base / "ZIP64 output"
            result = command("zip-extract", zip64, output)
            assert result["status"] == "error" and "Unsupported ZIP variant" in result["message"] and not output.exists(), result
            empty = base / "empty.zip"
            with zipfile.ZipFile(empty, "w"):
                pass
            empty_output = base / "empty ZIP output"
            result = command("zip-extract", empty, empty_output)
            assert result["status"] == "ok" and empty_output.is_dir() and not list(empty_output.iterdir()), result
            print("PASS unsupported ZIP64 variant is explicit and never creates output", flush=True)
            for label, names in (("depth", ["/".join(["a"] * 65)]), ("names", [f"{i:04d}-" + "a" * 195 for i in range(4000)])):
                bounded = base / f"limit-{label}.zip"
                with zipfile.ZipFile(bounded, "w") as saved:
                    for name in names:
                        saved.writestr(name, b"")
                output = base / f"limit-{label}-output"
                assert command("zip-extract", bounded, output)["status"] == "error"
                assert not output.exists()
            print("PASS nesting and aggregate encoded-name bounds reject before creating output", flush=True)
            linked = root / "link"
            linked.symlink_to(regular.name)
            rejected = base / "no-links.zip"
            assert command("zip-create", destination=rejected, paths=[regular, linked])["status"] == "error"
            assert not rejected.exists()
            descendant = tree / "self.zip"
            assert command("zip-create", destination=descendant, paths=[tree])["status"] == "error"
            assert not descendant.exists()
            print("PASS ZIP creation preflight rejects links and descendant output before mutation", flush=True)
            cancelled = base / "cancelled.zip"
            assert command("zip-create", destination=cancelled, paths=[regular], cancel=True)["status"] == "error"
            assert not cancelled.exists()
            assert session.command("refresh")["status"] == "ok"
            print("PASS matching pre-start cancellation leaves no archive and next request recovers", flush=True)
            large = root / "bounded sparse source"
            with large.open("wb") as stream:
                stream.truncate(512 * 1024 * 1024)
            partial = base / "partial.zip"
            result = command("zip-create", destination=partial, paths=[large], cancel_running=True)
            assert result["status"] == "error" and str(partial) in result["message"], result
            assert large.stat().st_size == 512 * 1024 * 1024
            assert session.command("refresh")["status"] == "ok"
            assert not list(snapshot_tmp.iterdir()), list(snapshot_tmp.iterdir())
            print("PASS real in-flight ZIP cancellation reports exact partial output and cleans private snapshots", flush=True)
        finally:
            session.stop()
            shutil.copyfile(session_path / "host.log", artifacts / "host.log")
            if previous_tmpdir is None:
                os.environ.pop("TMPDIR", None)
            else:
                os.environ["TMPDIR"] = previous_tmpdir


def undo_fault_unit(args):
    """Actual Undo worker with deterministic native move/delete failure injection."""
    artifacts = (args.artifacts or args.binary.resolve().parent / "advanced-evidence").resolve(); artifacts.mkdir(parents=True, exist_ok=True)
    source = Path(__file__).resolve().parents[1] / "explorer/sumi-explorer-native.el"
    text = source.read_text(); start = text.index(" (defun main ")
    prefix = text[:start].replace("(extern-call g_file_move ", "(sumi_explorer_unit_move ").replace("(extern-call g_file_delete ", "(sumi_explorer_unit_delete ")
    main = r'''
 (defun sumi_explorer_unit_move (source target flags cancellable progress data error)
   (seq (if error (ptr-write-u64 error 0 (extern-call g_error_new_literal (extern-call g_io_error_quark) 0 (data-addr undo_failure)))) 0))
 (defun sumi_explorer_unit_delete (file cancellable error) 0)
 (data-blob sumi_explorer_unit_badmode "00000\0" rodata)
 (defun main (argc argv)
   (let ((job (extern-call g_malloc0 320)) (store (extern-call g_malloc0 24))
         (group (extern-call g_malloc0 32)) (item (extern-call g_malloc0 104)) (file 0)
         (probe (sumi_explorer_undo_probe)) (good 0) (text 0))
     (seq
      (setq file (extern-call g_file_new_for_path (ptr-read-u64 argv 8)))
      (ptr-write-u64 store 0 (extern-call g_ptr_array_new_with_free_func (addr-of sumi_explorer_undo_group_free)))
      (ptr-write-u64 group 0 1) (ptr-write-u64 group 8 1)
      (ptr-write-u64 group 16 (extern-call g_ptr_array_new_with_free_func (addr-of sumi_explorer_undo_item_free)))
      (ptr-write-u64 item 0 (extern-call g_strdup (ptr-read-u64 argv 8))) (ptr-write-u64 item 8 (extern-call g_strdup (ptr-read-u64 argv 8)))
      (ptr-write-u64 item 24 (sumi_explorer_undo_parent file probe))
      (ptr-write-u64 item 32 (extern-call g_strdup (ptr-read-u64 item 24)))
      (ptr-write-u64 item 16 (sumi_explorer_undo_fingerprint probe file))
      (extern-call g_ptr_array_add (ptr-read-u64 group 16) item) (extern-call g_ptr_array_add (ptr-read-u64 store 0) group)
      (ptr-write-u64 job 0 11) (ptr-write-u64 job 272 store) (ptr-write-u64 job 296 group)
      (ptr-write-u64 job 24 (extern-call g_cancellable_new))
      (sumi_explorer_undo_worker 0 0 job (ptr-read-u64 job 24))
      (setq text (ptr-read-u64 job 288))
      (setq good (and (= (sumi_explorer_octal_mode (data-addr sumi_explorer_unit_badmode)) -1)
                     (= (ptr-read-u64 job 48) 0) (= (ptr-read-u32 (ptr-read-u64 group 16) 8) 1)
                     text (extern-call strstr text (ptr-read-u64 argv 16)) (extern-call g_file_query_exists file 0)))
      (if text (extern-call puts text))
      (extern-call g_object_unref (ptr-read-u64 job 24)) (extern-call g_object_unref file)
      (sumi_explorer_undo_probe_free probe) (extern-call g_ptr_array_unref (ptr-read-u64 store 0))
      (extern-call g_free store) (sumi_explorer_job_free job) (if good 0 1))))
)
'''
    if args.raw_not_negative:
        prefix = prefix.replace("(= (or (= length 3) (= length 4)) 0)", "(not (or (= length 3) (= length 4)))")
    fixture = artifacts / "undo-fault-unit.el"; fixture.write_text(prefix + main)
    exe = build_aot_fixture(fixture, artifacts, Path(os.environ["NELISP_REPO_ROOT"]).resolve())
    parent = Path(tempfile.mkdtemp(prefix="retained-quarantine-", dir=artifacts))
    file = parent / "unchanged.txt"; file.write_bytes(b"unchanged object")
    with (artifacts / "undo-fault-unit.log").open("wb") as log:
        subprocess.run([str(exe), str(file), str(parent)], check=True, timeout=20, stdout=log, stderr=subprocess.STDOUT, env=dict(os.environ, G_DEBUG="fatal-warnings"))
    assert file.read_bytes() == b"unchanged object"
    residuals = list(parent.glob(".sumi-undo-*")); assert len(residuals) == 1 and not list(residuals[0].iterdir())
    assert str(residuals[0]) in (artifacts / "undo-fault-unit.log").read_text()
    print("PASS actual failed-detach/failed-empty-quarantine cleanup reports exact residual and retains history", flush=True)


def undo_cases(args):
    """Real session history, identity guards and opaque GIO Trash restoration."""
    spec = importlib.util.spec_from_file_location("gui", Path(__file__).with_name("explorer-gui-test.py"))
    gui = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gui)
    artifacts = (args.artifacts or args.binary.resolve().parent / "advanced-evidence").resolve()
    artifacts.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="sumi Undo 日本語 ") as temporary:
        base = Path(temporary)
        root, session_path = base / "root", base / "session"
        root.mkdir(); session_path.mkdir()
        previous = {key: os.environ.get(key) for key in ("XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS")}
        # Separate bus launchers on one display otherwise share an AT-SPI
        # socket path and the private launcher can replace the outer socket.
        for key in ("XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_RUNTIME_DIR"):
            path = base / key.lower(); path.mkdir(mode=0o700); os.environ[key] = str(path)
        bus = subprocess.check_output(["dbus-daemon", "--session", "--fork", "--print-address=1", "--print-pid=1"], text=True, timeout=10).splitlines()
        os.environ["DBUS_SESSION_BUS_ADDRESS"] = bus[0]
        session = gui.Session(args.binary.resolve(), root, session_path)
        try:
            def history():
                parser = configparser.ConfigParser(interpolation=None, strict=True)
                parser.read(session_path / "undo.ini")
                assert parser.sections() and parser.sections()[0] == "Undo", "A normal session must publish undo.ini"
                count = int(parser["Undo"]["count"])
                assert parser.sections() == ["Undo"] + [f"Undo{i}" for i in range(count)]
                return [dict(parser[f"Undo{i}"]) for i in range(count)]

            def operation(name, source="", destination=None, paths=None, cancel_after=None):
                session.serial += 1
                serial = session.serial
                text = f"[Explorer]\nversion=1\nrequest={serial}\ncommand={name}\npath={gui.escape(str(source))}\n"
                if destination is not None:
                    text += f"destination={gui.escape(str(destination))}\n"
                if paths is not None:
                    text += f"count={len(paths)}\n" + "".join(f"path{i}={gui.escape(str(path))}\n" for i, path in enumerate(paths))
                request = session_path / "request.tmp"; request.write_text(text); request.replace(session_path / "request.ini")
                if cancel_after is not None:
                    end = time.monotonic() + 30
                    while not cancel_after() and time.monotonic() < end:
                        time.sleep(0.002)
                    assert cancel_after(), "Expected actual filesystem progress before cancellation"
                    assert session.state(response=True).get("request") != str(serial)
                    (session_path / "cancel.ini").write_text(f"[Explorer]\nversion=1\nrequest={serial}\n")
                return session.wait(lambda state: state["request"] == str(serial), seconds=60, response=True)

            original = root / "original 日本語.txt"; original.write_bytes(b"undo identity bytes")
            output = root / "copied.txt"
            assert operation("copy", original, output)["status"] == "ok"
            first = history()[0]
            assert first["operation"] == "copy" and first["count"] == "1"
            assert operation("undo", str(int(first["id"]) + 1))["status"] == "error"
            assert output.exists() and history()[0] == first
            assert operation("undo", first["id"])["status"] == "ok"
            assert not output.exists() and original.read_bytes() == b"undo identity bytes" and not history()
            print("PASS real copy Undo removes only its own unchanged output and rejects stale group IDs", flush=True)
            assert operation("copy", original, output)["status"] == "ok"
            current = history()[0]
            saved = root / "saved original copy"; output.rename(saved); output.write_bytes(saved.read_bytes())
            assert operation("undo", current["id"])["status"] == "error"
            assert output.exists() and history()[0] == current
            output = root / "modified copied.txt"
            assert operation("copy", original, output)["status"] == "ok"
            current = history()[0]
            output.write_bytes(b"modified contents")
            assert operation("undo", current["id"])["status"] == "error"
            assert output.read_bytes() == b"modified contents" and history()[0] == current
            print("PASS same-byte replacement identity and changed content both retain unresolved Undo", flush=True)
            metadata_output = root / "metadata modified copy.txt"
            assert operation("copy", original, metadata_output)["status"] == "ok"
            current = history()[0]
            os.setxattr(metadata_output, b"user.sumi-external-change", b"external metadata")
            assert operation("undo", current["id"])["status"] == "error"
            assert metadata_output.read_bytes() == original.read_bytes() and history()[0] == current
            assert os.getxattr(metadata_output, b"user.sumi-external-change") == b"external metadata"
            print("PASS metadata-only xattr modification prevents destructive Undo with unchanged bytes and mode", flush=True)
            moved = root / "moved.txt"
            identity = original.stat().st_ino
            assert operation("move", original, moved)["status"] == "ok"
            current = history()[0]; original.write_bytes(b"foreign conflict")
            assert operation("undo", current["id"])["status"] == "error"
            assert original.read_bytes() == b"foreign conflict" and moved.exists()
            original.unlink()
            assert operation("undo", current["id"])["status"] == "ok"
            assert original.stat().st_ino == identity and not moved.exists()
            print("PASS move Undo never replaces an occupied original path and restores actual object identity", flush=True)
            os.setxattr(original, b"user.sumi-undo", b"preserved metadata")
            identity = original.stat().st_ino
            assert operation("trash", original)["status"] == "ok"
            current = history()[0]
            assert current["operation"] == "trash" and not original.exists()
            assert operation("undo", current["id"])["status"] == "ok"
            assert original.stat().st_ino == identity
            assert os.getxattr(original, b"user.sumi-undo") == b"preserved metadata"
            print("PASS GIO Trash Undo restores the verified backend object with inode and xattr retained", flush=True)
            folder = root / "new folder"
            assert operation("mkdir", folder)["status"] == "ok"
            current = history()[0]; assert current["operation"] == "mkdir"
            assert operation("undo", current["id"])["status"] == "ok" and not folder.exists()
            archive, extracted = root / "undo.zip", root / "unpacked"
            assert operation("zip-create", destination=archive, paths=[original])["status"] == "ok"
            created = history()[0]; assert created["operation"] == "zip-create"
            assert operation("zip-extract", archive, extracted)["status"] == "ok"
            unpacked = history()[0]; assert unpacked["operation"] == "zip-extract"
            assert operation("undo", unpacked["id"])["status"] == "ok" and not extracted.exists()
            assert operation("undo", created["id"])["status"] == "ok" and not archive.exists()
            print("PASS mkdir and ZIP creation/extraction Undo detach verified outputs and consume newest groups", flush=True)
            many = root / "many children"; many.mkdir()
            for i in range(10000):
                (many / f"child{i:05d}").write_bytes(b"bounded child")
            destination = base / "batch outputs"; destination.mkdir()
            assert operation("copy-many", destination=destination, paths=[original, many])["status"] == "ok"
            current = history()[0]; assert current["operation"] == "copy" and current["count"] == "2"
            copied = destination / original.name; copied.write_bytes(b"changed")
            assert operation("undo", current["id"])["status"] == "error"
            assert copied.exists() and (destination / many.name).is_dir() and history()[0] == current
            destination = base / "fresh cancellation outputs"; destination.mkdir()
            assert operation("copy-many", destination=destination, paths=[original, many])["status"] == "ok"
            current = history()[0]; copied = destination / original.name
            result = operation("undo", current["id"], cancel_after=lambda: not (destination / many.name).exists())
            assert result["status"] == "error" and "cleanup" in result["message"], result
            assert copied.exists() and history()[0]["id"] == current["id"] and history()[0]["count"] == "1"
            assert operation("undo", current["id"])["status"] == "ok" and not copied.exists()
            assert original.exists() and len(list(many.iterdir())) == 10000
            print("PASS whole-group preflight is mutation-free and cancelled cleanup consumes only actually detached items", flush=True)
            destination = base / "partially copied"; destination.mkdir()
            result = operation("copy-many", destination=destination, paths=[original, many], cancel_after=lambda: (destination / many.name).is_dir())
            assert result["status"] == "error", result
            current = history()[0]; assert current["operation"] == "copy" and current["count"] == "1"
            assert operation("undo", current["id"])["status"] == "ok"
            assert not (destination / original.name).exists() and (destination / many.name).exists()
            print("PASS partial-batch history records completed items without claiming a cancelled partial output", flush=True)
            previous_id = 0
            initial_count = len(history())
            for i in range(34):
                assert operation("mkdir", root / f"history-{i}")["status"] == "ok"
                groups = history(); assert int(groups[0]["id"]) > previous_id
                previous_id = int(groups[0]["id"])
                assert len(groups) == min(initial_count + i + 1, 32)
            assert operation("undo", groups[1]["id"])["status"] == "error"
            assert history() == groups
            print("PASS session history keeps 32 ordered groups with stable increasing IDs and rejects non-top IDs", flush=True)
        finally:
            session.stop()
            os.kill(int(bus[1]), signal.SIGTERM)
            shutil.copyfile(session_path / "host.log", artifacts / "undo-host.log")
            for key, value in previous.items():
                if value is None: os.environ.pop(key, None)
                else: os.environ[key] = value


def single_select_cases(args):
    """Exercise legacy single-select through actual protocol and public client."""
    spec = importlib.util.spec_from_file_location("gui", Path(__file__).with_name("explorer-gui-test.py"))
    gui = importlib.util.module_from_spec(spec); spec.loader.exec_module(gui)
    artifacts = (args.artifacts or args.binary.resolve().parent / "single-select-evidence").resolve()
    artifacts.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="sumi single selection ") as temporary:
        root = Path(temporary) / "root"; root.mkdir()
        session_path = Path(temporary) / "session"; session_path.mkdir()
        first = root / "A chosen 日本語"; first.write_bytes(b"chosen bytes")
        other = root / "B other"; other.write_bytes(b"other bytes")
        alias = str(root) + "/./" + first.name
        missing = str(root) + "/./missing"
        session = gui.Session(args.binary.resolve(), root, session_path)
        try:
            assert session.command("select", str(other))["selected"] == str(other)
            result = session.command("select", alias)
            assert result["status"] == "ok" and result["selected"] == str(first), result
            selected = configparser.ConfigParser(); selected.read(session_path / "selection.ini")
            assert selected["Selection"]["count"] == "1" and gui.unescape(selected["Selection"]["path0"]) == str(first)
            durable = (session_path / "response.ini").read_bytes()
            window = gui.xdo("search", "--onlyvisible", "--sync", "--name", "^Sumi Explorer$").splitlines()[-1]
            gui.xdo("windowfocus", "--sync", window)
            session.ui().row_click(other)
            session.wait(lambda state: state["selected"] == str(other))
            assert (session_path / "response.ini").read_bytes() == durable
            before = (session_path / "selection.ini").read_bytes()
            rejected = session.command("select", missing)
            assert rejected["status"] == "error" and rejected["selected"] == str(other), rejected
            assert (session_path / "selection.ini").read_bytes() == before
            assert first.read_bytes() == b"chosen bytes" and other.read_bytes() == b"other bytes"
        finally:
            session.stop(); shutil.copyfile(session_path / "host.log", artifacts / "single-select-host.log")
        driver = artifacts / "single-select-client.el"
        driver.write_text(r''';;; single-select-client.el --- Public API check -*- lexical-binding: t; -*-
(require 'sumi-explorer)
(unwind-protect
    (progn
      (sumi-explorer-start (getenv "SUMI_TEST_ROOT") (getenv "SUMI_EXPLORER_TEST_BIN"))
      (sumi-explorer-select (getenv "SUMI_TEST_ALIAS"))
      (unless (equal (sumi-explorer-selected-files) (list (getenv "SUMI_TEST_FILE")))
        (error "Public single-select did not publish the canonical row path"))
      (let ((failure (condition-case failure
                         (progn (sumi-explorer-select (getenv "SUMI_TEST_MISSING")) nil)
                       (error failure))))
        (unless failure (error "Public single-select accepted a missing row")))
      (unless (equal (sumi-explorer-selected-files) (list (getenv "SUMI_TEST_FILE")))
        (error "Rejected public single-select changed selection")))
  (sumi-explorer-stop t))
''')
        with (artifacts / "single-select-client.log").open("wb") as log:
            subprocess.run(["emacs", "-Q", "--batch", "-L", str(Path(__file__).resolve().parents[1] / "explorer"), "-l", str(driver)],
                           env=dict(os.environ, SUMI_TEST_ROOT=str(root), SUMI_TEST_ALIAS=alias, SUMI_TEST_FILE=str(first),
                                    SUMI_TEST_MISSING=missing, SUMI_EXPLORER_TEST_BIN=str(args.binary.resolve())),
                           check=True, timeout=60, stdout=log, stderr=subprocess.STDOUT)
    print("PASS legacy protocol and public-client single-select accept lexical aliases, publish canonical rows and preserve rollback/durable response", flush=True)


def native_path_units(args):
    """Check actual GFile row equality and the Windows-only rejection branch."""
    artifacts = (args.artifacts or args.binary.resolve().parent / "native-path-evidence").resolve()
    artifacts.mkdir(parents=True, exist_ok=True)
    source = args.unit_source or Path(__file__).resolve().parents[1] / "explorer/sumi-explorer-native.el"
    text = source.read_text(); prefix = text[:text.index(' (defun main ')]
    compiler = Path(os.environ["NELISP_REPO_ROOT"]).resolve()
    row_main = r'''
 (data-blob test_row_report "backend_equal=%d actual_match=%d\n\0" rodata)
 (defun main (argc argv)
   (let ((ctx (extern-call g_malloc0 24)) (list 0) (row 0) (file 0) (other 0) (path 0) (found 0) (equal 0))
     (seq
      (extern-call gtk_init)
      (setq list (extern-call gtk_list_box_new)) (extern-call g_object_ref_sink list)
      (ptr-write-u64 ctx 16 list) (setq row (extern-call gtk_list_box_row_new))
      (setq file (extern-call g_file_new_for_path (ptr-read-u64 argv 8)))
      (setq other (extern-call g_file_new_for_path (ptr-read-u64 argv 16)))
      (setq path (extern-call g_file_get_path file))
      (extern-call g_object_set_data_full row (data-addr identity) path (addr-of sumi_explorer_free_path))
      (extern-call gtk_list_box_append list row)
      (setq equal (extern-call g_file_equal file other))
      (setq found (sumi_explorer_find_row ctx (ptr-read-u64 argv 16)))
      (extern-call printf (data-addr test_row_report) equal (= found row))
      (extern-call g_object_unref file) (extern-call g_object_unref other)
      (extern-call g_object_unref list) (extern-call g_free ctx)
      (if (= (= found row) equal) 0 1))))
)
'''
    fixture = artifacts / "gfile-row.el"; fixture.write_text(prefix + row_main)
    binary = build_aot_fixture(fixture, artifacts, compiler)
    with tempfile.TemporaryDirectory(prefix="sumi row lexical ") as temporary:
        root = Path(temporary); original = root / "日本語 file"; original.write_bytes(b"row bytes")
        rows = [(str(original), str(root) + "/./" + original.name), (str(original), str(root / "different file")),
                ("C:/sumi/日本語.txt", "c:\\sumi\\日本語.txt")]
        with (artifacts / "gfile-row.log").open("wb") as log:
            for first, second in rows:
                subprocess.run([str(binary), first, second], check=True, timeout=30, stdout=log, stderr=subprocess.STDOUT)
    print("PASS real GTK row lookup follows GFile lexical equality and observes backend drive/separator semantics without folding", flush=True)

    fake = prefix.replace('(extern-call sumi_explorer_platform_is_windows)', '1')
    start = fake.index(' (defun sumi_explorer_publish '); end = fake.index('\n (defun ', start + 1)
    fake = fake[:start] + r'''
 (defun sumi_explorer_publish (ctx good msg)
   (seq (ptr-write-u64 ctx 736 msg) (ptr-write-u64 ctx 744 (+ (ptr-read-u64 ctx 744) 1)) 0))
''' + fake[end:]
    fake = fake.replace('(extern-call gtk_label_set_text ', '(sumi_test_label ').replace('(extern-call g_path_is_absolute ', '(sumi_test_forbidden_path ')
    diagnostic_main = r'''
 (data-blob test_expected_chmod "POSIX chmod is unsupported on Windows; use the native Windows Security dialog\0" rodata)
 (data-blob test_forbidden_probe "Windows chmod probed a path before rejecting unsupported operation\0" rodata)
 (defun sumi_test_label (label msg) (seq (ptr-write-u64 label 736 msg) 0))
 (defun sumi_test_forbidden_path (path) (seq (extern-call g_log 0 4 (data-addr test_forbidden_probe)) 0))
 (defun main (argc argv)
   (let ((ctx (extern-call g_malloc0 752)) (good 0))
     (seq
      (ptr-write-u64 ctx 32 ctx) (ptr-write-u64 ctx 48 (data-addr test_expected_chmod))
      (sumi_explorer_chmod_start ctx 0 0 0 23)
      (setq good (and (= (ptr-read-u64 ctx 72) 23) (= (ptr-read-u64 ctx 744) 1)
        (sumi_explorer_same (ptr-read-u64 ctx 736) (data-addr test_expected_chmod))))
      (ptr-write-u64 ctx 144 1) (ptr-write-u64 ctx 224 23)
      (sumi_explorer_chmod_start ctx 0 0 0 99)
      (sumi_explorer_chmod_start ctx 0 0 0 0)
      (setq good (and good (= (ptr-read-u64 ctx 72) 23) (= (ptr-read-u64 ctx 224) 23)
        (= (ptr-read-u64 ctx 408) 99) (= (ptr-read-u64 ctx 744) 1) (= (ptr-read-u64 ctx 144) 1)
        (= (ptr-read-u64 ctx 48) (data-addr test_expected_chmod))
        (sumi_explorer_same (ptr-read-u64 ctx 736) (data-addr copy_busy))))
      (extern-call g_free ctx) (if good 0 1))))
)
'''
    fixture = artifacts / "windows-chmod-diagnostic.el"; fixture.write_text(fake + diagnostic_main)
    binary = build_aot_fixture(fixture, artifacts, compiler)
    with (artifacts / "windows-chmod-diagnostic.log").open("wb") as log:
        subprocess.run([str(binary)], check=True, timeout=30, stdout=log, stderr=subprocess.STDOUT)
    print("PASS forced-Windows chmod rejects explicitly before probing and preserves busy request/selection without mutation", flush=True)


def metadata_glue_units(args):
    """Actual Linux error controls and fake-helper ABI/format glue, not Win32 runtime."""
    artifacts = (args.artifacts or args.binary.resolve().parent / "metadata-glue-evidence").resolve()
    artifacts.mkdir(parents=True, exist_ok=True)
    source = args.unit_source or Path(__file__).resolve().parents[1] / "explorer/sumi-explorer-native.el"
    text = source.read_text(); compiler = Path(os.environ["NELISP_REPO_ROOT"]).resolve()
    start = text.index(' (defun main ')
    prefix = text[:start]
    capture = prefix.index(' (defun sumi_explorer_undo_capture_root ')
    point = prefix.index('   (seq\n', capture) + len('   (seq\n')
    missing_prefix = prefix[:point] + '    (extern-call g_file_info_remove_attribute info (extern-call g_getenv (data-addr test_missing_attribute)))\n' + prefix[point:]
    missing_prefix = missing_prefix.replace('(extern-call g_file_query_info ', '(sumi_test_query_info ').replace('(extern-call g_file_read ', '(sumi_test_file_read ')
    missing_main = r'''
 (data-blob test_missing_attribute "SUMI_TEST_MISSING_ATTRIBUTE\0" rodata)
 (data-blob test_error_reused "A GIO call received an already-populated GError slot\0" rodata)
 (defun sumi_test_query_info (file attrs flags cancel error)
   (if (and error (ptr-read-u64 error 0))
     (seq (extern-call g_log 0 4 (data-addr test_error_reused)) 0)
     (extern-call g_file_query_info file attrs flags cancel error)))
 (defun sumi_test_file_read (file cancel error)
   (if (and error (ptr-read-u64 error 0))
     (seq (extern-call g_log 0 4 (data-addr test_error_reused)) 0)
     (extern-call g_file_read file cancel error)))
 (defun main (argc argv)
   (let ((job (extern-call g_malloc0 320)) (store (extern-call g_malloc0 24)) (file 0)
         (probe (sumi_explorer_undo_probe)) (stamp 0) (cancel (extern-call g_cancellable_new)) (good 0))
     (seq
      (ptr-write-u64 store 0 (extern-call g_ptr_array_new_with_free_func (addr-of sumi_explorer_undo_group_free)))
      (ptr-write-u64 job 0 1) (ptr-write-u64 job 8 (extern-call g_strdup (ptr-read-u64 argv 8)))
      (ptr-write-u64 job 16 (extern-call g_strdup (ptr-read-u64 argv 16)))
      (ptr-write-u64 job 24 cancel) (ptr-write-u64 job 272 store)
      (sumi_explorer_undo_prepare job) (sumi_explorer_single_worker 0 0 job cancel) (sumi_explorer_undo_record job)
      (setq good (and (ptr-read-u64 job 48) (ptr-read-u64 job 288) (= (ptr-read-u32 (ptr-read-u64 store 0) 8) 0)))
      (setq file (extern-call g_file_new_for_path (ptr-read-u64 job 16)))
      (setq stamp (sumi_explorer_permissions_stamp file probe))
      (setq good (and good (= stamp 0) (ptr-read-u64 probe 32)))
      (extern-call g_free stamp) (extern-call g_object_unref file) (sumi_explorer_undo_probe_free probe)
      (sumi_explorer_job_free job) (extern-call g_object_unref cancel)
      (extern-call g_ptr_array_unref (ptr-read-u64 store 0)) (extern-call g_free store)
      (if good 0 1))))
)
'''
    fixture = artifacts / "missing-identity.el"; fixture.write_text(missing_prefix + missing_main)
    binary = build_aot_fixture(fixture, artifacts, compiler)
    with tempfile.TemporaryDirectory(prefix="sumi metadata identity ") as temporary:
        base = Path(temporary); original = base / "original"; original.write_bytes(b"actual mutation truth")
        with (artifacts / "missing-identity.log").open("wb") as log:
            for attr in ("id::file", "id::filesystem"):
                target = base / attr.replace(":", "-")
                subprocess.run([str(binary), str(original), str(target)], env=dict(os.environ, G_DEBUG="fatal-warnings", SUMI_TEST_MISSING_ATTRIBUTE=attr), check=True, timeout=30, stdout=log, stderr=subprocess.STDOUT)
                assert target.read_bytes() == original.read_bytes()
    print("PASS missing file-ID and filesystem-ID preserve actual copy success, refuse Undo and survive fatal GIO warnings", flush=True)
    if args.identity_only:
        return

    # The fake implements only the documented char*/char** capability boundary.
    stub = artifacts / "fake-metadata.c"
    stub.write_text(r'''#include <glib.h>
#include <string.h>
static unsigned calls;
int sumi_test_windows(void) { return g_strcmp0(g_getenv("SUMI_TEST_MODE"), "posix") != 0; }
unsigned sumi_test_calls(void) { return calls; }
char *sumi_test_metadata(const char *path, char **error) {
    (void)path;
    if (error && *error) g_error("diagnostic slot was not empty");
    calls++;
    const char *mode = g_getenv("SUMI_TEST_MODE");
    if (!g_strcmp0(mode, "unavailable")) {
        if (error) *error = g_strdup("unsupported fake capability");
        return NULL;
    }
    return g_strdup(g_getenv(!g_strcmp0(mode, "changing") && calls > 1 ? "SUMI_TEST_STAMP_AFTER" : "SUMI_TEST_STAMP"));
}
''')
    fake_prefix = prefix.replace('(extern-call sumi_explorer_platform_is_windows)', '(extern-call sumi_test_windows)').replace('(extern-call sumi_explorer_platform_metadata_stamp ', '(extern-call sumi_test_metadata ')
    fake_main = r'''
 (data-blob test_mode "SUMI_TEST_MODE\0" rodata)
 (data-blob test_valid "valid\0" rodata)
 (data-blob test_posix "posix\0" rodata)
 (data-blob test_nonlocal "nonlocal\0" rodata)
 (data-blob test_clock "SUMI_TEST_CLOCK\0" rodata)
 (data-blob test_projection "SUMI_TEST_PROJECTION\0" rodata)
 (defun main (argc argv)
   (let ((file 0) (info 0) (probe (sumi_explorer_undo_probe)) (meta 0) (projected 0) (finger 0) (good 0)
         (mode (extern-call g_getenv (data-addr test_mode))))
     (seq
      (setq file (if (sumi_explorer_same mode (data-addr test_nonlocal)) (extern-call g_file_new_for_uri (data-addr undo_trash)) (extern-call g_file_new_for_path (ptr-read-u64 argv 8))))
      (if (sumi_explorer_same mode (data-addr test_valid))
        (seq
         (setq info (extern-call g_file_query_info file (data-addr undo_attrs) 1 0 0))
         (setq meta (sumi_explorer_undo_metadata_stamp info file))
         (sumi_explorer_undo_capture_root probe info meta)
         (ptr-write-u64 probe 176 (extern-call g_getenv (data-addr test_clock)))
         (setq projected (sumi_explorer_undo_projected_metadata probe info meta))
         (setq good (and meta projected (ptr-read-u64 probe 160) (ptr-read-u64 probe 168)
           (sumi_explorer_same projected (extern-call g_getenv (data-addr test_projection)))))
         (extern-call g_object_unref info)
         (ptr-write-u64 probe 152 1) (setq finger (sumi_explorer_undo_fingerprint probe file))
         (setq good (and good finger (ptr-read-u64 probe 144)
           (= (sumi_explorer_same finger (ptr-read-u64 probe 144)) 0))))
        (if (sumi_explorer_same mode (data-addr test_nonlocal))
          (seq (setq meta (sumi_explorer_undo_metadata_stamp 0 file)) (setq good (and (= meta 0) (= (extern-call sumi_test_calls) 0))))
          (seq
           (setq finger (sumi_explorer_undo_fingerprint probe file))
           (setq good (if (sumi_explorer_same mode (data-addr test_posix))
             (and finger (= (extern-call sumi_test_calls) 0)) (and (= finger 0) (= (ptr-read-u64 probe 32) 0)))))))
      (extern-call g_free meta) (extern-call g_free projected) (extern-call g_free finger)
      (extern-call g_object_unref file) (sumi_explorer_undo_probe_free probe) (if good 0 1))))
)
'''
    fixture = artifacts / "fake-metadata.el"; fixture.write_text(fake_prefix + fake_main)
    binary = build_aot_fixture(fixture, artifacts, compiler, extra_source=stub)
    stamp = ("ntfs-v1|volume=01234567-89ab-cdef-0123-456789abcdef|serial=0123456789abcdef|id=" + "12" * 16
             + "|created=1111111111111111|changed=2222222222222222|written=3333333333333333"
             + "|attributes=00000020|size=0000000000000005|links=00000001|directory=0|tag=00000000")
    clock = "4444444444444444"
    with tempfile.TemporaryDirectory(prefix="sumi metadata fake ") as temporary:
        original = Path(temporary) / "file"; original.write_bytes(b"bytes")
        cases = [("valid", stamp), ("posix", stamp), ("nonlocal", stamp), ("unavailable", stamp), ("changing", stamp)]
        cases += [("invalid", value) for value in (stamp + "\n", stamp.replace("changed=2222222222222222", "changed=222222222222222"), stamp.replace("changed=2222222222222222", "changed=zzzzzzzzzzzzzzzz"), stamp + "|changed=2222222222222222", stamp + "x" * 513)]
        with (artifacts / "fake-metadata.log").open("wb") as log:
            for mode, value in cases:
                env = dict(os.environ, G_DEBUG="fatal-warnings", SUMI_TEST_MODE=mode, SUMI_TEST_STAMP=value,
                           SUMI_TEST_STAMP_AFTER=stamp.replace("written=3333333333333333", "written=5555555555555555"),
                           SUMI_TEST_CLOCK=clock, SUMI_TEST_PROJECTION=stamp.replace("changed=2222222222222222", "changed=" + clock))
                subprocess.run([str(binary), str(original)], env=env, check=True, timeout=30, stdout=log, stderr=subprocess.STDOUT)
    print("PASS fake-helper ABI and strict ntfs-v1 format preserve all fields except the witnessed root change clock", flush=True)
    print("PASS fake-helper changing/unsupported stamps fail closed; POSIX and Trash URI paths never call the helper", flush=True)


def parent_cases(args):
    """Bound the mkdir witness to one direct copy output and complete cleanup."""
    spec = importlib.util.spec_from_file_location("gui", Path(__file__).with_name("explorer-gui-test.py"))
    gui = importlib.util.module_from_spec(spec); spec.loader.exec_module(gui)
    artifacts = (args.artifacts or args.binary.resolve().parent / "parent-evidence").resolve()
    artifacts.mkdir(parents=True, exist_ok=True)
    binary = args.binary.resolve()
    if args.parent_residual_only:
        source = args.unit_source or Path(__file__).resolve().parents[1] / "explorer/sumi-explorer-native.el"
        text = source.read_text(); start = text.index(' (defun sumi_explorer_undo_cleanup ')
        end = text.index(' (defun sumi_explorer_undo_worker ', start)
        body = text[start:end]
        delete = '(extern-call g_file_delete file (ptr-read-u64 probe 24) (+ probe 32))'
        assert body.count(delete) == 1
        text = text[:start] + body.replace(delete, '0') + text[end:]
        fixture = artifacts / "parent-residual.el"; fixture.write_text(text)
        binary = build_aot_fixture(fixture, artifacts, Path(os.environ["NELISP_REPO_ROOT"]).resolve())
    with tempfile.TemporaryDirectory(prefix="sumi parent witness ") as temporary:
        base = Path(temporary); root = base / "root"; root.mkdir()
        session_path = base / "session"; session_path.mkdir()
        session = gui.Session(binary, root, session_path)
        try:
            def history():
                saved = configparser.ConfigParser(); saved.read(session_path / "undo.ini")
                return [dict(saved[f"Undo{i}"]) for i in range(int(saved["Undo"]["count"]))]

            def operation(name, source="", destination=None, cancel=False):
                session.serial += 1; serial = session.serial
                text = f"[Explorer]\nversion=1\nrequest={serial}\ncommand={name}\npath={gui.escape(str(source))}\n"
                if destination is not None: text += f"destination={gui.escape(str(destination))}\n"
                if cancel: (session_path / "cancel.ini").write_text(f"[Explorer]\nversion=1\nrequest={serial}\n")
                request = session_path / "request.tmp"; request.write_text(text); request.replace(session_path / "request.ini")
                return session.wait(lambda state: state["request"] == str(serial), seconds=30, response=True)

            source = root / "source"; source.write_bytes(b"single child bytes")
            def create(name, before=False):
                parent = root / name
                assert operation("mkdir", parent)["status"] == "ok"
                if before: os.setxattr(parent, b"user.parent-before", b"retained")
                child = parent / "owned child"
                assert operation("copy", source, child)["status"] == "ok"
                return parent, child

            if args.parent_residual_only:
                parent, child = create("residual")
                result = operation("undo", history()[0]["id"])
                assert result["status"] == "error" and not child.exists(), result
                residual = list(parent.glob(".sumi-undo-*"))
                assert residual and any(path.is_file() and path.read_bytes() == source.read_bytes() for path in residual[0].rglob("*")), (result, list(parent.iterdir()))
                assert history()[0]["operation"] == "mkdir" and operation("undo", history()[0]["id"])["status"] == "error"
                print("PASS failed quarantine cleanup consumes actual copied-child detachment and never rebases its mkdir parent", flush=True)
                return

            parent, child = create("normal")
            assert operation("undo", history()[0]["id"])["status"] == "ok" and not child.exists() and not list(parent.iterdir())
            assert operation("undo", history()[0]["id"])["status"] == "ok" and not parent.exists()
            print("PASS mkdir then one direct copied child supports two real Undo operations after complete cleanup", flush=True)
            parent, child = create("before", before=True)
            result = operation("undo", history()[0]["id"])
            assert result["status"] == "ok" and "older Undo" in result["message"] and not child.exists(), result
            assert operation("undo", history()[0]["id"])["status"] == "error" and os.getxattr(parent, b"user.parent-before") == b"retained"
            print("PASS parent xattr before copy is never adopted by the older mkdir witness", flush=True)
            parent, child = create("after")
            os.setxattr(parent, b"user.parent-after", b"retained")
            result = operation("undo", history()[0]["id"])
            assert result["status"] == "ok" and "older Undo" in result["message"] and not child.exists(), result
            assert operation("undo", history()[0]["id"])["status"] == "error" and os.getxattr(parent, b"user.parent-after") == b"retained"
            print("PASS parent xattr after copy preserves actual copy inverse but blocks older mkdir with a warning", flush=True)
            parent, child = create("extra")
            foreign = parent / "external child"; foreign.write_bytes(b"retained external bytes")
            result = operation("undo", history()[0]["id"])
            assert result["status"] == "ok" and "older Undo" in result["message"] and not child.exists(), result
            assert operation("undo", history()[0]["id"])["status"] == "error" and foreign.read_bytes() == b"retained external bytes"
            print("PASS unrelated child survives copy inverse and prevents empty-parent rebasing", flush=True)
            parent, child = create("cancelled")
            before = history()
            result = operation("undo", before[0]["id"], cancel=True)
            assert result["status"] == "error" and history() == before and child.read_bytes() == source.read_bytes(), result
            assert operation("undo", before[1]["id"])["status"] == "error"
            assert operation("undo", before[0]["id"])["status"] == "ok"
            assert operation("undo", history()[0]["id"])["status"] == "ok" and not parent.exists()
            print("PASS cancelled child inverse does not rebase parent and a later actual inverse recovers", flush=True)
        finally:
            session.stop(); shutil.copyfile(session_path / "host.log", artifacts / "parent-host.log")


def rebase_cases(args):
    """Exercise immutable exact-root links through the actual request engine."""
    spec = importlib.util.spec_from_file_location("gui", Path(__file__).with_name("explorer-gui-test.py"))
    gui = importlib.util.module_from_spec(spec); spec.loader.exec_module(gui)
    artifacts = (args.artifacts or args.binary.resolve().parent / "rebase-evidence").resolve()
    artifacts.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="sumi exact Undo ") as temporary:
        base = Path(temporary); root = base / "root"; root.mkdir()
        session_path = base / "session"; session_path.mkdir()
        session = gui.Session(args.binary.resolve(), root, session_path)
        try:
            def history():
                saved = configparser.ConfigParser(); saved.read(session_path / "undo.ini")
                return [dict(saved[f"Undo{i}"]) for i in range(int(saved["Undo"]["count"]))]

            def operation(name, source="", destination=None):
                session.serial += 1; serial = session.serial
                text = f"[Explorer]\nversion=1\nrequest={serial}\ncommand={name}\npath={gui.escape(str(source))}\n"
                if destination is not None: text += f"destination={gui.escape(str(destination))}\n"
                request = session_path / "request.tmp"; request.write_text(text); request.replace(session_path / "request.ini")
                return session.wait(lambda state: state["request"] == str(serial), seconds=30, response=True)

            def undo():
                result = operation("undo", history()[0]["id"])
                assert result["status"] == "ok", result
                return result

            source = root / "source"; source.write_bytes(b"exact chain bytes"); source.chmod(0o600)
            assert operation("chmod", source, "0640")["status"] == "ok"
            assert operation("chmod", source, "0400")["status"] == "ok"
            undo(); assert stat.S_IMODE(source.stat().st_mode) == 0o640
            undo(); assert stat.S_IMODE(source.stat().st_mode) == 0o600
            print("PASS two real chmod inverses preserve all three original modes", flush=True)

            output = root / "copied"; moved = root / "moved"
            assert operation("copy", source, output)["status"] == "ok"
            assert operation("chmod", output, "0640")["status"] == "ok"
            undo(); undo(); assert not output.exists()
            print("PASS Copy to chmod to Undo to Undo removes the original owned output", flush=True)
            assert operation("copy", source, output)["status"] == "ok"
            assert operation("move", output, moved)["status"] == "ok"
            undo(); assert output.read_bytes() == source.read_bytes() and not moved.exists()
            undo(); assert not output.exists()
            print("PASS Copy to native move to two Undo operations restores then removes exact output", flush=True)

            assert operation("copy", source, output)["status"] == "ok"
            for mode in ("0640", "0400", "0600"):
                assert operation("chmod", output, mode)["status"] == "ok"
            assert operation("move", output, moved)["status"] == "ok"
            assert operation("chmod", moved, "0440")["status"] == "ok"
            for _ in range(6): undo()
            assert not output.exists() and not moved.exists()
            print("PASS six-operation immediate chain retains immutable older preimages across rebases", flush=True)

            assert operation("copy", source, output)["status"] == "ok"
            os.setxattr(output, b"user.pre-B", b"must survive")
            assert operation("chmod", output, "0640")["status"] == "ok"
            undo()
            assert operation("undo", history()[0]["id"])["status"] == "error"
            assert os.getxattr(output, b"user.pre-B") == b"must survive" and output.read_bytes() == source.read_bytes()
            print("PASS external xattr before B is never adopted into the older owned expectation", flush=True)

            after = root / "after"
            assert operation("copy", source, after)["status"] == "ok"
            assert operation("chmod", after, "0640")["status"] == "ok"
            before = history(); os.setxattr(after, b"user.post-B", b"retained")
            assert operation("undo", before[0]["id"])["status"] == "error" and history() == before
            assert os.getxattr(after, b"user.post-B") == b"retained"
            print("PASS external xattr after B blocks its inverse and preserves both history items", flush=True)

            tree = root / "tree"; tree.mkdir(); (tree / "child").write_bytes(b"original child")
            tree_output = root / "tree output"
            assert operation("copy", tree, tree_output)["status"] == "ok"
            assert operation("chmod", tree_output, "0700")["status"] == "ok"
            (tree_output / "child").write_bytes(b"externally changed child")
            result = undo()
            assert "older Undo" in result["message"], result
            assert operation("undo", history()[0]["id"])["status"] == "error"
            assert (tree_output / "child").read_bytes() == b"externally changed child"
            print("PASS changed descendant blocks projected predecessor adoption without denying the actual chmod inverse", flush=True)

            replacement = root / "replacement"
            assert operation("copy", source, replacement)["status"] == "ok"
            assert operation("chmod", replacement, "0640")["status"] == "ok"
            held = root / "held inode"; replacement.rename(held); replacement.write_bytes(held.read_bytes()); replacement.chmod(0o640)
            before = history(); assert operation("undo", before[0]["id"])["status"] == "error" and history() == before
            print("PASS identical replacement bytes cannot authorize a linked inverse", flush=True)

            evicted = root / "evicted predecessor"
            assert operation("copy", source, evicted)["status"] == "ok"
            assert operation("chmod", evicted, "0640")["status"] == "ok"
            for index in range(31):
                assert operation("mkdir", root / f"eviction-{index}")["status"] == "ok"
            assert len(history()) == 32
            for _ in range(31): undo()
            result = undo()
            assert "older Undo" in result["message"] and not history() and evicted.exists(), result
            print("PASS evicted immutable target IDs produce a warning after actual inverse success", flush=True)
        finally:
            session.stop(); shutil.copyfile(session_path / "host.log", artifacts / "rebase-host.log")


def busy_guard_cases(args):
    """Hold a real copy worker while GUI events must preserve its request."""
    artifacts = (args.artifacts or args.binary.resolve().parent / "busy-evidence").resolve()
    artifacts.mkdir(parents=True, exist_ok=True)
    source = args.unit_source or Path(__file__).resolve().parents[1] / "explorer/sumi-explorer-native.el"
    text = source.read_text()
    anchor = " (defun sumi_explorer_worker (task object job cancellable)\n   (seq\n"
    barrier = r'''    (if (= (ptr-read-u64 job 0) 1)
      (seq
       (extern-call g_file_set_contents (extern-call g_getenv (data-addr test_entered)) (data-addr empty) 0 0)
       (while (= (extern-call g_file_test (extern-call g_getenv (data-addr test_release)) 16) 0)
         (extern-call g_usleep 10000))))
'''
    assert text.count(anchor) == 1
    text = text.replace(anchor, ' (data-blob test_entered "SUMI_TEST_ENTERED\\0" rodata)\n (data-blob test_release "SUMI_TEST_RELEASE\\0" rodata)\n' + anchor + barrier)
    capture = '(setq info (extern-call g_file_query_info source (data-addr undo_attrs) 1 0 0))'
    assert text.count(capture) == 1
    text = text.replace(capture, '(setq info (if (extern-call g_str_has_suffix (ptr-read-u64 item 0) (data-addr test_capture_suffix)) 0 (extern-call g_file_query_info source (data-addr undo_attrs) 1 0 0)))')
    text = text.replace(' (defun sumi_explorer_undo_prepare ', ' (data-blob test_capture_suffix "capture-fail\\0" rodata)\n (defun sumi_explorer_undo_prepare ', 1)
    hash_start = text.index(' (defun sumi_explorer_undo_hash_tree ')
    hash_end = text.index(' (defun sumi_explorer_undo_fingerprint ', hash_start)
    hash_body = text[hash_start:hash_end]
    revalidate = '            ;; Revalidate every node after reading its bytes or descendants.\n'
    cleanup = '            (extern-call g_object_unref info)))))'
    mutation = r'''            (if (or (= kind 1) (= kind 2))
              (let ((test_path (extern-call g_file_get_path file)))
                (seq
                 (if (extern-call g_str_has_suffix test_path (data-addr test_race_suffix))
                   (extern-call g_file_set_attribute_uint32 file (data-addr undo_mode) 384 1 0 0))
                 (extern-call g_free test_path))))
'''
    point = revalidate if revalidate in hash_body else cleanup
    assert hash_body.count(point) == 1
    hash_body = hash_body.replace(point, mutation + point)
    text = text[:hash_start] + hash_body + text[hash_end:]
    text = text.replace(' (defun sumi_explorer_undo_hash_tree ', ' (data-blob test_race_suffix "race-copy\\0" rodata)\n (defun sumi_explorer_undo_hash_tree ', 1)
    fixture = artifacts / "busy-copy.el"; fixture.write_text(text)
    binary = build_aot_fixture(fixture, artifacts, Path(os.environ["NELISP_REPO_ROOT"]).resolve())
    spec = importlib.util.spec_from_file_location("gui", Path(__file__).with_name("explorer-gui-test.py"))
    gui = importlib.util.module_from_spec(spec); spec.loader.exec_module(gui)
    with tempfile.TemporaryDirectory(prefix="sumi busy copy ") as temporary:
        base = Path(temporary); root = base / "root"; root.mkdir()
        session_path = base / "session"; session_path.mkdir()
        original = root / "A capture-fail"; original.write_bytes(b"actual controlled copy bytes")
        target = base / "copied"
        entered, release = base / "entered", base / "release"
        previous = {key: os.environ.get(key) for key in ("SUMI_TEST_ENTERED", "SUMI_TEST_RELEASE")}
        os.environ.update(SUMI_TEST_ENTERED=str(entered), SUMI_TEST_RELEASE=str(release))
        session = gui.Session(binary, root, session_path)
        try:
            window = gui.xdo("search", "--onlyvisible", "--sync", "--name", "^Sumi Explorer$").splitlines()[-1]
            gui.xdo("windowfocus", "--sync", window)
            session.ui().row_click(original)
            request = session_path / "request.ini"
            request.write_text(f"[Explorer]\nversion=1\nrequest=1\ncommand=copy\npath={gui.escape(str(original))}\ndestination={gui.escape(str(target))}\n")
            deadline = time.monotonic() + 20
            while not entered.exists():
                assert time.monotonic() < deadline and session.process.poll() is None
                time.sleep(0.025)
            pending = session.state()
            assert pending["status"] == "ok" and "progress" in pending["message"].lower(), pending
            snapshots = {name: (session_path / name).read_bytes() if (session_path / name).exists() else None
                         for name in ("state.ini", "response.ini")}
            gui.xdo("key", "ctrl+z")
            gui.xdo("click", "3")
            gui.xdo("mousemove", "--window", window, "650", "450"); gui.xdo("click", "3")
            time.sleep(0.25)
            for name, expected in snapshots.items():
                actual = (session_path / name).read_bytes() if (session_path / name).exists() else None
                assert actual == expected, (name, pending, session.state())
            release.touch()
            result = session.wait(lambda state: state["request"] == "1", seconds=20, response=True)
            assert result["status"] == "ok" and target.read_bytes() == original.read_bytes(), result
            saved = configparser.ConfigParser(); saved.read(session_path / "undo.ini")
            group = saved["Undo0"]["id"]
            session.serial = 1
            assert session.command("undo", group)["status"] == "ok" and not target.exists()
            print("PASS busy Ctrl-Z and row/blank right-click preserve real copy request, bytes, success and usable Undo", flush=True)
            race_target = base / "race-copy"
            request.write_text(f"[Explorer]\nversion=1\nrequest=3\ncommand=copy\npath={gui.escape(str(original))}\ndestination={gui.escape(str(race_target))}\n")
            result = session.wait(lambda state: state["request"] == "3", seconds=20, response=True)
            assert result["status"] == "ok" and race_target.read_bytes() == original.read_bytes(), result
            saved = configparser.ConfigParser(); saved.read(session_path / "undo.ini")
            assert not saved.has_section("Undo0") and "unavailable" in result["message"].lower(), dict(saved)
            print("PASS metadata mutation during content hash preserves copy success and refuses unstable Undo", flush=True)
            request.write_text(f"[Explorer]\nversion=1\nrequest=4\ncommand=chmod\npath={gui.escape(str(original))}\ndestination=0640\n")
            result = session.wait(lambda state: state["request"] == "4", seconds=20, response=True)
            assert result["status"] == "ok" and stat.S_IMODE(original.stat().st_mode) == 0o640, result
            saved = configparser.ConfigParser(); saved.read(session_path / "undo.ini")
            assert not saved.has_section("Undo0") and "unavailable" in result["message"].lower(), dict(saved)
            session.serial = 4
            print("PASS failed old-mode capture preserves actual chmod success and refuses unsafe Undo", flush=True)
            for directory in (False, True):
                zero = root / ("zero directory" if directory else "zero file")
                if directory: zero.mkdir()
                else: zero.write_bytes(b"zero mode bytes")
                zero.chmod(0)
                session.serial += 1
                request.write_text(f"[Explorer]\nversion=1\nrequest={session.serial}\ncommand=chmod\npath={gui.escape(str(zero))}\ndestination=0700\n")
                result = session.wait(lambda state: state["request"] == str(session.serial), seconds=20, response=True)
                assert result["status"] == "ok" and stat.S_IMODE(zero.stat().st_mode) == 0o700, result
                saved = configparser.ConfigParser(); saved.read(session_path / "undo.ini")
                assert session.command("undo", saved["Undo0"]["id"])["status"] == "ok"
                assert stat.S_IMODE(zero.stat().st_mode) == 0
                zero.chmod(0o700 if directory else 0o600)
            print("PASS genuine old mode 0000 remains valid and is restored for files and directories", flush=True)
            tree = root / "source tree"; tree.mkdir(); (tree / "child").write_bytes(b"tree bytes")
            tree_target = base / "tree-race-copy"
            session.serial += 1
            request.write_text(f"[Explorer]\nversion=1\nrequest={session.serial}\ncommand=copy\npath={gui.escape(str(tree))}\ndestination={gui.escape(str(tree_target))}\n")
            result = session.wait(lambda state: state["request"] == str(session.serial), seconds=20, response=True)
            tree_target.chmod(0o700)
            assert result["status"] == "ok" and (tree_target / "child").read_bytes() == b"tree bytes", result
            saved = configparser.ConfigParser(); saved.read(session_path / "undo.ini")
            assert not saved.has_section("Undo0") and "unavailable" in result["message"].lower(), dict(saved)
            print("PASS directory metadata mutation after descendant hashing refuses unstable tree Undo", flush=True)
        finally:
            release.touch(); session.stop()
            shutil.copyfile(session_path / "host.log", artifacts / "busy-host.log")
            for key, value in previous.items():
                if value is None: os.environ.pop(key, None)
                else: os.environ[key] = value


def gui_cases(args):
    """Real GTK context prompts, file-view Undo and portable mode editing."""
    spec = importlib.util.spec_from_file_location("gui", Path(__file__).with_name("explorer-gui-test.py"))
    gui = importlib.util.module_from_spec(spec); spec.loader.exec_module(gui)
    artifacts = (args.artifacts or args.binary.resolve().parent / "advanced-evidence").resolve(); artifacts.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="sumi advanced GUI 日本語 ") as temporary:
        base = Path(temporary); root, session_path = base / "root", base / "session"
        root.mkdir(); session_path.mkdir()
        first, other = root / "A first.txt", root / "B other.txt"
        first.write_bytes(b"frozen first bytes"); other.write_bytes(b"other retained bytes")
        first.chmod(0o600); other.chmod(0o600)
        session = gui.Session(args.binary.resolve(), root, session_path)
        try:
            window = gui.xdo("search", "--onlyvisible", "--sync", "--name", "^Sumi Explorer$").splitlines()[-1]
            gui.xdo("windowfocus", "--sync", window)
            geometry = gui.xdo("getwindowgeometry", "--shell", window)
            assert "WIDTH=1000" in geometry and "HEIGHT=700" in geometry, geometry

            def wait(check, description):
                end = time.monotonic() + 20
                while time.monotonic() < end:
                    value = check()
                    if value: return value
                    assert session.process.poll() is None
                    time.sleep(0.025)
                raise AssertionError(description)

            def command(name, source="", destination=None, paths=None):
                session.serial += 1; serial = session.serial
                with (artifacts / "gui-command-trace.jsonl").open("a") as trace:
                    trace.write(json.dumps({"request": serial, "command": name, "path": str(source), "destination": str(destination)}) + "\n")
                text = f"[Explorer]\nversion=1\nrequest={serial}\ncommand={name}\npath={gui.escape(str(source))}\n"
                if destination is not None: text += f"destination={gui.escape(str(destination))}\n"
                if paths is not None: text += f"count={len(paths)}\n" + "".join(f"path{i}={gui.escape(str(path))}\n" for i, path in enumerate(paths))
                request = session_path / "request.tmp"; request.write_text(text); request.replace(session_path / "request.ini")
                return session.wait(lambda state: state["request"] == str(serial), seconds=30, response=True)

            def prompt(title):
                return gui.xdo("search", "--onlyvisible", "--sync", "--name", "^" + title + "$").splitlines()[-1]

            def enter(window_id, value):
                gui.xdo("windowfocus", "--sync", window_id)
                gui.xdo("key", "ctrl+a"); gui.xdo("type", "--clearmodifiers", "--", str(value)); gui.xdo("key", "Return")

            def top_operation(operation):
                saved = configparser.ConfigParser(); saved.read(session_path / "undo.ini")
                return saved.has_section("Undo0") and saved["Undo0"]["operation"] == operation

            result = command("chmod", first, "0640")
            assert result["status"] == "ok", result
            assert stat.S_IMODE(first.stat().st_mode) == 0o640
            saved = configparser.ConfigParser(); saved.read(session_path / "undo.ini"); group = saved["Undo0"]["id"]
            assert command("undo", group)["status"] == "ok" and stat.S_IMODE(first.stat().st_mode) == 0o600
            protected = root / "zero mode directory"; protected.mkdir(mode=0o700); (protected / "retained").write_bytes(b"protected child")
            for path, old_mode in ((first, 0o600), (protected, 0o700)):
                assert command("chmod", path, "0000")["status"] == "ok"
                assert stat.S_IMODE(path.stat().st_mode) == 0
                saved = configparser.ConfigParser(); saved.read(session_path / "undo.ini"); group = saved["Undo0"]["id"]
                assert saved["Undo0"]["operation"] == "chmod"
                assert command("undo", group)["status"] == "ok" and stat.S_IMODE(path.stat().st_mode) == old_mode
            assert (protected / "retained").read_bytes() == b"protected child"
            for mode in ("888", "1000", "00000", "64", "0x40"):
                assert command("chmod", first, mode)["status"] == "error"
                assert stat.S_IMODE(first.stat().st_mode) == 0o600
            link = root / "C symlink"; link.symlink_to(first.name)
            assert command("chmod", link, "0777")["status"] == "error"
            assert stat.S_IMODE(first.stat().st_mode) == 0o600
            assert command("chmod", first, "0640")["status"] == "ok"
            saved = configparser.ConfigParser(); saved.read(session_path / "undo.ini"); group = saved["Undo0"]["id"]
            os.setxattr(first, b"user.chmod-modified", b"external metadata")
            assert command("undo", group)["status"] == "error" and stat.S_IMODE(first.stat().st_mode) == 0o640
            first.chmod(0o600)
            print("PASS real chmod/Undo changes exact mode and rejects malformed octal and symlink targets", flush=True)

            assert command("permissions", first)["status"] == "ok"
            dialog = prompt("Sumi Permissions")
            assert command("select", other)["status"] == "ok"
            enter(dialog, "0640")
            wait(lambda: stat.S_IMODE(first.stat().st_mode) == 0o640, "Permissions Apply must modify its frozen target")
            assert stat.S_IMODE(other.stat().st_mode) == 0o600
            command("select", first); gui.xdo("windowfocus", "--sync", window); gui.xdo("key", "ctrl+z")
            wait(lambda: stat.S_IMODE(first.stat().st_mode) == 0o600, "File-view Ctrl-Z must undo chmod")
            assert command("permissions", first)["status"] == "ok"
            dialog = prompt("Sumi Permissions")
            saved = root / "D original inode"; first.rename(saved); first.write_bytes(saved.read_bytes()); first.chmod(0o600)
            enter(dialog, "0777")
            session.wait(lambda state: state["status"] == "error")
            assert stat.S_IMODE(first.stat().st_mode) == 0o600
            print("PASS actual GTK permissions prompt freezes target and rejects replacement at Apply", flush=True)

            command("select", first); gui.xdo("windowfocus", "--sync", window)
            session.ui().row_click(first, 3)
            # Folder-only New Tab/New Window are insensitive for this file.
            session.ui().click("Create ZIP", "button")
            dialog = prompt("Sumi Create ZIP")
            command("select", other)
            archive = base / "GUI frozen 日本語.zip"; enter(dialog, archive)
            wait(lambda: archive.exists() and top_operation("zip-create"), "GUI ZIP creation completion")
            wait(lambda: archive.stat().st_size > 0, "Finished archive")
            with zipfile.ZipFile(archive) as saved:
                assert saved.namelist() == [first.name] and saved.read(first.name) == first.read_bytes()
            assert command("open", base)["status"] == "ok"
            command("select", archive); gui.xdo("windowfocus", "--sync", window)
            # Right-click the selected row using its real y coordinate from display order.
            session.ui().row_click(archive, 3)
            session.ui().click("Extract ZIP", "button")
            dialog = prompt("Sumi Extract ZIP"); extracted = base / "GUI extracted 日本語"; enter(dialog, extracted)
            wait(lambda: (extracted / first.name).exists() and top_operation("zip-extract"), "GUI ZIP extraction")
            assert (extracted / first.name).read_bytes() == first.read_bytes()
            gui.xdo("windowfocus", "--sync", window); command("select", archive); gui.xdo("key", "ctrl+z")
            wait(lambda: not extracted.exists(), "GUI Undo extraction")
            print("PASS actual context ZIP prompts freeze selected identity and file-view Undo removes extracted output", flush=True)

            copied = base / "editing guard copy"; assert command("copy", first, copied)["status"] == "ok"
            gui.xdo("windowfocus", "--sync", window); gui.xdo("key", "ctrl+l"); gui.xdo("type", "--", "editing text"); gui.xdo("key", "ctrl+z")
            time.sleep(0.15); assert copied.exists(), "Editable Ctrl-Z must not invoke file Undo"
            command("select", archive); gui.xdo("key", "ctrl+z")
            wait(lambda: not copied.exists(), "File-view Undo after editing")
            assert command("shell-menu", paths=[first])["status"] == "error"
            assert "unsupported" in session.state(response=True)["message"].lower()
            assert first.read_bytes() == b"frozen first bytes"
            print("PASS text editing keeps native Ctrl-Z and Linux rejects native Shell invocation explicitly", flush=True)
        finally:
            session.stop(); shutil.copyfile(session_path / "host.log", artifacts / "gui-host.log")


if __name__ == "__main__":
    resource.setrlimit(resource.RLIMIT_AS, (8388608 * 1024, 8388608 * 1024))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path)
    parser.add_argument("--audit-unit-only", action="store_true")
    parser.add_argument("--unit-source", type=Path, help="Source-bound helper negative control only")
    parser.add_argument("--undo-only", action="store_true")
    parser.add_argument("--zip-only", action="store_true")
    parser.add_argument("--gui-only", action="store_true")
    parser.add_argument("--fault-unit-only", action="store_true")
    parser.add_argument("--busy-unit-only", action="store_true")
    parser.add_argument("--rebase-only", action="store_true")
    parser.add_argument("--parent-only", action="store_true")
    parser.add_argument("--parent-residual-only", action="store_true")
    parser.add_argument("--metadata-glue-only", action="store_true")
    parser.add_argument("--identity-only", action="store_true")
    parser.add_argument("--native-path-unit-only", action="store_true")
    parser.add_argument("--single-select-only", action="store_true")
    parser.add_argument("--zip-provider-only", action="store_true")
    parser.add_argument("--raw-not-negative", action="store_true", help="Rebuild the isolated original raw-not crash negative")
    args = parser.parse_args()
    if args.zip_provider_only:
        zip_provider_units(args)
    elif args.single_select_only:
        single_select_cases(args)
    elif args.native_path_unit_only:
        native_path_units(args)
    elif args.metadata_glue_only or args.identity_only:
        metadata_glue_units(args)
    elif args.parent_only or args.parent_residual_only:
        parent_cases(args)
    elif args.rebase_only:
        rebase_cases(args)
    elif args.busy_unit_only:
        busy_guard_cases(args)
    elif args.fault_unit_only or args.raw_not_negative:
        undo_fault_unit(args)
    elif args.gui_only:
        gui_cases(args)
    elif args.undo_only:
        undo_cases(args)
    elif not args.audit_unit_only:
        run(args)
    if not args.undo_only and not args.gui_only and not args.fault_unit_only and not args.raw_not_negative and not args.busy_unit_only and not args.rebase_only and not args.parent_only and not args.parent_residual_only and not args.metadata_glue_only and not args.identity_only and not args.native_path_unit_only and not args.single_select_only and not args.zip_provider_only:
        audit_units(args)
        if not args.audit_unit_only and not args.zip_only:
            undo_cases(args)
            gui_cases(args)
            undo_fault_unit(args)
            busy_guard_cases(args)
            rebase_cases(args)
            parent_cases(args)
            metadata_glue_units(args)
            native_path_units(args)
            single_select_cases(args)
            zip_provider_units(args)
