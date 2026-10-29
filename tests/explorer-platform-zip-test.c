/* Real owned-handle ZIP I/O checks; Linux exercises unsupported stubs only. */
#include "platform-zip.h"
#include <stdio.h>
#include <string.h>
#include <stddef.h>
static unsigned checks, failures;
#define CHECK(value, label) do { gboolean passed = !!(value); ++checks; if (!passed) ++failures; \
    printf("%s %s\n", passed ? "PASS" : "FAIL", label); } while (0)

#ifdef _WIN32
#include <windows.h>
#include <wchar.h>
#include <winioctl.h>

static HANDLE owned[128];
static unsigned opened, closed, active, close_failures;
static unsigned legacy_identity_calls;
static int query_fault = -1, identity_unavailable, provider, close_fault, high_identity, omit_identity;
static GCancellable *cancel_after_write;
static WCHAR last_snapshot[32768];

static HANDLE WINAPI observed_create(LPCWSTR path, DWORD access, DWORD share, LPSECURITY_ATTRIBUTES security,
                                    DWORD disposition, DWORD flags, HANDLE template)
{
    HANDLE handle = CreateFileW(path, access, share, security, disposition, flags, template);
    if (handle != INVALID_HANDLE_VALUE) {
        unsigned slot;
        for (slot = 0; slot < G_N_ELEMENTS(owned) && owned[slot]; ++slot) {}
        if (slot == G_N_ELEMENTS(owned)) { CloseHandle(handle); SetLastError(ERROR_TOO_MANY_OPEN_FILES); return INVALID_HANDLE_VALUE; }
        owned[slot] = handle; ++opened; ++active;
        if (flags & FILE_FLAG_DELETE_ON_CLOSE) wcsncpy(last_snapshot, path, G_N_ELEMENTS(last_snapshot) - 1);
    }
    return handle;
}
static BOOL WINAPI observed_close(HANDLE handle)
{
    BOOL result = CloseHandle(handle);
    for (unsigned i = 0; i < G_N_ELEMENTS(owned); ++i) if (owned[i] == handle) {
        if (result) { owned[i] = NULL; ++closed; --active; }
        else ++close_failures;
        break;
    }
    if (result && close_fault) { close_fault = 0; SetLastError(ERROR_ACCESS_DENIED); return FALSE; }
    return result;
}
static BOOL WINAPI observed_query(HANDLE handle, FILE_INFO_BY_HANDLE_CLASS kind, void *data, DWORD size)
{
    if ((int)kind == query_fault || (kind == FileIdInfo && identity_unavailable)) {
        SetLastError(kind == FileIdInfo && identity_unavailable ? (DWORD)identity_unavailable : ERROR_ACCESS_DENIED);
        return FALSE;
    }
    BOOL result = GetFileInformationByHandleEx(handle, kind, data, size);
    if (result && kind == FileIdInfo) {
        FILE_ID_INFO *identity = data;
        if (high_identity) identity->FileId.Identifier[15] ^= 0x7f;
        if (omit_identity) memset(&identity->FileId, 0, sizeof(identity->FileId));
    }
    return result;
}
static BOOL WINAPI observed_volume(HANDLE handle, LPWSTR label, DWORD label_size, LPDWORD serial,
                                  LPDWORD maximum, LPDWORD flags, LPWSTR filesystem, DWORD filesystem_size)
{
    BOOL result = GetVolumeInformationByHandleW(handle, label, label_size, serial, maximum, flags, filesystem, filesystem_size);
    if (result && provider && filesystem && filesystem_size >= 8)
        wcscpy(filesystem, provider == 1 ? L"FAT32" : L"ReFS");
    return result;
}
static BOOL WINAPI observed_legacy_identity(HANDLE handle, LPBY_HANDLE_FILE_INFORMATION information)
{
    ++legacy_identity_calls;
    return GetFileInformationByHandle(handle, information);
}
static BOOL WINAPI observed_write(HANDLE handle, const void *data, DWORD size, LPDWORD written, LPOVERLAPPED overlapped)
{
    BOOL result = WriteFile(handle, data, size, written, overlapped);
    CHECK(overlapped == NULL && size <= 65536, "snapshot writes use synchronous bounded chunks");
    if (result && cancel_after_write) g_cancellable_cancel(cancel_after_write);
    return result;
}
#define CreateFileW observed_create
#define CloseHandle observed_close
#define GetFileInformationByHandleEx observed_query
#define GetVolumeInformationByHandleW observed_volume
#define GetFileInformationByHandle observed_legacy_identity
#define WriteFile observed_write
#include <platform-zip.inc>
#undef CreateFileW
#undef CloseHandle
#undef GetFileInformationByHandleEx
#undef GetVolumeInformationByHandleW
#undef GetFileInformationByHandle
#undef WriteFile

enum { PAYLOAD_SIZE = 3 * 65536 + 37 };
static unsigned char payload[PAYLOAD_SIZE], readback[PAYLOAD_SIZE];
static const DWORD sharing = FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE;
static char *utf8(const WCHAR *path) { return g_utf16_to_utf8((const gunichar2 *)path, -1, NULL, NULL, NULL); }
static gboolean write_fixture(const WCHAR *path)
{
    HANDLE file = CreateFileW(path, GENERIC_WRITE, sharing, NULL, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, NULL);
    DWORD written = 0;
    gboolean result = file != INVALID_HANDLE_VALUE && WriteFile(file, payload, PAYLOAD_SIZE, &written, NULL) && written == PAYLOAD_SIZE;
    if (file != INVALID_HANDLE_VALUE) CloseHandle(file);
    return result;
}
static const char *attribute(GFileInfo *info, const char *name)
{ return info ? g_file_info_get_attribute_string(info, name) : NULL; }
static gboolean same_attribute(GFileInfo *a, GFileInfo *b, const char *name)
{ const char *x = attribute(a, name), *y = attribute(b, name); return x && y && !strcmp(x, y); }
static void clear_info(GFileInfo **info) { if (*info) { g_object_unref(*info); *info = NULL; } }

static void test_source(const WCHAR *path, const WCHAR *root)
{
    char *name = utf8(path), *directory = utf8(root); GError *error = NULL;
    GFileInfo *before = sumi_explorer_zip_path_query(name, G_FILE_QUERY_INFO_NOFOLLOW_SYMLINKS, NULL, &error);
    SumiExplorerZipIO *io = sumi_explorer_zip_open_source(name, NULL, &error);
    CHECK(before && io && !error, "open actual Unicode multi-chunk source");
    if (!io) goto done;
    GFileInfo *opened_info = sumi_explorer_zip_io_query(io, NULL, &error);
    CHECK(opened_info && !error && same_attribute(before, opened_info, G_FILE_ATTRIBUTE_ID_FILESYSTEM) &&
          same_attribute(before, opened_info, G_FILE_ATTRIBUTE_ID_FILE) && same_attribute(before, opened_info, G_FILE_ATTRIBUTE_ETAG_VALUE),
          "path and actual opened source share one native identity and etag domain");
    GFileInfo *ancestor = sumi_explorer_zip_path_query(directory, G_FILE_QUERY_INFO_NONE, NULL, &error);
    CHECK(ancestor && g_file_info_get_file_type(ancestor) == G_FILE_TYPE_DIRECTORY &&
          same_attribute(before, ancestor, G_FILE_ATTRIBUTE_ID_FILESYSTEM), "directory ancestor uses the same native filesystem domain");
    clear_info(&ancestor);
    gsize total = 0;
    while (total < PAYLOAD_SIZE) {
        gssize count = sumi_explorer_zip_io_read(io, readback + total, PAYLOAD_SIZE - total, NULL, &error);
        CHECK(count > 0 && count <= 65536 && !error, "read distinct sequential source chunk");
        if (count <= 0) break;
        total += (gsize)count;
    }
    CHECK(total == PAYLOAD_SIZE && !memcmp(payload, readback, PAYLOAD_SIZE), "all distinct source chunks retain exact Unicode fixture bytes");
    CHECK(sumi_explorer_zip_io_read(io, readback, 1, NULL, &error) == 0 && !error, "source EOF is zero rather than a repeated first chunk");
    CHECK(sumi_explorer_zip_io_seek(io, 70003, G_SEEK_SET, NULL, &error) == 70003 &&
          sumi_explorer_zip_io_read(io, readback, 13, NULL, &error) == 13 && !memcmp(readback, payload + 70003, 13), "SET seek returns actual position and bytes");
    CHECK(sumi_explorer_zip_io_seek(io, -5, G_SEEK_CUR, NULL, &error) == 70011, "CUR seek returns actual position");
    CHECK(sumi_explorer_zip_io_seek(io, -17, G_SEEK_END, NULL, &error) == PAYLOAD_SIZE - 17 &&
          sumi_explorer_zip_io_read(io, readback, 17, NULL, &error) == 17 && !memcmp(readback, payload + PAYLOAD_SIZE - 17, 17), "END seek reads the actual tail");
    GCancellable *cancel = g_cancellable_new(); g_cancellable_cancel(cancel);
    CHECK(sumi_explorer_zip_io_read(io, readback, 1, cancel, &error) == -1 &&
          g_error_matches(error, G_IO_ERROR, G_IO_ERROR_CANCELLED), "cancelled source read reports typed cancellation");
    g_clear_error(&error); g_object_unref(cancel);
    CHECK(sumi_explorer_zip_io_seek(io, 0, G_SEEK_CUR, NULL, &error) == PAYLOAD_SIZE, "cancelled read did not advance the actual source pointer");
    GFileInfo *after = sumi_explorer_zip_io_query(io, NULL, &error);
    CHECK(same_attribute(opened_info, after, G_FILE_ATTRIBUTE_ETAG_VALUE), "source reads do not alter etag through access time");
    clear_info(&after); clear_info(&opened_info);
    HANDLE writer = CreateFileW(path, GENERIC_WRITE, sharing, NULL, OPEN_EXISTING, 0, NULL);
    DWORD code = writer == INVALID_HANDLE_VALUE ? GetLastError() : ERROR_SUCCESS;
    CHECK(writer == INVALID_HANDLE_VALUE && code == ERROR_SHARING_VIOLATION, "source handle denies concurrent data writers");
    if (writer != INVALID_HANDLE_VALUE) CloseHandle(writer);
    CHECK(!DeleteFileW(path) && GetLastError() == ERROR_SHARING_VIOLATION, "source handle denies deletion");
    CHECK(!sumi_explorer_zip_io_write_all(io, payload, 1, NULL, &error) && error, "readonly source cannot become a snapshot writer");
    g_clear_error(&error);
    CHECK(sumi_explorer_zip_io_close(io, &error) && !error, "source close consumes the actual handle"); io = NULL;
done:
    if (io) sumi_explorer_zip_io_close(io, NULL);
    clear_info(&before); g_clear_error(&error); g_free(name); g_free(directory);
}

static void test_snapshot(void)
{
    GError *error = NULL; GCancellable *cancel = g_cancellable_new();
    SumiExplorerZipIO *io = sumi_explorer_zip_new_snapshot(NULL, &error);
    CHECK(io && !error, "create verified private snapshot on actual owned handle");
    if (!io) goto done;
    CHECK(sumi_explorer_zip_io_write_all(io, payload, PAYLOAD_SIZE, NULL, &error), "write complete distinct multi-chunk snapshot");
    GFileInfo *info = sumi_explorer_zip_io_query(io, NULL, &error);
    CHECK(info && g_file_info_get_size(info) == PAYLOAD_SIZE && attribute(info, G_FILE_ATTRIBUTE_ETAG_VALUE), "snapshot metadata queries its actual handle");
    clear_info(&info);
    CHECK(sumi_explorer_zip_io_seek(io, 0, G_SEEK_SET, NULL, &error) == 0, "rewind actual snapshot handle");
    gsize total = 0;
    while (total < PAYLOAD_SIZE) {
        gssize count = sumi_explorer_zip_io_read(io, readback + total, PAYLOAD_SIZE - total, NULL, &error);
        if (count <= 0) break;
        total += (gsize)count;
    }
    CHECK(total == PAYLOAD_SIZE && !memcmp(payload, readback, PAYLOAD_SIZE), "rewound snapshot returns every exact chunk once");
    WCHAR *original = g_memdup2(io->name, (wcslen(io->name) + 1) * sizeof(WCHAR));
    WCHAR *retired = g_new0(WCHAR, wcslen(original) + 12); swprintf(retired, wcslen(original) + 12, L"%ls.retired", original);
    gsize bytes = offsetof(FILE_RENAME_INFO, FileName) + (wcslen(retired) + 1) * sizeof(WCHAR);
    FILE_RENAME_INFO *rename = g_malloc0(bytes);
    rename->FileNameLength = (DWORD)(wcslen(retired) * sizeof(WCHAR)); memcpy(rename->FileName, retired, rename->FileNameLength);
    BOOL moved = SetFileInformationByHandle(io->handle, FileRenameInfo, rename, (DWORD)bytes);
    CHECK(moved, "rename only the test-owned snapshot through its actual handle");
    gboolean foreign = moved && write_fixture(original);
    CHECK(foreign, "create distinct foreign replacement at the snapshot's retired original name");
    CHECK(sumi_explorer_zip_io_close(io, &error) && !error, "snapshot close deletes its owned object by handle"); io = NULL;
    CHECK(GetFileAttributesW(retired) == INVALID_FILE_ATTRIBUTES && GetLastError() == ERROR_FILE_NOT_FOUND,
          "renamed snapshot disappears on final handle close");
    if (foreign) {
        HANDLE file = CreateFileW(original, GENERIC_READ, sharing, NULL, OPEN_EXISTING, 0, NULL); DWORD got = 0;
        CHECK(file != INVALID_HANDLE_VALUE && ReadFile(file, readback, PAYLOAD_SIZE, &got, NULL) && got == PAYLOAD_SIZE &&
              !memcmp(readback, payload, PAYLOAD_SIZE), "snapshot close preserves the distinct replacement and its bytes");
        if (file != INVALID_HANDLE_VALUE) CloseHandle(file);
        CHECK(DeleteFileW(original), "cleanup only the test-created replacement");
    }
    g_free(original); g_free(retired); g_free(rename);
    io = sumi_explorer_zip_new_snapshot(NULL, &error);
    CHECK(io && !error, "create owned cancellation snapshot");
    if (!io) goto done;
    cancel_after_write = cancel;
    CHECK(!sumi_explorer_zip_io_write_all(io, payload, PAYLOAD_SIZE, cancel, &error) &&
          g_error_matches(error, G_IO_ERROR, G_IO_ERROR_CANCELLED), "cancellation between real write chunks reports partial failure");
    cancel_after_write = NULL; g_clear_error(&error);
    info = sumi_explorer_zip_io_query(io, NULL, &error);
    CHECK(info && g_file_info_get_size(info) == 65536, "cancelled write retains exactly the first completed owned chunk"); clear_info(&info);
    CHECK(sumi_explorer_zip_io_close(io, &error) && !error, "cancelled snapshot still closes without a cancellation parameter"); io = NULL;
    query_fault = FileBasicInfo;
    io = sumi_explorer_zip_new_snapshot(NULL, &error);
    CHECK(!io && error && active == 0 && GetFileAttributesW(last_snapshot) == INVALID_FILE_ATTRIBUTES,
          "snapshot metadata failure closes and deletes the newly owned object");
    query_fault = -1; g_clear_error(&error);
    io = sumi_explorer_zip_new_snapshot(NULL, &error);
    CHECK(io != NULL && !error, "create primary-error close control");
    if (io) {
        error = g_error_new_literal(G_IO_ERROR, G_IO_ERROR_FAILED, "preserved primary"); GError *primary = error;
        close_fault = 1;
        CHECK(!sumi_explorer_zip_io_close(io, &error) && error == primary && active == 0,
              "consuming close reports injected close failure while preserving the primary error"); io = NULL;
        g_clear_error(&error);
    }
done:
    if (io) sumi_explorer_zip_io_close(io, NULL);
    cancel_after_write = NULL; query_fault = -1; g_clear_error(&error); g_object_unref(cancel);
}

static char *without_file_id(GFileInfo *info)
{
    const char *id = attribute(info, G_FILE_ATTRIBUTE_ID_FILE), *etag = attribute(info, G_FILE_ATTRIBUTE_ETAG_VALUE);
    if (!id || !etag) return NULL;
    char *result = g_strdup(etag), *start = strstr(result, id);
    if (!start) { g_free(result); return NULL; }
    memmove(start + 1, start + strlen(id), strlen(start + strlen(id)) + 1); *start = 'I';
    return result;
}
static void test_identity(const WCHAR *path)
{
    char *name = utf8(path); GError *error = NULL;
    WCHAR retired[32768]; swprintf(retired, 32768, L"%ls.identity-retired", path);
    HANDLE old = CreateFileW(path, FILE_READ_ATTRIBUTES, sharing, NULL, OPEN_EXISTING, 0, NULL);
    FILE_BASIC_INFO basic;
    BOOL pinned = old != INVALID_HANDLE_VALUE && GetFileInformationByHandleEx(old, FileBasicInfo, &basic, sizeof(basic));
    CHECK(pinned, "pin actual original identity and native times");
    if (!pinned) { if (old != INVALID_HANDLE_VALUE) CloseHandle(old); g_free(name); return; }
    GFileInfo *before = sumi_explorer_zip_path_query(name, G_FILE_QUERY_INFO_NONE, NULL, &error);
    BOOL moved = MoveFileW(path, retired);
    CHECK(moved && write_fixture(path), "replace pathname while the retired original identity stays live");
    HANDLE replacement = CreateFileW(path, FILE_WRITE_ATTRIBUTES, sharing, NULL, OPEN_EXISTING, 0, NULL);
    CHECK(replacement != INVALID_HANDLE_VALUE && SetFileInformationByHandle(replacement, FileBasicInfo, &basic, sizeof(basic)), "equalize replacement native timestamps and attributes");
    if (replacement != INVALID_HANDLE_VALUE) CloseHandle(replacement);
    GFileInfo *after = sumi_explorer_zip_path_query(name, G_FILE_QUERY_INFO_NONE, NULL, &error);
    char *left = without_file_id(before), *right = without_file_id(after);
    CHECK(left && right && !strcmp(left, right), "replacement control calibrated: all etag fields except native file ID equal");
    CHECK(before && after && !same_attribute(before, after, G_FILE_ATTRIBUTE_ID_FILE), "same-byte equal-time replacement changes native ZIP file identity");
    g_free(left); g_free(right); clear_info(&before); clear_info(&after);
    CloseHandle(old); if (moved) CHECK(DeleteFileW(retired), "cleanup pinned retired identity fixture");
    g_clear_error(&error); g_free(name);
}

static void test_failures(const WCHAR *path)
{
    char *name = utf8(path); GError *error = NULL;
    FILE_INFO_BY_HANDLE_CLASS classes[] = {FileBasicInfo, FileStandardInfo, FileAttributeTagInfo, FileIdInfo};
    for (unsigned i = 0; i < G_N_ELEMENTS(classes); ++i) {
        query_fault = classes[i];
        GFileInfo *info = sumi_explorer_zip_path_query(name, G_FILE_QUERY_INFO_NONE, NULL, &error);
        CHECK(!info && error && active == 0, "required native query failure is explicit and balances path handles");
        clear_info(&info); g_clear_error(&error);
        SumiExplorerZipIO *io = sumi_explorer_zip_open_source(name, NULL, &error);
        CHECK(!io && error && active == 0, "source query failure closes the actual newly opened handle");
        if (io) sumi_explorer_zip_io_close(io, NULL);
        g_clear_error(&error);
    }
    query_fault = -1;
    provider = 1; identity_unavailable = ERROR_INVALID_FUNCTION;
    unsigned legacy_before = legacy_identity_calls;
    GFileInfo *info = sumi_explorer_zip_path_query(name, G_FILE_QUERY_INFO_NONE, NULL, &error);
    SumiExplorerZipIO *io = sumi_explorer_zip_open_source(name, NULL, &error);
    GFileInfo *opened_info = io ? sumi_explorer_zip_io_query(io, NULL, &error) : NULL;
    CHECK(info && opened_info && !error && legacy_identity_calls == legacy_before + 3 &&
          strstr(attribute(info, G_FILE_ATTRIBUTE_ID_FILE), "fat-id64:") == attribute(info, G_FILE_ATTRIBUTE_ID_FILE) &&
          same_attribute(info, opened_info, G_FILE_ATTRIBUTE_ID_FILESYSTEM) && same_attribute(info, opened_info, G_FILE_ATTRIBUTE_ID_FILE),
          "calibrated FAT provider fallback uses actual native64 identity consistently");
    clear_info(&info); clear_info(&opened_info); if (io) sumi_explorer_zip_io_close(io, NULL);
    provider = 2;
    info = sumi_explorer_zip_path_query(name, G_FILE_QUERY_INFO_NONE, NULL, &error);
    CHECK(!info && error, "ReFS never falls back to truncated64 identity"); clear_info(&info); g_clear_error(&error);
    provider = 1; identity_unavailable = ERROR_ACCESS_DENIED;
    legacy_before = legacy_identity_calls;
    info = sumi_explorer_zip_path_query(name, G_FILE_QUERY_INFO_NONE, NULL, &error);
    /* GLib's public Win32 converter returns FAILED for unmapped native codes;
     * 2.90.1 maps WSAEACCES, but not ERROR_ACCESS_DENIED, to PERMISSION_DENIED. */
    GIOErrorEnum mapped = g_io_error_from_win32_error(ERROR_ACCESS_DENIED);
    printf("DIAG FAT access-denial domain=%s code=%d mapped=%d message=%s legacy_before=%u legacy_after=%u\n",
        error ? g_quark_to_string(error->domain) : "<none>", error ? error->code : -1, mapped,
        error ? error->message : "<none>", legacy_before, legacy_identity_calls);
    CHECK(!info && g_error_matches(error, G_IO_ERROR, mapped) &&
          !strcmp(error->message, "Query full ZIP file identity failed (Win32 error 5)") &&
          legacy_identity_calls == legacy_before, "FAT identity access failure never becomes capability fallback");
    clear_info(&info); g_clear_error(&error); identity_unavailable = 0;
    provider = 2; high_identity = 1;
    HANDLE raw = CreateFileW(path, FILE_READ_ATTRIBUTES, sharing, NULL, OPEN_EXISTING, 0, NULL);
    FILE_ID_INFO native_identity; char expected[39] = "id128:";
    BOOL native_read = raw != INVALID_HANDLE_VALUE && GetFileInformationByHandleEx(raw, FileIdInfo, &native_identity, sizeof(native_identity));
    if (raw != INVALID_HANDLE_VALUE) CloseHandle(raw);
    if (native_read) {
        native_identity.FileId.Identifier[15] ^= 0x7f;
        for (unsigned i = 0; i < 16; ++i) g_snprintf(expected + 6 + i * 2, 3, "%02x", native_identity.FileId.Identifier[i]);
    }
    info = sumi_explorer_zip_path_query(name, G_FILE_QUERY_INFO_NONE, NULL, &error);
    const char *id = attribute(info, G_FILE_ATTRIBUTE_ID_FILE);
    CHECK(native_read && info && id && !strcmp(id, expected), "calibrated128 provider preserves all high identity bytes");
    clear_info(&info); g_clear_error(&error); provider = high_identity = 0;
    printf("NOTRUN physical FAT/ReFS volume runtime; provider fallback/high-ID controls alter only selected successful native query results\n");
    g_free(name);
}

int main(int argc, char **argv)
{
    omit_identity = argc == 2 && !strcmp(argv[1], "--omit-file-id");
    for (gsize i = 0; i < PAYLOAD_SIZE; ++i) payload[i] = (unsigned char)(i * 37 + (i >> 8) * 13 + (i >> 16) * 73);
    WCHAR temp[32768], root[MAX_PATH], path[MAX_PATH];
    if (!GetTempPathW(32768, temp) || !GetTempFileNameW(temp, L"szp", 0, root)) return 2;
    DeleteFileW(root); if (!CreateDirectoryW(root, NULL)) return 2;
    SetEnvironmentVariableW(L"TMP", root); SetEnvironmentVariableW(L"TEMP", root);
    swprintf(path, MAX_PATH, L"%ls\\\u65e5\u672c\u8a9e source.bin", root);
    CHECK(write_fixture(path), "create actual Unicode distinct multi-chunk fixture");
    test_source(path, root); test_snapshot(); test_failures(path); test_identity(path);
    CHECK(!active && opened == closed && !close_failures, "all ZIP bridge acquired file handles close exactly once");
    CHECK(DeleteFileW(path) && RemoveDirectoryW(root), "owned ZIP fixture cleanup");
    printf("RESULT Windows ZIP checks=%u failures=%u opens=%u closes=%u active=%u\n", checks, failures, opened, closed, active);
    return failures ? 1 : 0;
}
#else
#include <platform-zip.inc>
int main(void)
{
    GError *error = NULL;
#define UNSUPPORTED(expr, expected, label) do { CHECK((expr) == (expected) && \
    g_error_matches(error, G_IO_ERROR, G_IO_ERROR_NOT_SUPPORTED), label); g_clear_error(&error); } while (0)
    UNSUPPORTED(sumi_explorer_zip_path_query("/fixture", 0, NULL, &error), NULL, "Linux ZIP path query is explicitly unsupported");
    UNSUPPORTED(sumi_explorer_zip_open_source("/fixture", NULL, &error), NULL, "Linux ZIP source open is explicitly unsupported");
    UNSUPPORTED(sumi_explorer_zip_new_snapshot(NULL, &error), NULL, "Linux ZIP snapshot is explicitly unsupported");
    UNSUPPORTED(sumi_explorer_zip_io_read(NULL, NULL, 0, NULL, &error), -1, "Linux ZIP read is explicitly unsupported");
    UNSUPPORTED(sumi_explorer_zip_io_write_all(NULL, NULL, 0, NULL, &error), FALSE, "Linux ZIP write is explicitly unsupported");
    UNSUPPORTED(sumi_explorer_zip_io_seek(NULL, 0, G_SEEK_SET, NULL, &error), -1, "Linux ZIP seek is explicitly unsupported");
    UNSUPPORTED(sumi_explorer_zip_io_query(NULL, NULL, &error), NULL, "Linux ZIP handle query is explicitly unsupported");
    UNSUPPORTED(sumi_explorer_zip_io_close(NULL, &error), FALSE, "Linux ZIP close is explicitly unsupported");
    GCancellable *cancel = g_cancellable_new(); g_cancellable_cancel(cancel);
    UNSUPPORTED(sumi_explorer_zip_new_snapshot(cancel, &error), NULL, "cancelled Linux request still reports the unavailable platform capability");
    g_object_unref(cancel);
    error = g_error_new_literal(G_IO_ERROR, G_IO_ERROR_FAILED, "primary"); GError *primary = error;
    CHECK(!sumi_explorer_zip_io_close(NULL, &error) && error == primary && !strcmp(error->message, "primary"), "Linux stub preserves an existing primary GError");
    g_clear_error(&error);
    CHECK(!sumi_explorer_zip_open_source(NULL, NULL, NULL), "Linux unsupported stub accepts an optional error output");
    printf("RESULT Linux ZIP stub checks=%u failures=%u; no Windows runtime evidence\n", checks, failures);
    return failures ? 1 : 0;
}
#endif
