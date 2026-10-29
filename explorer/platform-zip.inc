/* Public OS I/O only; archive policy and operation ownership live in callers. */
#include "platform-zip.h"
#include <string.h>

static gboolean sumi_zip_error(GError **error, GIOErrorEnum code, const char *message)
{
    if (!error || !*error) g_set_error_literal(error, G_IO_ERROR, code, message);
    return FALSE;
}

static gboolean sumi_zip_ready(GCancellable *cancel, GError **error)
{
    if (error && *error) return FALSE;
    return !cancel || !g_cancellable_set_error_if_cancelled(cancel, error);
}

#ifdef _WIN32
#include <windows.h>
#include <aclapi.h>
#include <ole2.h>
#include <wchar.h>

struct SumiExplorerZipIO { HANDLE handle; gboolean writable; WCHAR *name; };

static gboolean sumi_zip_winerror(GError **error, const char *operation, DWORD code)
{
    if (!error || !*error) g_set_error(error, G_IO_ERROR, g_io_error_from_win32_error(code),
        "%s failed (Win32 error %lu)", operation, (unsigned long)code);
    return FALSE;
}

static WCHAR *sumi_zip_path(const char *path, GError **error)
{
    gsize length = 0;
    if (path) while (length <= 1024u * 1024u && path[length]) ++length;
    if (!path || !length || length > 1024u * 1024u ||
        !g_utf8_validate(path, (gssize)length, NULL) || !g_path_is_absolute(path)) {
        sumi_zip_error(error, G_IO_ERROR_INVALID_ARGUMENT, "ZIP OS I/O requires a bounded absolute UTF-8 path");
        return NULL;
    }
    GFile *file = g_file_new_for_path(path);
    char *native = g_file_get_path(file);
    g_object_unref(file);
    glong written = 0;
    WCHAR *wide = native ? (WCHAR *)g_utf8_to_utf16(native, -1, NULL, &written, error) : NULL;
    g_free(native);
    if (!wide) {
        sumi_zip_error(error, G_IO_ERROR_INVALID_ARGUMENT, "ZIP OS I/O requires a native filesystem path");
    } else if (written >= 32767) {
        g_free(wide); wide = NULL;
        sumi_zip_error(error, G_IO_ERROR_FILENAME_TOO_LONG, "ZIP OS I/O path exceeds the UTF-16 bound");
    }
    return wide;
}

static gboolean sumi_zip_owner(SumiExplorerZipIO *io, GError **error)
{
    return (io && io->handle != INVALID_HANDLE_VALUE) ||
        sumi_zip_error(error, G_IO_ERROR_INVALID_ARGUMENT, "ZIP OS I/O requires a live owned handle");
}

static GFileInfo *sumi_zip_query_handle(HANDLE handle, GError **error)
{
    FILE_BASIC_INFO basic;
    FILE_STANDARD_INFO standard;
    FILE_ATTRIBUTE_TAG_INFO tag;
    FILE_ID_INFO identity;
    BY_HANDLE_FILE_INFORMATION legacy;
    WCHAR filesystem[64] = {0};
    DWORD volume_serial = 0;
    char *volume = NULL, *provider = NULL, *filesystem_id = NULL, *file_id = NULL, *etag = NULL;
    WCHAR *final = NULL;
    GFileInfo *info = NULL;
    gboolean id128;
    DWORD identity_error;
    if (GetFileType(handle) != FILE_TYPE_DISK) {
        sumi_zip_error(error, G_IO_ERROR_NOT_SUPPORTED, "ZIP OS metadata requires a filesystem disk handle");
        goto done;
    }
    if (!GetVolumeInformationByHandleW(handle, NULL, 0, &volume_serial, NULL, NULL, filesystem, 64)) {
        sumi_zip_winerror(error, "Query ZIP filesystem provider", GetLastError()); goto done;
    }
    if (!GetFileInformationByHandleEx(handle, FileBasicInfo, &basic, sizeof(basic)) ||
        !GetFileInformationByHandleEx(handle, FileStandardInfo, &standard, sizeof(standard)) ||
        !GetFileInformationByHandleEx(handle, FileAttributeTagInfo, &tag, sizeof(tag))) {
        sumi_zip_winerror(error, "Query required ZIP handle metadata", GetLastError()); goto done;
    }
    id128 = GetFileInformationByHandleEx(handle, FileIdInfo, &identity, sizeof(identity));
    identity_error = id128 ? ERROR_SUCCESS : GetLastError();
    if (!id128) {
        gboolean fat = !_wcsicmp(filesystem, L"FAT") || !_wcsicmp(filesystem, L"FAT32") ||
            !_wcsicmp(filesystem, L"exFAT");
        if (!fat || (identity_error != ERROR_INVALID_PARAMETER && identity_error != ERROR_NOT_SUPPORTED &&
                     identity_error != ERROR_CALL_NOT_IMPLEMENTED && identity_error != ERROR_INVALID_FUNCTION)) {
            sumi_zip_winerror(error, "Query full ZIP file identity", identity_error); goto done;
        }
        if (!GetFileInformationByHandle(handle, &legacy)) {
            sumi_zip_winerror(error, "Query supported FAT file identity", GetLastError()); goto done;
        }
        if (legacy.dwVolumeSerialNumber != volume_serial) {
            sumi_zip_error(error, G_IO_ERROR_FAILED, "ZIP FAT identity has inconsistent volume serials"); goto done;
        }
    }
    DWORD needed = GetFinalPathNameByHandleW(handle, NULL, 0, FILE_NAME_OPENED | VOLUME_NAME_GUID);
    if (!needed || needed > 32768) {
        sumi_zip_error(error, G_IO_ERROR_NOT_SUPPORTED, "ZIP metadata requires a bounded volume GUID identity"); goto done;
    }
    final = g_new0(WCHAR, needed + 1);
    DWORD actual = GetFinalPathNameByHandleW(handle, final, needed + 1, FILE_NAME_OPENED | VOLUME_NAME_GUID);
    WCHAR *end = actual && actual <= needed ? wcschr(final, L'}') : NULL;
    if (!end || wcsncmp(final, L"\\\\?\\Volume{", 11)) {
        sumi_zip_error(error, G_IO_ERROR_NOT_SUPPORTED, "ZIP metadata requires a local volume GUID"); goto done;
    }
    end[1] = 0;
    volume = g_utf16_to_utf8((const gunichar2 *)final, -1, NULL, NULL, error);
    if (!volume) goto done;
    provider = g_utf16_to_utf8((const gunichar2 *)filesystem, -1, NULL, NULL, error);
    if (!volume || !provider) goto done;
    filesystem_id = g_strdup_printf("win-volume-v1|provider=%s|kind=%s|volume=%s|serial=%016" G_GINT64_MODIFIER "x",
        provider, id128 ? "id128" : "fat-id64", volume,
        (guint64)(id128 ? identity.VolumeSerialNumber : (guint64)legacy.dwVolumeSerialNumber));
    if (id128) {
        char hex[33];
        for (unsigned i = 0; i < 16; ++i) g_snprintf(hex + i * 2, 3, "%02x", identity.FileId.Identifier[i]);
        file_id = g_strdup_printf("id128:%s", hex);
    } else {
        file_id = g_strdup_printf("fat-id64:%08lx%08lx", (unsigned long)legacy.nFileIndexHigh,
            (unsigned long)legacy.nFileIndexLow);
    }
    if (standard.EndOfFile.QuadPart < 0) {
        sumi_zip_error(error, G_IO_ERROR_NOT_SUPPORTED, "ZIP metadata has an unrepresentable size"); goto done;
    }
    if ((tag.FileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) &&
        tag.ReparseTag != IO_REPARSE_TAG_SYMLINK && tag.ReparseTag != IO_REPARSE_TAG_MOUNT_POINT) {
        sumi_zip_error(error, G_IO_ERROR_NOT_SUPPORTED, "ZIP metadata does not support this reparse provider"); goto done;
    }
    etag = g_strdup_printf("win-etag-v1|%s|%s|created=%016" G_GINT64_MODIFIER "x"
        "|written=%016" G_GINT64_MODIFIER "x|changed=%016" G_GINT64_MODIFIER "x"
        "|attributes=%08lx|size=%016" G_GINT64_MODIFIER "x|tag=%08lx|links=%08lx|directory=%u",
        filesystem_id, file_id, (guint64)basic.CreationTime.QuadPart, (guint64)basic.LastWriteTime.QuadPart,
        (guint64)basic.ChangeTime.QuadPart, (unsigned long)basic.FileAttributes,
        (guint64)standard.EndOfFile.QuadPart, (unsigned long)tag.ReparseTag,
        (unsigned long)standard.NumberOfLinks, !!standard.Directory);
    info = g_file_info_new();
    g_file_info_set_file_type(info, (tag.FileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) ? G_FILE_TYPE_SYMBOLIC_LINK :
        standard.Directory ? G_FILE_TYPE_DIRECTORY : G_FILE_TYPE_REGULAR);
    g_file_info_set_is_symlink(info, !!(tag.FileAttributes & FILE_ATTRIBUTE_REPARSE_POINT));
    g_file_info_set_size(info, standard.EndOfFile.QuadPart);
    g_file_info_set_attribute_string(info, G_FILE_ATTRIBUTE_ID_FILESYSTEM, filesystem_id);
    g_file_info_set_attribute_string(info, G_FILE_ATTRIBUTE_ID_FILE, file_id);
    g_file_info_set_attribute_string(info, G_FILE_ATTRIBUTE_ETAG_VALUE, etag);
    gint64 seconds = basic.LastWriteTime.QuadPart / 10000000 - G_GINT64_CONSTANT(11644473600);
    g_file_info_set_attribute_uint64(info, G_FILE_ATTRIBUTE_TIME_MODIFIED, (guint64)seconds);
    g_file_info_set_attribute_uint32(info, G_FILE_ATTRIBUTE_TIME_MODIFIED_USEC,
        ((guint64)basic.LastWriteTime.QuadPart % 10000000) / 10);
done:
    g_free(final); g_free(volume); g_free(provider); g_free(filesystem_id); g_free(file_id); g_free(etag);
    return info;
}

GFileInfo *sumi_explorer_zip_path_query(const char *path, GFileQueryInfoFlags flags,
                                      GCancellable *cancel, GError **error)
{
    if (!sumi_zip_ready(cancel, error)) return NULL;
    if (flags & ~G_FILE_QUERY_INFO_NOFOLLOW_SYMLINKS) {
        sumi_zip_error(error, G_IO_ERROR_INVALID_ARGUMENT, "Unsupported ZIP path query flags"); return NULL;
    }
    WCHAR *wide = sumi_zip_path(path, error);
    if (!wide) return NULL;
    HANDLE handle = CreateFileW(wide, FILE_READ_ATTRIBUTES, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        NULL, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS |
        ((flags & G_FILE_QUERY_INFO_NOFOLLOW_SYMLINKS) ? FILE_FLAG_OPEN_REPARSE_POINT : 0), NULL);
    DWORD open_error = handle == INVALID_HANDLE_VALUE ? GetLastError() : ERROR_SUCCESS;
    g_free(wide);
    if (handle == INVALID_HANDLE_VALUE) { sumi_zip_winerror(error, "Open ZIP path metadata", open_error); return NULL; }
    GFileInfo *info = sumi_zip_query_handle(handle, error);
    if (!CloseHandle(handle)) {
        sumi_zip_winerror(error, "Close ZIP path metadata", GetLastError());
        if (info) { g_object_unref(info); info = NULL; }
    }
    return info;
}

SumiExplorerZipIO *sumi_explorer_zip_open_source(const char *path, GCancellable *cancel, GError **error)
{
    if (!sumi_zip_ready(cancel, error)) return NULL;
    WCHAR *wide = sumi_zip_path(path, error);
    if (!wide) return NULL;
    HANDLE handle = CreateFileW(wide, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING,
        FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_SEQUENTIAL_SCAN, NULL);
    if (handle == INVALID_HANDLE_VALUE) {
        DWORD code = GetLastError(); g_free(wide); sumi_zip_winerror(error, "Open ZIP source", code); return NULL;
    }
    GFileInfo *info = sumi_zip_query_handle(handle, error);
    if (!info || g_file_info_get_file_type(info) != G_FILE_TYPE_REGULAR) {
        if (info) g_object_unref(info);
        sumi_zip_error(error, G_IO_ERROR_NOT_SUPPORTED, "ZIP source handle is not an ordinary file");
        if (!CloseHandle(handle)) sumi_zip_winerror(error, "Close rejected ZIP source", GetLastError());
        g_free(wide); return NULL;
    }
    g_object_unref(info);
    SumiExplorerZipIO *io = g_new0(SumiExplorerZipIO, 1);
    io->handle = handle; io->name = wide;
    return io;
}

static gboolean sumi_zip_private_security(SECURITY_DESCRIPTOR *descriptor, PACL *acl, GError **error)
{
    HANDLE token = NULL; DWORD needed = 0; TOKEN_USER *user = NULL; gboolean result = FALSE;
    if (!OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &token)) goto failed;
    GetTokenInformation(token, TokenUser, NULL, 0, &needed);
    if (!needed) goto failed;
    user = g_malloc0(needed);
    if (!GetTokenInformation(token, TokenUser, user, needed, &needed)) goto failed;
    DWORD bytes = sizeof(ACL) + sizeof(ACCESS_ALLOWED_ACE) + GetLengthSid(user->User.Sid);
    *acl = g_malloc0(bytes);
    if (!InitializeAcl(*acl, bytes, ACL_REVISION) ||
        !AddAccessAllowedAceEx(*acl, ACL_REVISION, 0, FILE_ALL_ACCESS, user->User.Sid) ||
        !InitializeSecurityDescriptor(descriptor, SECURITY_DESCRIPTOR_REVISION) ||
        !SetSecurityDescriptorDacl(descriptor, TRUE, *acl, FALSE) ||
        !SetSecurityDescriptorControl(descriptor, SE_DACL_PROTECTED, SE_DACL_PROTECTED)) goto failed;
    result = TRUE;
failed:
    if (!result) sumi_zip_winerror(error, "Prepare private ZIP snapshot security", GetLastError());
    if (token) CloseHandle(token);
    g_free(user);
    return result;
}

static gboolean sumi_zip_verify_private(HANDLE handle, PACL expected, GError **error)
{
    DWORD flags = 0;
    if (!GetVolumeInformationByHandleW(handle, NULL, 0, NULL, NULL, &flags, NULL, 0))
        return sumi_zip_winerror(error, "Query ZIP snapshot ACL capability", GetLastError());
    if (!(flags & FILE_PERSISTENT_ACLS))
        return sumi_zip_error(error, G_IO_ERROR_NOT_SUPPORTED, "ZIP snapshot storage must support persistent ACLs");
    PACL actual = NULL; PSECURITY_DESCRIPTOR descriptor = NULL;
    DWORD code = GetSecurityInfo(handle, SE_FILE_OBJECT, DACL_SECURITY_INFORMATION,
        NULL, NULL, &actual, NULL, &descriptor);
    SECURITY_DESCRIPTOR_CONTROL control = 0; DWORD revision = 0;
    void *left = NULL, *right = NULL;
    gboolean private = code == ERROR_SUCCESS && descriptor && actual && actual->AceCount == 1 &&
        GetSecurityDescriptorControl(descriptor, &control, &revision) && (control & SE_DACL_PROTECTED) &&
        GetAce(expected, 0, &left) && GetAce(actual, 0, &right) &&
        ((ACE_HEADER *)left)->AceSize == ((ACE_HEADER *)right)->AceSize &&
        !memcmp(left, right, ((ACE_HEADER *)left)->AceSize);
    if (descriptor) LocalFree(descriptor);
    if (!private) return sumi_zip_error(error, G_IO_ERROR_NOT_SUPPORTED,
        "ZIP snapshot user-only protected ACL could not be verified");
    GFileInfo *info = sumi_zip_query_handle(handle, error);
    if (!info) return FALSE;
    gboolean regular = g_file_info_get_file_type(info) == G_FILE_TYPE_REGULAR;
    g_object_unref(info);
    return regular || sumi_zip_error(error, G_IO_ERROR_NOT_SUPPORTED, "ZIP snapshot is not an ordinary file");
}

SumiExplorerZipIO *sumi_explorer_zip_new_snapshot(GCancellable *cancel, GError **error)
{
    if (!sumi_zip_ready(cancel, error)) return NULL;
    SECURITY_DESCRIPTOR descriptor; PACL acl = NULL;
    if (!sumi_zip_private_security(&descriptor, &acl, error)) { g_free(acl); return NULL; }
    SECURITY_ATTRIBUTES security = {sizeof(security), &descriptor, FALSE};
    WCHAR *temp = g_new0(WCHAR, 32768), *name = g_new0(WCHAR, 32768);
    DWORD length = GetTempPathW(32768, temp);
    SumiExplorerZipIO *io = NULL;
    if (!length || length > 32680) {
        sumi_zip_error(error, G_IO_ERROR_FILENAME_TOO_LONG, "ZIP snapshot temporary directory exceeds bound"); goto done;
    }
    for (unsigned attempt = 0; attempt < 16; ++attempt) {
        GUID guid; WCHAR text[40];
        if (!sumi_zip_ready(cancel, error)) break;
        if (FAILED(CoCreateGuid(&guid)) || !StringFromGUID2(&guid, text, 40)) {
            sumi_zip_error(error, G_IO_ERROR_FAILED, "Cannot generate unique ZIP snapshot identity"); break;
        }
        swprintf(name, 32768, L"%lsSumiZip-%ls.tmp", temp, text);
        HANDLE handle = CreateFileW(name, GENERIC_READ | GENERIC_WRITE | DELETE,
            FILE_SHARE_READ | FILE_SHARE_DELETE, &security, CREATE_NEW,
            FILE_ATTRIBUTE_TEMPORARY | FILE_FLAG_DELETE_ON_CLOSE, NULL);
        if (handle != INVALID_HANDLE_VALUE) {
            if (!sumi_zip_verify_private(handle, acl, error)) {
                if (!CloseHandle(handle)) sumi_zip_winerror(error, "Close rejected private ZIP snapshot", GetLastError());
                break;
            }
            io = g_new0(SumiExplorerZipIO, 1); io->handle = handle; io->writable = TRUE;
            io->name = name; name = NULL; break;
        }
        DWORD code = GetLastError();
        if (code != ERROR_FILE_EXISTS && code != ERROR_ALREADY_EXISTS) {
            sumi_zip_winerror(error, "Create private ZIP snapshot", code); break;
        }
    }
    if (!io) sumi_zip_error(error, G_IO_ERROR_FAILED, "Cannot create a new private ZIP snapshot");
done:
    g_free(acl); g_free(temp); g_free(name);
    return io;
}

gssize sumi_explorer_zip_io_read(SumiExplorerZipIO *io, void *buffer, gsize capacity,
                               GCancellable *cancel, GError **error)
{
    if (!sumi_zip_ready(cancel, error) || !sumi_zip_owner(io, error)) return -1;
    if (!capacity) return 0;
    if (!buffer) { sumi_zip_error(error, G_IO_ERROR_INVALID_ARGUMENT, "ZIP read needs a buffer"); return -1; }
    DWORD count = 0, chunk = (DWORD)MIN(capacity, (gsize)65536);
    if (!ReadFile(io->handle, buffer, chunk, &count, NULL)) {
        sumi_zip_winerror(error, "Read ZIP source handle", GetLastError()); return -1;
    }
    return (gssize)count;
}

gboolean sumi_explorer_zip_io_write_all(SumiExplorerZipIO *io, const void *buffer, gsize length,
                                      GCancellable *cancel, GError **error)
{
    if (!sumi_zip_ready(cancel, error) || !sumi_zip_owner(io, error)) return FALSE;
    if (!io->writable || (!buffer && length))
        return sumi_zip_error(error, G_IO_ERROR_INVALID_ARGUMENT, "ZIP write requires a writable owned snapshot and buffer");
    const unsigned char *bytes = buffer;
    while (length) {
        if (!sumi_zip_ready(cancel, error)) return FALSE;
        DWORD count = 0, chunk = (DWORD)MIN(length, (gsize)65536);
        if (!WriteFile(io->handle, bytes, chunk, &count, NULL))
            return sumi_zip_winerror(error, "Write ZIP snapshot handle", GetLastError());
        if (!count) return sumi_zip_error(error, G_IO_ERROR_FAILED, "ZIP snapshot write made no progress");
        bytes += count; length -= count;
    }
    return TRUE;
}

gint64 sumi_explorer_zip_io_seek(SumiExplorerZipIO *io, gint64 offset, GSeekType whence,
                               GCancellable *cancel, GError **error)
{
    if (!sumi_zip_ready(cancel, error) || !sumi_zip_owner(io, error)) return -1;
    DWORD method;
    switch (whence) {
    case G_SEEK_SET: method = FILE_BEGIN; break;
    case G_SEEK_CUR: method = FILE_CURRENT; break;
    case G_SEEK_END: method = FILE_END; break;
    default: sumi_zip_error(error, G_IO_ERROR_INVALID_ARGUMENT, "Invalid ZIP seek origin"); return -1;
    }
    LARGE_INTEGER distance, position; distance.QuadPart = offset;
    if (!SetFilePointerEx(io->handle, distance, &position, method)) {
        sumi_zip_winerror(error, "Seek ZIP owned handle", GetLastError()); return -1;
    }
    return position.QuadPart;
}

GFileInfo *sumi_explorer_zip_io_query(SumiExplorerZipIO *io, GCancellable *cancel, GError **error)
{
    if (!sumi_zip_ready(cancel, error) || !sumi_zip_owner(io, error)) return NULL;
    return sumi_zip_query_handle(io->handle, error);
}

gboolean sumi_explorer_zip_io_close(SumiExplorerZipIO *io, GError **error)
{
    if (!sumi_zip_owner(io, error)) return FALSE;
    gboolean result = CloseHandle(io->handle);
    if (!result) sumi_zip_winerror(error, "Close ZIP owned handle", GetLastError());
    io->handle = INVALID_HANDLE_VALUE;
    g_free(io->name); g_free(io);
    return result;
}

#else

static void sumi_zip_unsupported(GError **error)
{
    (void)sumi_zip_ready;
    sumi_zip_error(error, G_IO_ERROR_NOT_SUPPORTED, "Native ZIP OS I/O is unsupported on this platform");
}
GFileInfo *sumi_explorer_zip_path_query(const char *path, GFileQueryInfoFlags flags, GCancellable *cancel, GError **error)
{ (void)path; (void)flags; (void)cancel; sumi_zip_unsupported(error); return NULL; }
SumiExplorerZipIO *sumi_explorer_zip_open_source(const char *path, GCancellable *cancel, GError **error)
{ (void)path; (void)cancel; sumi_zip_unsupported(error); return NULL; }
SumiExplorerZipIO *sumi_explorer_zip_new_snapshot(GCancellable *cancel, GError **error)
{ (void)cancel; sumi_zip_unsupported(error); return NULL; }
gssize sumi_explorer_zip_io_read(SumiExplorerZipIO *io, void *buffer, gsize capacity, GCancellable *cancel, GError **error)
{ (void)io; (void)buffer; (void)capacity; (void)cancel; sumi_zip_unsupported(error); return -1; }
gboolean sumi_explorer_zip_io_write_all(SumiExplorerZipIO *io, const void *buffer, gsize length, GCancellable *cancel, GError **error)
{ (void)io; (void)buffer; (void)length; (void)cancel; sumi_zip_unsupported(error); return FALSE; }
gint64 sumi_explorer_zip_io_seek(SumiExplorerZipIO *io, gint64 offset, GSeekType whence, GCancellable *cancel, GError **error)
{ (void)io; (void)offset; (void)whence; (void)cancel; sumi_zip_unsupported(error); return -1; }
GFileInfo *sumi_explorer_zip_io_query(SumiExplorerZipIO *io, GCancellable *cancel, GError **error)
{ (void)io; (void)cancel; sumi_zip_unsupported(error); return NULL; }
gboolean sumi_explorer_zip_io_close(SumiExplorerZipIO *io, GError **error)
{ (void)io; sumi_zip_unsupported(error); return FALSE; }

#endif
