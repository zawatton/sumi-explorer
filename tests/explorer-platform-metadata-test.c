/* Native NTFS capability acceptance. Fault/omission controls still execute the
 * real adapter and real filesystem APIs; only the selected query is altered. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#ifdef _WIN32
#define COBJMACROS
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <aclapi.h>
#include <winioctl.h>
#include <wchar.h>
static int injected_query = -1, injected_provider, active_handles, close_failures;
static BOOL WINAPI observed_query(HANDLE file, FILE_INFO_BY_HANDLE_CLASS kind, void *output, DWORD size)
{
    if ((int)kind == injected_query) { SetLastError(ERROR_ACCESS_DENIED); return FALSE; }
    BOOL result = GetFileInformationByHandleEx(file, kind, output, size);
#ifdef SUMI_TEST_OMIT_CHANGE_TIME
    if (result && kind == FileBasicInfo) ((FILE_BASIC_INFO *)output)->ChangeTime.QuadPart = 0;
#endif
#ifdef SUMI_TEST_OMIT_FILE_ID
    if (result && kind == FileIdInfo) memset(&((FILE_ID_INFO *)output)->FileId, 0, sizeof(FILE_ID_128));
#endif
    if (result && injected_provider == 6 && kind == FileStandardInfo)
        ((FILE_STANDARD_INFO *)output)->DeletePending = TRUE;
    return result;
}
static HANDLE WINAPI observed_open(LPCWSTR name, DWORD access, DWORD share,
    LPSECURITY_ATTRIBUTES security, DWORD disposition, DWORD flags, HANDLE template)
{
    if (injected_provider == 5) { SetLastError(ERROR_ACCESS_DENIED); return INVALID_HANDLE_VALUE; }
    HANDLE file = CreateFileW(name, access, share, security, disposition, flags, template);
    if (file != INVALID_HANDLE_VALUE) ++active_handles;
    return file;
}
static BOOL WINAPI observed_close(HANDLE file)
{
    BOOL result = CloseHandle(file);
    if (result) --active_handles;
    else ++close_failures;
    return result;
}
static DWORD WINAPI observed_final(HANDLE file, LPWSTR output, DWORD capacity, DWORD flags)
{
    if (injected_provider == 4) { SetLastError(ERROR_NOT_SUPPORTED); return 0; }
    return GetFinalPathNameByHandleW(file, output, capacity, flags);
}
static UINT WINAPI observed_drive(LPCWSTR path)
{ return injected_provider == 2 ? DRIVE_REMOTE : GetDriveTypeW(path); }
static BOOL WINAPI observed_volume(HANDLE file, LPWSTR name, DWORD name_size, LPDWORD serial,
    LPDWORD maximum, LPDWORD flags, LPWSTR filesystem, DWORD capacity)
{
    if (injected_provider == 3) { SetLastError(ERROR_ACCESS_DENIED); return FALSE; }
    BOOL result = GetVolumeInformationByHandleW(file, name, name_size, serial, maximum, flags, filesystem, capacity);
    if (result && injected_provider == 1 && capacity >= 5) wcscpy(filesystem, L"ReFS");
    return result;
}
#define GetFileInformationByHandleEx observed_query
#define CreateFileW observed_open
#define CloseHandle observed_close
#define GetFinalPathNameByHandleW observed_final
#define GetDriveTypeW observed_drive
#define GetVolumeInformationByHandleW observed_volume
#endif
#include <platform.c>
#ifdef _WIN32
#undef GetFileInformationByHandleEx
#undef CreateFileW
#undef CloseHandle
#undef GetFinalPathNameByHandleW
#undef GetDriveTypeW
#undef GetVolumeInformationByHandleW
#endif

static unsigned failures;
#define CHECK(condition, name) do { int ok = !!(condition); \
    printf("%s %s\n", ok ? "PASS" : "FAIL", name); fflush(stdout); if (!ok) ++failures; \
} while (0)

#ifdef _WIN32
static const char fixture_bytes[] = "metadata-fixture";
static const DWORD sharing = FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE;

static char *utf8(const WCHAR *path)
{
    int length = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, path, -1, NULL, 0, NULL, NULL);
    char *result = length > 0 ? malloc((size_t)length) : NULL;
    if (result) WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, path, -1, result, length, NULL, NULL);
    return result;
}
static char *stamp(const WCHAR *path)
{
    char *name = utf8(path), *error = NULL;
    char *result = name ? sumi_explorer_platform_metadata_stamp(name, &error) : NULL;
    if (!result) printf("DIAG stamp error=%s\n", error ? error : "<encoding/allocation>");
    CHECK(result && !error && strlen(result) < 512 && !strncmp(result, "ntfs-v1|", 8), "bounded NTFS stamp capability");
    free(name); g_free(error);
    return result;
}
static void rejected(const char *name, const char *reason)
{
    char *error = NULL;
    char *result = sumi_explorer_platform_metadata_stamp(name, &error);
    CHECK(!result && error && *error && active_handles == 0, reason);
    if (result) g_free(result);
    g_free(error);
}
static int write_fixture(const WCHAR *path, const char *bytes, DWORD length)
{
    HANDLE file = CreateFileW(path, GENERIC_WRITE, sharing, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    DWORD written = 0;
    int result = file != INVALID_HANDLE_VALUE && WriteFile(file, bytes, length, &written, NULL) && written == length;
    if (file != INVALID_HANDLE_VALUE) CloseHandle(file);
    return result;
}
static int same_bytes(const WCHAR *path)
{
    char bytes[sizeof(fixture_bytes)] = {0}; DWORD read = 0;
    HANDLE file = CreateFileW(path, GENERIC_READ, sharing, NULL, OPEN_EXISTING, 0, NULL);
    int result = file != INVALID_HANDLE_VALUE && ReadFile(file, bytes, sizeof(bytes), &read, NULL) &&
        read == sizeof(fixture_bytes) && !memcmp(bytes, fixture_bytes, sizeof(bytes));
    if (file != INVALID_HANDLE_VALUE) CloseHandle(file);
    return result;
}
static int equal_except(const char *a, const char *b, const char *field)
{
    char left[512], right[512];
    if (!a || !b || strlen(a) >= sizeof(left) || strlen(b) >= sizeof(right)) return 0;
    strcpy(left, a); strcpy(right, b);
    char *start = strstr(left, field), *end = start ? strchr(start + 1, '|') : NULL;
    if (!start || !end) return 0;
    memmove(start, end, strlen(end) + 1);
    start = strstr(right, field); end = start ? strchr(start + 1, '|') : NULL;
    if (!start || !end) return 0;
    memmove(start, end, strlen(end) + 1);
    return !strcmp(left, right);
}

static void test_acl(const WCHAR *path)
{
    HANDLE token = NULL, file = INVALID_HANDLE_VALUE;
    DWORD required = 0;
    TOKEN_USER *user = NULL;
    PSECURITY_DESCRIPTOR descriptor = NULL, changed_descriptor = NULL;
    PACL old_acl = NULL, actual_acl = NULL, changed_acl = NULL;
    char *before = stamp(path), *after = NULL;
    if (!OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &token)) { CHECK(0, "open own token"); goto done; }
    GetTokenInformation(token, TokenUser, NULL, 0, &required);
    user = malloc(required);
    if (!user || !GetTokenInformation(token, TokenUser, user, required, &required)) { CHECK(0, "read own SID"); goto done; }
    file = CreateFileW(path, READ_CONTROL | WRITE_DAC, sharing, NULL, OPEN_EXISTING, 0, NULL);
    if (file == INVALID_HANDLE_VALUE || GetSecurityInfo(file, SE_FILE_OBJECT, DACL_SECURITY_INFORMATION,
        NULL, NULL, &old_acl, NULL, &descriptor) != ERROR_SUCCESS || !old_acl) {
        CHECK(0, "read fixture original DACL"); goto done;
    }
    DWORD capacity = old_acl->AclSize + GetLengthSid(user->User.Sid) + sizeof(ACCESS_ALLOWED_ACE);
    changed_acl = malloc(capacity);
    if (!changed_acl || !InitializeAcl(changed_acl, capacity, ACL_REVISION)) { CHECK(0, "allocate fixture DACL"); goto done; }
    for (WORD i = 0; i < old_acl->AceCount; ++i) {
        void *ace = NULL;
        if (!GetAce(old_acl, i, &ace) || !AddAce(changed_acl, ACL_REVISION, MAXDWORD, ace, ((ACE_HEADER *)ace)->AceSize)) {
            CHECK(0, "copy fixture DACL entries"); goto done;
        }
    }
    CHECK(AddAccessAllowedAceEx(changed_acl, ACL_REVISION, 0, FILE_READ_EA, user->User.Sid), "add harmless own-SID permission ACE");
    Sleep(40);
    DWORD set_result = SetSecurityInfo(file, SE_FILE_OBJECT, DACL_SECURITY_INFORMATION, NULL, NULL, changed_acl, NULL);
    CHECK(set_result == ERROR_SUCCESS, "apply genuine DACL-only change on fixture");
    DWORD read_result = GetSecurityInfo(file, SE_FILE_OBJECT, DACL_SECURITY_INFORMATION,
        NULL, NULL, &actual_acl, NULL, &changed_descriptor);
    CHECK(read_result == ERROR_SUCCESS && actual_acl && actual_acl->AceCount == old_acl->AceCount + 1,
        "DACL control actually changed security descriptor");
    CloseHandle(file); file = INVALID_HANDLE_VALUE;
    after = stamp(path);
    printf("DIAG DACL before=%s after=%s\n", before ? before : "<none>", after ? after : "<none>");
    CHECK(equal_except(before, after, "|changed="), "DACL control calibrated: all serialized fields except ChangeTime equal");
    CHECK(before && after && strcmp(before, after), "DACL-only modification changes stamp");
    CHECK(same_bytes(path), "DACL-only modification preserves default-stream bytes");
    file = CreateFileW(path, WRITE_DAC, sharing, NULL, OPEN_EXISTING, 0, NULL);
    CHECK(file != INVALID_HANDLE_VALUE && SetSecurityInfo(file, SE_FILE_OBJECT, DACL_SECURITY_INFORMATION,
          NULL, NULL, old_acl, NULL) == ERROR_SUCCESS, "restore only fixture original DACL");
    /* Keep the already authorized WRITE_DAC handle while denying only our
     * fixture's READ_ATTRIBUTES access, so restoration cannot lock us out. */
    if (file != INVALID_HANDLE_VALUE) {
        CHECK(InitializeAcl(changed_acl, capacity, ACL_REVISION) &&
              AddAccessDeniedAceEx(changed_acl, ACL_REVISION, 0, FILE_READ_ATTRIBUTES, user->User.Sid),
              "construct fixture-only read-attributes denial");
        for (WORD i = 0; i < old_acl->AceCount; ++i) {
            void *ace = NULL;
            CHECK(GetAce(old_acl, i, &ace) && AddAce(changed_acl, ACL_REVISION, MAXDWORD,
                ace, ((ACE_HEADER *)ace)->AceSize), "retain original permission entries after denial");
        }
        CHECK(SetSecurityInfo(file, SE_FILE_OBJECT, DACL_SECURITY_INFORMATION,
            NULL, NULL, changed_acl, NULL) == ERROR_SUCCESS, "apply fixture-only read-attributes denial");
        char *name = utf8(path), *denied_error = NULL;
        char *denied_stamp = sumi_explorer_platform_metadata_stamp(name, &denied_error);
        CHECK(!denied_stamp && denied_error && strstr(denied_error, "Win32 error 5") && !active_handles,
              "genuine DACL read-attributes denial is explicit");
        free(name); g_free(denied_error); g_free(denied_stamp);
        CHECK(SetSecurityInfo(file, SE_FILE_OBJECT, DACL_SECURITY_INFORMATION,
            NULL, NULL, old_acl, NULL) == ERROR_SUCCESS, "restore fixture ACL after access-denial control");
    }
done:
    if (file != INVALID_HANDLE_VALUE) CloseHandle(file);
    if (token) CloseHandle(token);
    if (descriptor) LocalFree(descriptor);
    if (changed_descriptor) LocalFree(changed_descriptor);
    free(user); free(changed_acl); g_free(before); g_free(after);
}

static void test_identity(const WCHAR *path)
{
    FILE_BASIC_INFO original = {0};
    HANDLE old = CreateFileW(path, FILE_READ_ATTRIBUTES, sharing, NULL, OPEN_EXISTING, 0, NULL);
    CHECK(old != INVALID_HANDLE_VALUE && GetFileInformationByHandleEx(old, FileBasicInfo, &original, sizeof(original)),
          "read replacement control timestamps");
    char *before = stamp(path);
    CHECK(DeleteFileW(path) && write_fixture(path, fixture_bytes, sizeof(fixture_bytes)), "same-byte replacement created");
    HANDLE replacement = CreateFileW(path, FILE_WRITE_ATTRIBUTES, sharing, NULL, OPEN_EXISTING, 0, NULL);
    CHECK(replacement != INVALID_HANDLE_VALUE && SetFileInformationByHandle(replacement, FileBasicInfo, &original, sizeof(original)),
          "equalize all replacement timestamps and attributes");
    if (replacement != INVALID_HANDLE_VALUE) CloseHandle(replacement);
    char *after = stamp(path);
    printf("DIAG identity before=%s after=%s\n", before ? before : "<none>", after ? after : "<none>");
    CHECK(equal_except(before, after, "|id="), "identity control calibrated: all fields except file ID equal");
    CHECK(before && after && strcmp(before, after), "same-byte replacement changes file identity stamp");
    CHECK(same_bytes(path), "replacement control preserves exact bytes");
    if (old != INVALID_HANDLE_VALUE) CloseHandle(old);
    g_free(before); g_free(after);
}

static void test_faults(const WCHAR *path)
{
    char *name = utf8(path);
    DWORD before = 0, after = 0;
    GetProcessHandleCount(GetCurrentProcess(), &before);
    FILE_INFO_BY_HANDLE_CLASS queries[] = {FileIdInfo, FileBasicInfo, FileStandardInfo, FileAttributeTagInfo};
    for (unsigned round = 0; round < 8; ++round) {
        for (unsigned i = 0; i < sizeof(queries) / sizeof(queries[0]); ++i) {
            injected_query = queries[i]; rejected(name, "required metadata query failure is explicit and closes handles");
        }
        injected_query = -1;
        for (injected_provider = 1; injected_provider <= 6; ++injected_provider)
            rejected(name, "unsupported provider/open/volume/delete-pending control rejected");
        injected_provider = 0;
    }
    GetProcessHandleCount(GetCurrentProcess(), &after);
    CHECK(active_handles == 0 && !close_failures && before == after, "all injected paths close each acquired handle without leaks");
    char *sentinel = g_strdup("preserved diagnostic"), *error = sentinel;
    CHECK(!sumi_explorer_platform_metadata_stamp(name, &error) && error == sentinel, "preexisting error is preserved");
    g_free(error); free(name);
}

static void test_junction(const WCHAR *root, const WCHAR *target_file)
{
    WCHAR junction[2048], child[2048], substitute[2048];
    swprintf(junction, 2048, L"%ls\\junction", root);
    swprintf(child, 2048, L"%ls\\junction\\\u65e5\u672c\u8a9e.txt", root);
    swprintf(substitute, 2048, L"\\??\\%ls", root);
    CHECK(CreateDirectoryW(junction, NULL), "create owned junction fixture directory");
    HANDLE file = CreateFileW(junction, GENERIC_WRITE, sharing, NULL, OPEN_EXISTING,
                              FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS, NULL);
    struct {
        DWORD tag; WORD length, reserved, substitute_offset, substitute_length, print_offset, print_length;
        WCHAR names[4096];
    } data = {0};
    data.tag = IO_REPARSE_TAG_MOUNT_POINT;
    data.substitute_length = (WORD)(wcslen(substitute) * sizeof(WCHAR));
    data.print_offset = data.substitute_length + sizeof(WCHAR);
    data.print_length = (WORD)(wcslen(root) * sizeof(WCHAR));
    memcpy(data.names, substitute, data.substitute_length);
    memcpy((char *)data.names + data.print_offset, root, data.print_length);
    data.length = (WORD)(8 + data.print_offset + data.print_length + sizeof(WCHAR));
    DWORD returned = 0;
    CHECK(file != INVALID_HANDLE_VALUE && DeviceIoControl(file, FSCTL_SET_REPARSE_POINT, &data,
        data.length + 8, NULL, 0, &returned, NULL), "install genuine junction without elevation");
    if (file != INVALID_HANDLE_VALUE) CloseHandle(file);
    char *name = utf8(junction); rejected(name, "reparse object rejected without following target"); free(name);
    name = utf8(child); rejected(name, "intermediate junction rejected without following target"); free(name);
    CHECK(same_bytes(target_file), "reparse rejection preserves target bytes");
    CHECK(RemoveDirectoryW(junction), "junction fixture cleanup");
}

int main(void)
{
    WCHAR temp[MAX_PATH], root[MAX_PATH], path[2048], alias[2048], link[2048], stream[2048];
    if (!GetTempPathW(MAX_PATH, temp) || !GetTempFileNameW(temp, L"smd", 0, root)) return 2;
    DeleteFileW(root);
    if (!CreateDirectoryW(root, NULL)) return 2;
    swprintf(path, 2048, L"%ls\\\u65e5\u672c\u8a9e.txt", root);
    swprintf(alias, 2048, L"%ls\\.\\unused\\..\\\u65e5\u672c\u8a9e.txt", root);
    swprintf(link, 2048, L"%ls\\hard-link.txt", root);
    swprintf(stream, 2048, L"%ls:sumi-test", path);
    CHECK(write_fixture(path, fixture_bytes, sizeof(fixture_bytes)), "create Unicode metadata fixture");
    HANDLE info = CreateFileW(path, FILE_READ_ATTRIBUTES, sharing, NULL, OPEN_EXISTING, 0, NULL);
    WCHAR fs[32] = {0};
    CHECK(info != INVALID_HANDLE_VALUE && GetVolumeInformationByHandleW(info, NULL, 0, NULL, NULL, NULL, fs, 32), "query actual fixture filesystem");
    if (info != INVALID_HANDLE_VALUE) CloseHandle(info);
    printf("DIAG actual_filesystem=%ls; OS recorded in environment.json\n", fs);
    char *first = stamp(path);
    CHECK(same_bytes(path), "read-only default-stream probe before repeated stamp");
    char *again = stamp(path), *aliased = stamp(alias);
    CHECK(first && again && aliased && !strcmp(first, again) && !strcmp(first, aliased), "reads and lexical aliases preserve stamp");
    CHECK(CreateHardLinkW(link, path, NULL), "create real hard-link alias");
    g_free(first); g_free(again); g_free(aliased);
    first = stamp(path); again = stamp(link);
    CHECK(first && again && !strcmp(first, again), "hard-link aliases identify same current object including link count");
    g_free(first); g_free(again); CHECK(DeleteFileW(link), "hard-link fixture cleanup");
    first = stamp(path); DWORD original_attributes = GetFileAttributesW(path);
    CHECK(SetFileAttributesW(path, original_attributes | FILE_ATTRIBUTE_READONLY), "change only fixture read-only attribute");
    again = stamp(path);
    CHECK(first && again && strcmp(first, again) && same_bytes(path), "read-only metadata changes stamp and preserves bytes");
    CHECK(SetFileAttributesW(path, original_attributes), "restore fixture attributes");
    g_free(first); g_free(again);
    test_acl(path); test_identity(path);
    first = stamp(path); Sleep(40);
    CHECK(write_fixture(stream, "12345678", 8), "create actual ADS"); again = stamp(path);
    CHECK(first && again && strcmp(first, again), "ADS creation changes base-file stamp");
    g_free(first); first = again; Sleep(40);
    CHECK(write_fixture(stream, "87654321", 8), "modify ADS with same length"); again = stamp(path);
    CHECK(first && again && strcmp(first, again), "same-length ADS modification changes base-file stamp");
    g_free(first); first = again; Sleep(40);
    CHECK(DeleteFileW(stream), "delete actual ADS"); again = stamp(path);
    CHECK(first && again && strcmp(first, again) && same_bytes(path), "ADS deletion changes stamp without default-stream mutation");
    g_free(first); g_free(again);
    first = stamp(root); Sleep(40);
    CHECK(write_fixture(link, fixture_bytes, sizeof(fixture_bytes)), "create directory child"); again = stamp(root);
    CHECK(first && again && strcmp(first, again), "directory child change detected");
    g_free(first); g_free(again); CHECK(DeleteFileW(link), "directory child cleanup");
    first = stamp(root); original_attributes = GetFileAttributesW(root);
    CHECK(SetFileAttributesW(root, original_attributes | FILE_ATTRIBUTE_HIDDEN), "change directory metadata"); again = stamp(root);
    CHECK(first && again && strcmp(first, again), "directory metadata change detected");
    CHECK(SetFileAttributesW(root, original_attributes), "restore directory attributes");
    g_free(first); g_free(again);
    test_junction(root, path); test_faults(path);
    const char *invalid[] = {NULL, "", "relative", "C:relative", "\\\\server\\share\\file", "\\\\?\\C:\\file",
        "C:\\file:stream", "C:\\CON", "C:\\NUL.txt", "C:\\bad-\xed\xa0\x80"};
    for (unsigned i = 0; i < sizeof(invalid) / sizeof(invalid[0]); ++i) rejected(invalid[i], "invalid/device/ADS/network path rejected");
    WCHAR long_path[2048], extended[2052]; wcscpy(long_path, root);
    for (unsigned i = 0; i < 16; ++i) {
        wcscat(long_path, L"\\long-component-123456"); swprintf(extended, 2052, L"\\\\?\\%ls", long_path);
        CHECK(CreateDirectoryW(extended, NULL), "create long-path component");
    }
    swprintf(extended, 2052, L"\\\\?\\%ls\\file.txt", long_path);
    CHECK(write_fixture(extended, fixture_bytes, sizeof(fixture_bytes)), "create file beyond MAX_PATH");
    wcscat(long_path, L"\\file.txt"); first = stamp(long_path);
    CHECK(first != NULL, "ordinary long UTF-8 path deliberately supported"); g_free(first);
    CHECK(DeleteFileW(extended), "long-path file cleanup");
    *wcsrchr(long_path, '\\') = 0;
    for (unsigned i = 0; i < 16; ++i) {
        swprintf(extended, 2052, L"\\\\?\\%ls", long_path);
        CHECK(RemoveDirectoryW(extended), "long-path component cleanup"); *wcsrchr(long_path, '\\') = 0;
    }
    CHECK(DeleteFileW(path) && RemoveDirectoryW(root), "metadata fixture cleanup");
    CHECK(!active_handles && !close_failures, "no adapter handles remain");
    printf("RESULT metadata failures=%u\n", failures);
    return failures ? 1 : 0;
}
#else
int main(void)
{
    char *error = NULL;
    CHECK(!sumi_explorer_platform_metadata_stamp("/fixture/file", &error) && error && strstr(error, "unsupported"), "non-Windows metadata explicitly unsupported");
    g_free(error); error = g_strdup("sentinel"); char *saved = error;
    CHECK(!sumi_explorer_platform_metadata_stamp("/fixture/file", &error) && error == saved && !strcmp(error, "sentinel"), "metadata preserves existing diagnostic");
    g_free(error); error = NULL;
    CHECK(!sumi_explorer_platform_metadata_stamp(NULL, &error) && error, "invalid metadata path fails without placeholder");
    g_free(error);
    CHECK(!sumi_explorer_platform_metadata_stamp("/fixture/file", NULL), "optional error output supported");
    return failures ? 1 : 0;
}
#endif
