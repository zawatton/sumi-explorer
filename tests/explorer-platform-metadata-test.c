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
#include <processsnapshot.h>
#include <wchar.h>
static int injected_query = -1, injected_provider, active_handles, close_failures;
static unsigned observed_opens, observed_closes;
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
    if (file != INVALID_HANDLE_VALUE) { ++active_handles; ++observed_opens; }
    return file;
}
static BOOL WINAPI observed_close(HANDLE file)
{
    BOOL result = CloseHandle(file);
    if (result) { --active_handles; ++observed_closes; }
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

static BOOL direct_metadata_open(const WCHAR *path, const char *stage, DWORD flags, int require_denied)
{
    WCHAR extended[2052]; swprintf(extended, 2052, L"\\\\?\\%ls", path);
    HANDLE file = CreateFileW(extended, FILE_READ_ATTRIBUTES, sharing, NULL, OPEN_EXISTING, flags, NULL);
    DWORD code = file == INVALID_HANDLE_VALUE ? GetLastError() : ERROR_SUCCESS;
    printf("DIAG direct-open stage=%s flags=%08lx success=%d error=%lu\n", stage,
           (unsigned long)flags, file != INVALID_HANDLE_VALUE, (unsigned long)code);
    if (require_denied) CHECK(file == INVALID_HANDLE_VALUE && code == ERROR_ACCESS_DENIED,
                              "same-flags native open confirms fixture access denial");
    if (file != INVALID_HANDLE_VALUE) CloseHandle(file);
    return file == INVALID_HANDLE_VALUE && code == ERROR_ACCESS_DENIED;
}

static BOOL test_restricted_denial(const WCHAR *path, HANDLE process_token)
{
    HANDLE restricted = NULL, impersonation = NULL, existing = NULL;
    BOOL restored = TRUE;
    DWORD size = 0;
    GetTokenInformation(process_token, TokenPrivileges, NULL, 0, &size);
    TOKEN_PRIVILEGES *privileges = malloc(size);
    LUID backup = {0}, restore = {0};
    CHECK(LookupPrivilegeValueW(NULL, L"SeBackupPrivilege", &backup) &&
          LookupPrivilegeValueW(NULL, L"SeRestorePrivilege", &restore), "lookup backup/restore privilege identifiers");
    BOOL read_privileges = privileges && GetTokenInformation(process_token, TokenPrivileges, privileges, size, &size);
    CHECK(read_privileges, "read actual process-token privileges");
    if (read_privileges) {
        for (DWORD i = 0; i < privileges->PrivilegeCount; ++i) {
            LUID id = privileges->Privileges[i].Luid;
            if ((id.LowPart == backup.LowPart && id.HighPart == backup.HighPart) ||
                (id.LowPart == restore.LowPart && id.HighPart == restore.HighPart))
                printf("DIAG process-privilege luid=%lu:%ld attributes=%08lx\n", (unsigned long)id.LowPart,
                       (long)id.HighPart, (unsigned long)privileges->Privileges[i].Attributes);
        }
    }
    free(privileges);
    const DWORD flags = FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS;
    direct_metadata_open(path, "process-token/exact-flags", flags, 0);
    direct_metadata_open(path, "process-token/without-backup", FILE_FLAG_OPEN_REPARSE_POINT, 0);
    BOOL had_token = OpenThreadToken(GetCurrentThread(), TOKEN_QUERY, TRUE, &existing);
    DWORD token_error = had_token ? ERROR_SUCCESS : GetLastError();
    CHECK(!had_token && token_error == ERROR_NO_TOKEN, "fresh test thread has no preexisting impersonation");
    if (existing) CloseHandle(existing);
    if (had_token || token_error != ERROR_NO_TOKEN) return TRUE;
    BOOL created = CreateRestrictedToken(process_token, DISABLE_MAX_PRIVILEGE, 0, NULL, 0, NULL, 0, NULL, &restricted);
    BOOL duplicated = created && DuplicateTokenEx(restricted, TOKEN_QUERY | TOKEN_IMPERSONATE,
        NULL, SecurityImpersonation, TokenImpersonation, &impersonation);
    CHECK(duplicated, "create own thread-only token with privileges disabled");
    if (duplicated) {
        size = 0; GetTokenInformation(impersonation, TokenPrivileges, NULL, 0, &size);
        privileges = malloc(size);
        read_privileges = privileges && GetTokenInformation(impersonation, TokenPrivileges, privileges, size, &size);
        BOOL elevated_backup = FALSE;
        if (read_privileges) for (DWORD i = 0; i < privileges->PrivilegeCount; ++i) {
            LUID id = privileges->Privileges[i].Luid;
            if ((id.LowPart == backup.LowPart && id.HighPart == backup.HighPart) ||
                (id.LowPart == restore.LowPart && id.HighPart == restore.HighPart)) {
                printf("DIAG restricted-privilege luid=%lu:%ld attributes=%08lx\n", (unsigned long)id.LowPart,
                       (long)id.HighPart, (unsigned long)privileges->Privileges[i].Attributes);
                elevated_backup |= !!(privileges->Privileges[i].Attributes & SE_PRIVILEGE_ENABLED);
            }
        }
        CHECK(read_privileges && !elevated_backup, "restricted thread token has no enabled backup/restore privileges");
        free(privileges);
    }
    BOOL impersonated = duplicated && SetThreadToken(NULL, impersonation);
    CHECK(impersonated, "temporarily use restricted token on this test thread only");
    if (impersonated) {
        if (direct_metadata_open(path, "restricted-thread/exact-flags", flags, 1)) {
            char *name = utf8(path), *error = NULL;
            char *result = sumi_explorer_platform_metadata_stamp(name, &error);
            printf("DIAG restricted denied stamp=%s error=%s\n", result ? result : "<none>", error ? error : "<none>");
            CHECK(!result && error && strstr(error, "Win32 error 5") && !active_handles,
                  "genuine DACL read-attributes denial is explicit");
            free(name); g_free(error); g_free(result);
        }
        restored = RevertToSelf();
        CHECK(restored, "restore original test-thread security context");
    }
    if (impersonation) CloseHandle(impersonation);
    if (restricted) CloseHandle(restricted);
    return restored;
}

static PACL acl_with_denial(PACL original, PSID sid, DWORD mask)
{
    DWORD capacity = original->AclSize + GetLengthSid(sid) + sizeof(ACCESS_DENIED_ACE);
    PACL result = malloc(capacity);
    if (!result || !InitializeAcl(result, capacity, ACL_REVISION) ||
        !AddAccessDeniedAceEx(result, ACL_REVISION, 0, mask, sid)) { free(result); return NULL; }
    for (WORD i = 0; i < original->AceCount; ++i) {
        void *ace = NULL;
        if (!GetAce(original, i, &ace) || !AddAce(result, ACL_REVISION, MAXDWORD, ace,
            ((ACE_HEADER *)ace)->AceSize)) { free(result); return NULL; }
    }
    return result;
}

static BOOL verify_denial(HANDLE file, PSID sid, DWORD mask, const char *label)
{
    PSECURITY_DESCRIPTOR descriptor = NULL; PACL acl = NULL; void *first = NULL;
    DWORD code = GetSecurityInfo(file, SE_FILE_OBJECT, DACL_SECURITY_INFORMATION,
        NULL, NULL, &acl, NULL, &descriptor);
    BOOL valid = code == ERROR_SUCCESS && acl && GetAce(acl, 0, &first) &&
        ((ACE_HEADER *)first)->AceType == ACCESS_DENIED_ACE_TYPE &&
        ((ACE_HEADER *)first)->AceFlags == 0 && ((ACCESS_DENIED_ACE *)first)->Mask == mask &&
        EqualSid(&((ACCESS_DENIED_ACE *)first)->SidStart, sid);
    printf("DIAG %s query=%lu own_sid_noninherit_denial=%d mask=%08lx\n", label,
           (unsigned long)code, valid, (unsigned long)mask);
    CHECK(valid, label);
    if (descriptor) LocalFree(descriptor);
    return valid;
}

static void restore_and_verify_acl(HANDLE file, PACL original, const char *label)
{
    PSECURITY_DESCRIPTOR descriptor = NULL; PACL actual = NULL;
    DWORD restored = SetSecurityInfo(file, SE_FILE_OBJECT, DACL_SECURITY_INFORMATION,
        NULL, NULL, original, NULL);
    DWORD queried = GetSecurityInfo(file, SE_FILE_OBJECT, DACL_SECURITY_INFORMATION,
        NULL, NULL, &actual, NULL, &descriptor);
    BOOL equal = restored == ERROR_SUCCESS && queried == ERROR_SUCCESS && actual &&
        actual->AclRevision == original->AclRevision && actual->AceCount == original->AceCount;
    for (WORD i = 0; equal && i < original->AceCount; ++i) {
        void *left = NULL, *right = NULL;
        equal = GetAce(original, i, &left) && GetAce(actual, i, &right) &&
            ((ACE_HEADER *)left)->AceSize == ((ACE_HEADER *)right)->AceSize &&
            !memcmp(left, right, ((ACE_HEADER *)left)->AceSize);
    }
    printf("DIAG %s restore=%lu query=%lu original_aces_equal=%d\n", label,
           (unsigned long)restored, (unsigned long)queried, equal);
    CHECK(equal, label);
    if (descriptor) LocalFree(descriptor);
}

static void test_parent_child_denial(const WCHAR *path, const WCHAR *root, HANDLE token, PSID sid)
{
    HANDLE file = INVALID_HANDLE_VALUE, parent = INVALID_HANDLE_VALUE;
    PSECURITY_DESCRIPTOR file_descriptor = NULL, parent_descriptor = NULL;
    PACL file_original = NULL, parent_original = NULL, file_denied = NULL, parent_denied = NULL;
    BOOL restored_thread = TRUE;
    /* Only this disposable root is changed. Keep both already-authorized
     * restoration handles before installing either noninheriting deny ACE. */
    file = CreateFileW(path, READ_CONTROL | WRITE_DAC, sharing, NULL, OPEN_EXISTING, 0, NULL);
    parent = CreateFileW(root, READ_CONTROL | WRITE_DAC, sharing, NULL, OPEN_EXISTING,
        FILE_FLAG_BACKUP_SEMANTICS, NULL);
    BOOL saved_file = file != INVALID_HANDLE_VALUE && GetSecurityInfo(file, SE_FILE_OBJECT,
        DACL_SECURITY_INFORMATION, NULL, NULL, &file_original, NULL, &file_descriptor) == ERROR_SUCCESS && file_original;
    BOOL saved_parent = parent != INVALID_HANDLE_VALUE && GetSecurityInfo(parent, SE_FILE_OBJECT,
        DACL_SECURITY_INFORMATION, NULL, NULL, &parent_original, NULL, &parent_descriptor) == ERROR_SUCCESS && parent_original;
    CHECK(saved_file && saved_parent, "retain both fixture ACLs and authorized restoration handles");
    if (!saved_file || !saved_parent) goto done;
    file_denied = acl_with_denial(file_original, sid, FILE_READ_ATTRIBUTES);
    parent_denied = acl_with_denial(parent_original, sid, FILE_LIST_DIRECTORY);
    CHECK(file_denied && parent_denied, "construct both noninheriting own-SID fixture denials");
    if (!file_denied || !parent_denied) goto done;
    DWORD file_code = SetSecurityInfo(file, SE_FILE_OBJECT, DACL_SECURITY_INFORMATION,
        NULL, NULL, file_denied, NULL);
    DWORD parent_code = SetSecurityInfo(parent, SE_FILE_OBJECT, DACL_SECURITY_INFORMATION,
        NULL, NULL, parent_denied, NULL);
    CHECK(file_code == ERROR_SUCCESS && parent_code == ERROR_SUCCESS, "apply child-attributes and parent-list fixture denials");
    BOOL child_valid = verify_denial(file, sid, FILE_READ_ATTRIBUTES, "actual child own-SID READ_ATTRIBUTES denial");
    BOOL parent_valid = verify_denial(parent, sid, FILE_LIST_DIRECTORY, "actual parent own-SID noninherit LIST_DIRECTORY denial");
    /* MS-FSA 2.1.5.1.2.1 also grants child attributes through parent listing.
     * The identical native open must calibrate denial before testing failure. */
    if (file_code == ERROR_SUCCESS && parent_code == ERROR_SUCCESS && child_valid && parent_valid)
        restored_thread = test_restricted_denial(path, token);
done:
    /* Restore the parent first so any inherited-ACE propagation completes
     * before the child's final original-ACL restoration and verification. */
    if (parent != INVALID_HANDLE_VALUE && parent_original)
        restore_and_verify_acl(parent, parent_original, "restore and verify parent original ACL");
    if (file != INVALID_HANDLE_VALUE && file_original)
        restore_and_verify_acl(file, file_original, "restore and verify child original ACL");
    if (file != INVALID_HANDLE_VALUE) CloseHandle(file);
    if (parent != INVALID_HANDLE_VALUE) CloseHandle(parent);
    if (file_descriptor) LocalFree(file_descriptor);
    if (parent_descriptor) LocalFree(parent_descriptor);
    free(file_denied); free(parent_denied);
    if (!restored_thread) ExitProcess(1);
}

static void test_acl(const WCHAR *path, const WCHAR *root)
{
    HANDLE token = NULL, file = INVALID_HANDLE_VALUE;
    DWORD required = 0;
    TOKEN_USER *user = NULL;
    PSECURITY_DESCRIPTOR descriptor = NULL, changed_descriptor = NULL;
    PACL old_acl = NULL, actual_acl = NULL, changed_acl = NULL;
    char *before = stamp(path), *after = NULL;
    if (!OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY | TOKEN_DUPLICATE, &token)) { CHECK(0, "open own token"); goto done; }
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
    file = CreateFileW(path, READ_CONTROL | WRITE_DAC, sharing, NULL, OPEN_EXISTING, 0, NULL);
    DWORD open_error = file == INVALID_HANDLE_VALUE ? GetLastError() : ERROR_SUCCESS;
    DWORD restore_result = file == INVALID_HANDLE_VALUE ? open_error :
        SetSecurityInfo(file, SE_FILE_OBJECT, DACL_SECURITY_INFORMATION, NULL, NULL, old_acl, NULL);
    printf("DIAG DACL restore open_error=%lu result=%lu\n", (unsigned long)open_error, (unsigned long)restore_result);
    CHECK(file != INVALID_HANDLE_VALUE && restore_result == ERROR_SUCCESS, "restore only fixture original DACL");
    if (file != INVALID_HANDLE_VALUE && restore_result == ERROR_SUCCESS)
        test_parent_child_denial(path, root, token, user->User.Sid);
done:
    if (file != INVALID_HANDLE_VALUE && old_acl)
        restore_and_verify_acl(file, old_acl, "verify original DACL-only fixture restoration");
    if (file != INVALID_HANDLE_VALUE) CloseHandle(file);
    if (token) CloseHandle(token);
    if (descriptor) LocalFree(descriptor);
    if (changed_descriptor) LocalFree(changed_descriptor);
    free(user); free(changed_acl); g_free(before); g_free(after);
}

static void test_identity(const WCHAR *path)
{
    FILE_BASIC_INFO original = {0};
    WCHAR retired[2048];
    swprintf(retired, 2048, L"%ls.retired-%lu-%llu", path, (unsigned long)GetCurrentProcessId(),
             (unsigned long long)GetTickCount64());
    HANDLE old = CreateFileW(path, FILE_READ_ATTRIBUTES, sharing, NULL, OPEN_EXISTING, 0, NULL);
    BOOL baseline_read = old != INVALID_HANDLE_VALUE && GetFileInformationByHandleEx(old, FileBasicInfo, &original, sizeof(original));
    CHECK(baseline_read, "read replacement control timestamps");
    if (!baseline_read) { if (old != INVALID_HANDLE_VALUE) CloseHandle(old); return; }
    char *before = stamp(path);
    BOOL moved = MoveFileW(path, retired);
    CHECK(moved, "retire pinned original to a unique sibling without overwrite");
    if (!moved) { CloseHandle(old); g_free(before); return; }
    HANDLE replacement = CreateFileW(path, GENERIC_WRITE, sharing, NULL,
                                      CREATE_NEW, FILE_ATTRIBUTE_NORMAL, NULL);
    DWORD written = 0;
    CHECK(replacement != INVALID_HANDLE_VALUE && WriteFile(replacement, fixture_bytes, sizeof(fixture_bytes), &written, NULL)
          && written == sizeof(fixture_bytes), "same-byte replacement created");
    if (replacement != INVALID_HANDLE_VALUE) CloseHandle(replacement);
    replacement = CreateFileW(path, FILE_WRITE_ATTRIBUTES, sharing, NULL, OPEN_EXISTING, 0, NULL);
    CHECK(replacement != INVALID_HANDLE_VALUE && SetFileInformationByHandle(replacement, FileBasicInfo, &original, sizeof(original)),
          "equalize all replacement timestamps and attributes");
    if (replacement != INVALID_HANDLE_VALUE) CloseHandle(replacement);
    char *after = stamp(path);
    printf("DIAG identity before=%s after=%s\n", before ? before : "<none>", after ? after : "<none>");
    CHECK(equal_except(before, after, "|id="), "identity control calibrated: all fields except file ID equal");
    CHECK(before && after && strcmp(before, after), "same-byte replacement changes file identity stamp");
    CHECK(same_bytes(path), "replacement control preserves exact bytes");
    CloseHandle(old);
    CHECK(DeleteFileW(retired), "retired original cleanup after pinned handle closes");
    g_free(before); g_free(after);
}

static BOOL warmup_handle_observer(void);

static void test_faults(const WCHAR *path)
{
    char *name = utf8(path);
    DWORD before = 0, after = 0;
    if (!warmup_handle_observer()) { free(name); return; }
    CHECK(GetProcessHandleCount(GetCurrentProcess(), &before), "read baseline process handle count");
    printf("DIAG handles before=%lu adapter_active=%d opens=%u closes=%u close_failures=%d\n",
           (unsigned long)before, active_handles, observed_opens, observed_closes, close_failures);
    FILE_INFO_BY_HANDLE_CLASS queries[] = {FileIdInfo, FileBasicInfo, FileStandardInfo, FileAttributeTagInfo};
    for (unsigned round = 0; round < 8; ++round) {
        for (unsigned i = 0; i < sizeof(queries) / sizeof(queries[0]); ++i) {
            injected_query = queries[i]; rejected(name, "required metadata query failure is explicit and closes handles");
        }
        injected_query = -1;
        for (injected_provider = 1; injected_provider <= 6; ++injected_provider)
            rejected(name, "unsupported provider/open/volume/delete-pending control rejected");
        injected_provider = 0;
        DWORD current = 0;
        CHECK(GetProcessHandleCount(GetCurrentProcess(), &current), "read fault-round process handle count");
        printf("DIAG handles round=%u process=%lu adapter_active=%d opens=%u closes=%u close_failures=%d\n",
               round, (unsigned long)current, active_handles, observed_opens, observed_closes, close_failures);
    }
    CHECK(GetProcessHandleCount(GetCurrentProcess(), &after), "read final process handle count");
    printf("DIAG handles after=%lu adapter_active=%d opens=%u closes=%u close_failures=%d\n",
           (unsigned long)after, active_handles, observed_opens, observed_closes, close_failures);
    CHECK(active_handles == 0 && observed_opens == observed_closes && !close_failures && before == after,
          "all injected paths close each acquired handle without leaks");
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

typedef struct { uintptr_t value; WCHAR type[64], name[384]; } HandleRecord;
typedef struct { unsigned count; HandleRecord records[4096]; } HandleSnapshot;

static HandleSnapshot *snapshot_own_handles(const char *stage)
{
    HandleSnapshot *result = calloc(1, sizeof(*result));
    HPSS snapshot = NULL; HPSSWALK marker = NULL;
    DWORD before = 0, after = 0, code;
    CHECK(result != NULL, "allocate bounded own-process handle snapshot");
    if (!result) return NULL;
    GetProcessHandleCount(GetCurrentProcess(), &before);
    code = PssCaptureSnapshot(GetCurrentProcess(), (PSS_CAPTURE_FLAGS)(PSS_CAPTURE_HANDLES |
        PSS_CAPTURE_HANDLE_NAME_INFORMATION | PSS_CAPTURE_HANDLE_BASIC_INFORMATION), 0, &snapshot);
    CHECK(code == ERROR_SUCCESS, "capture own-process handles with documented PSS API");
    if (code != ERROR_SUCCESS) { printf("DIAG PSS capture error=%lu\n", (unsigned long)code); free(result); return NULL; }
    code = PssWalkMarkerCreate(NULL, &marker);
    CHECK(code == ERROR_SUCCESS, "create owned PSS walk marker");
    if (code == ERROR_SUCCESS) {
        PSS_HANDLE_ENTRY entry;
        while ((code = PssWalkSnapshot(snapshot, PSS_WALK_HANDLES, marker, &entry, sizeof(entry))) == ERROR_SUCCESS) {
            if (result->count == 4096) { CHECK(0, "own-process handle snapshot exceeded explicit bound"); break; }
            HandleRecord *record = &result->records[result->count++];
            record->value = (uintptr_t)entry.Handle;
            if ((entry.Flags & PSS_HANDLE_HAVE_TYPE) && entry.TypeName) {
                size_t length = entry.TypeNameLength / sizeof(WCHAR);
                if (length > 63) length = 63;
                memcpy(record->type, entry.TypeName, length * sizeof(WCHAR));
            }
            if ((entry.Flags & PSS_HANDLE_HAVE_NAME) && entry.ObjectName) {
                size_t length = entry.ObjectNameLength / sizeof(WCHAR);
                if (length > 383) length = 383;
                memcpy(record->name, entry.ObjectName, length * sizeof(WCHAR));
            }
        }
        CHECK(code == ERROR_NO_MORE_ITEMS, "walk complete bounded own-process snapshot");
        CHECK(PssWalkMarkerFree(marker) == ERROR_SUCCESS, "free own PSS walk marker");
    }
    CHECK(PssFreeSnapshot(GetCurrentProcess(), snapshot) == ERROR_SUCCESS, "free own PSS snapshot");
    GetProcessHandleCount(GetCurrentProcess(), &after);
    printf("DIAG PSS stage=%s captured=%u observer_handles_before=%lu observer_handles_after=%lu\n",
           stage, result->count, (unsigned long)before, (unsigned long)after);
    return result;
}

static BOOL warmup_handle_observer(void)
{
    ULONGLONG started = GetTickCount64(), deadline = started + 5000;
    unsigned prior_failures = failures;
    char *glib_probe = g_strdup("initialize GLib before ownership measurement");
    g_free(glib_probe);
    HandleSnapshot *observer = snapshot_own_handles("premeasurement-observer-initialization");
    BOOL initialized = observer != NULL && failures == prior_failures;
    free(observer);
    if (!initialized) return FALSE;
    /* Actual PSS evidence isolates startup registry-key churn to idle time.
     * Initialize observers once, close them, then require quiescence. Never
     * run adapter work here or reset the baseline after measured operations. */
    Sleep(500);
    DWORD previous = 0, current = 0; unsigned stable = 0;
    while (GetTickCount64() < deadline) {
        BOOL queried = GetProcessHandleCount(GetCurrentProcess(), &current);
        CHECK(queried, "sample process handles before fixed measurement baseline");
        if (!queried) return FALSE;
        stable = stable && current == previous ? stable + 1 : 1;
        printf("DIAG startup-quiescence elapsed_ms=%llu handles=%lu consecutive=%u\n",
               (unsigned long long)(GetTickCount64() - started), (unsigned long)current, stable);
        if (stable >= 3) {
            CHECK(1, "startup handles quiescent within five seconds before measurement");
            return TRUE;
        }
        previous = current;
        Sleep(200);
    }
    CHECK(0, "startup handles quiescent within five seconds before measurement");
    return FALSE;
}

static void compare_handles(const char *stage, const HandleSnapshot *before, const HandleSnapshot *after)
{
    if (!before || !after) return;
    unsigned added = 0;
    for (unsigned i = 0; i < after->count; ++i) {
        const HandleRecord *record = &after->records[i];
        int found = 0;
        for (unsigned j = 0; j < before->count && !found; ++j)
            found = record->value == before->records[j].value && !wcscmp(record->type, before->records[j].type) &&
                    !wcscmp(record->name, before->records[j].name);
        if (!found) {
            if (added < 64) {
                char *type = utf8(record->type), *name = utf8(record->name);
                printf("DIAG PSS added stage=%s handle=%p type=%s name=%s\n", stage, (void *)record->value,
                       type ? type : "<encoding>", name ? name : "<encoding>");
                free(type); free(name);
            }
            ++added;
        }
    }
    printf("DIAG PSS delta stage=%s before=%u after=%u added=%u added_print_limit=64\n",
           stage, before->count, after->count, added);
}

static void diagnose_own_handles(const WCHAR *path)
{
    HANDLE file = CreateFileW(path, FILE_READ_ATTRIBUTES, sharing, NULL, OPEN_EXISTING,
                             FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS, NULL);
    CHECK(file != INVALID_HANDLE_VALUE, "open own isolated handle-probe fixture");
    if (file == INVALID_HANDLE_VALUE) return;
    WCHAR final[2048] = {0}, volume[50] = {0}, fs[32] = {0};
    DWORD length = GetFinalPathNameByHandleW(file, final, 2048, FILE_NAME_OPENED | VOLUME_NAME_GUID);
    CHECK(length >= 49 && length < 2048, "resolve isolated fixture volume");
    if (length >= 49 && length < 2048) memcpy(volume, final, 49 * sizeof(WCHAR));
    char *name = utf8(path);
    const char *stages[] = {"idle-500ms", "glib-only", "raw-queries", "raw-final-path", "raw-drive-type",
                           "raw-volume-info", "successful-stamps", "query-failures", "provider-failures"};
    HandleSnapshot *previous = snapshot_own_handles("initial");
    for (unsigned stage = 0; stage < sizeof(stages) / sizeof(stages[0]); ++stage) {
        if (stage == 0) Sleep(500);
        else for (unsigned repeat = 0; repeat < 32; ++repeat) {
            if (stage == 1) {
                char *text = g_strdup_printf("metadata diagnostic %u", repeat);
                gunichar2 *wide = g_utf8_to_utf16(name, -1, NULL, NULL, NULL);
                g_free(text); g_free(wide);
            } else if (stage == 2) {
                FILE_ID_INFO id; FILE_BASIC_INFO basic; FILE_STANDARD_INFO standard; FILE_ATTRIBUTE_TAG_INFO tag;
                CHECK(GetFileInformationByHandleEx(file, FileIdInfo, &id, sizeof(id)) &&
                    GetFileInformationByHandleEx(file, FileBasicInfo, &basic, sizeof(basic)) &&
                    GetFileInformationByHandleEx(file, FileStandardInfo, &standard, sizeof(standard)) &&
                    GetFileInformationByHandleEx(file, FileAttributeTagInfo, &tag, sizeof(tag)), "raw own-file queries succeed");
            } else if (stage == 3) {
                CHECK(GetFinalPathNameByHandleW(file, final, 2048, FILE_NAME_OPENED | VOLUME_NAME_GUID) > 0,
                      "raw own-file final-path query succeeds");
            } else if (stage == 4) {
                UINT drive = GetDriveTypeW(volume);
                CHECK(drive == DRIVE_FIXED || drive == DRIVE_REMOVABLE, "raw own-volume drive-type query succeeds");
            } else if (stage == 5) {
                CHECK(GetVolumeInformationByHandleW(file, NULL, 0, NULL, NULL, NULL, fs, 32),
                      "raw own-volume filesystem query succeeds");
            } else if (stage == 6) {
                char *error = NULL, *value = sumi_explorer_platform_metadata_stamp(name, &error);
                CHECK(value && !error && !active_handles, "isolated successful stamp closes owned handles");
                g_free(value); g_free(error);
            } else {
                if (stage == 7) {
                    FILE_INFO_BY_HANDLE_CLASS kinds[] = {FileIdInfo, FileBasicInfo, FileStandardInfo, FileAttributeTagInfo};
                    injected_query = kinds[repeat % 4];
                } else injected_provider = 1 + repeat % 6;
                rejected(name, "isolated injected failure closes owned handles");
                injected_query = -1; injected_provider = 0;
            }
        }
        HandleSnapshot *current = snapshot_own_handles(stages[stage]);
        compare_handles(stages[stage], previous, current);
        free(previous); previous = current;
    }
    free(previous); free(name); CloseHandle(file);
    CHECK(!active_handles && observed_opens == observed_closes && !close_failures, "isolated adapter ownership remains balanced");
}

int main(int argc, char **argv)
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
    if (argc == 2 && !strcmp(argv[1], "--handle-probe")) {
        diagnose_own_handles(path);
        CHECK(DeleteFileW(path) && RemoveDirectoryW(root), "isolated handle-probe fixture cleanup");
        return failures ? 1 : 0;
    }
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
    test_acl(path, root); test_identity(path);
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
