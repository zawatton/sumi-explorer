/* OS ABI bridge only: no application file operations or ACL mutation policy. */
#include "platform.h"
#include <string.h>
#include <stdint.h>
#ifdef _WIN32
/* Narrow GLib/GDK imports use their public C ABI, independent of a host's
 * generated glibconfig.h.  Win32/COM types below always come from the SDK. */
typedef struct _GError GError;
typedef struct _GTypeInstance GTypeInstance;
typedef uint16_t gunichar2;
typedef intptr_t gssize;
extern char *g_strdup(const char *);
extern char *g_strdup_printf(const char *, ...);
extern void g_free(void *);
extern int g_utf8_validate(const char *, gssize, const char **);
extern gunichar2 *g_utf8_to_utf16(const char *, long, long *, long *, GError **);
extern uintptr_t gdk_win32_surface_get_type(void);
extern int g_type_check_instance_is_a(GTypeInstance *, uintptr_t);
#else
#include <glib.h>
#endif

#define SUMI_MAX_PATHS 256u
#define SUMI_MAX_BYTES (1024u * 1024u)

static int fail(char **error, const char *message)
{
    if (error) *error = g_strdup(message);
    return -1;
}

static int absolute_path(const char *path)
{
#ifdef _WIN32
    /* A rooted drive path or a complete UNC server/share is required. */
    if (((path[0] >= 'A' && path[0] <= 'Z') ||
         (path[0] >= 'a' && path[0] <= 'z')) && path[1] == ':' &&
        (path[2] == '/' || path[2] == '\\')) return 1;
    if ((path[0] == '/' && path[1] == '/') ||
        (path[0] == '\\' && path[1] == '\\')) {
        const char *server = path + 2, *share;
        if (*server == '?' || *server == '.' || !*server) return 0;
        share = strpbrk(server, "/\\");
        if (!share || share == server || !share[1]) return 0;
        ++share;
        return *share != '/' && *share != '\\';
    }
    return 0;
#else
    return g_path_is_absolute(path);
#endif
}

static int validate(const char *const *paths, unsigned count, char **error)
{
    size_t total = 0;
    unsigned i, j;
    if (error) *error = NULL;
    if (!paths || !count || count > SUMI_MAX_PATHS)
        return fail(error, "Native action requires 1..256 frozen paths");
    for (i = 0; i < count; ++i) {
        size_t length = 0;
        if (!paths[i]) return fail(error, "Native action received a null path");
        while (length <= SUMI_MAX_BYTES && paths[i][length]) ++length;
        if (length > SUMI_MAX_BYTES || total > SUMI_MAX_BYTES - length ||
            total + length == SUMI_MAX_BYTES)
            return fail(error, "Native action exceeds 1 MiB of UTF-8 paths");
        total += length + 1;
        if (!length || !g_utf8_validate(paths[i], (gssize)length, NULL))
            return fail(error, "Native action requires scalar UTF-8 paths");
        if (!absolute_path(paths[i]))
            return fail(error, "Native action requires absolute filesystem paths");
        for (j = 0; j < i; ++j)
            if (!strcmp(paths[i], paths[j]))
                return fail(error, "Native action received duplicate paths");
    }
    return 1;
}

static char *metadata_failure(char **error, const char *message)
{
    if (error && !*error) *error = g_strdup(message);
    return NULL;
}

static int metadata_validate(const char *path, char **error)
{
    char *diagnostic = NULL;
    if (error && *error) return 0;
    if (validate(&path, 1, &diagnostic) < 0) {
        if (error) *error = diagnostic;
        else g_free(diagnostic);
        return 0;
    }
    return 1;
}

#ifdef _WIN32
#define COBJMACROS
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <shlobj.h>
#include <shellapi.h>
#include <ole2.h>
#include <wchar.h>
extern HWND gdk_win32_surface_get_handle(void *);

typedef struct {
    IContextMenu2 *menu2;
    IContextMenu3 *menu3;
    int menu_entered;
    int menu_exited;
} MenuBridge;

static int hresult_error(char **error, const char *operation, HRESULT result)
{
    if (error) *error = g_strdup_printf("%s failed (HRESULT 0x%08lx)",
                                        operation, (unsigned long)(ULONG)result);
    return -1;
}

static int win32_error(char **error, const char *operation, DWORD code)
{
    if (error) *error = g_strdup_printf("%s failed (Win32 error %lu)",
                                        operation, (unsigned long)code);
    return -1;
}

static HWND surface_handle(void *surface, char **error)
{
    HWND handle;
    if (!surface) return NULL; /* Standalone caller: use our owned menu window. */
    if (!g_type_check_instance_is_a((GTypeInstance *)surface, gdk_win32_surface_get_type())) {
        fail(error, "Native action requires a live GTK Win32 surface");
        return NULL;
    }
    handle = gdk_win32_surface_get_handle(surface);
    if (!handle || !IsWindow(handle)) {
        fail(error, "Native action requires a live native window");
        return NULL;
    }
    return handle;
}

static LRESULT CALLBACK menu_window_proc(HWND window, UINT message,
                                         WPARAM wparam, LPARAM lparam)
{
    MenuBridge *bridge = (MenuBridge *)GetWindowLongPtrW(window, GWLP_USERDATA);
    if (message == WM_NCCREATE) {
        CREATESTRUCTW *create = (CREATESTRUCTW *)lparam;
        SetWindowLongPtrW(window, GWLP_USERDATA, (LONG_PTR)create->lpCreateParams);
    }
    if (bridge && message == WM_ENTERMENULOOP) bridge->menu_entered = 1;
    if (bridge && message == WM_EXITMENULOOP) bridge->menu_exited = 1;
    if (bridge && (message == WM_INITMENUPOPUP || message == WM_DRAWITEM ||
                   message == WM_MEASUREITEM || message == WM_MENUCHAR)) {
        LRESULT result = 0;
        if (bridge->menu3 && IContextMenu3_HandleMenuMsg2(
                bridge->menu3, message, wparam, lparam, &result) == S_OK) return result;
        if (message != WM_MENUCHAR && bridge->menu2 &&
            IContextMenu2_HandleMenuMsg(bridge->menu2, message, wparam, lparam) == S_OK)
            return (message == WM_DRAWITEM || message == WM_MEASUREITEM) ? TRUE : 0;
    }
    if (message == WM_NCDESTROY) SetWindowLongPtrW(window, GWLP_USERDATA, 0);
    return DefWindowProcW(window, message, wparam, lparam);
}

static HWND menu_owner(HWND parent, MenuBridge *bridge, char **error)
{
    static const WCHAR name[] = L"SumiExplorerPlatformMenuOwner";
    WNDCLASSW definition = {0}, existing = {0};
    HINSTANCE instance = GetModuleHandleW(NULL);
    HWND window;
    definition.lpfnWndProc = menu_window_proc;
    definition.hInstance = instance;
    definition.lpszClassName = name;
    if (!RegisterClassW(&definition)) {
        DWORD code = GetLastError();
        if (code != ERROR_CLASS_ALREADY_EXISTS ||
            !GetClassInfoW(instance, name, &existing) ||
            existing.lpfnWndProc != menu_window_proc) {
            win32_error(error, "Register native menu owner", code);
            return NULL;
        }
    }
    /* This owned hidden window is ours; never replace GTK's window procedure. */
    window = CreateWindowExW(0, name, L"", WS_POPUP, 0, 0, 0, 0,
                             parent, NULL, instance, bridge);
    if (!window) win32_error(error, "Create native menu owner", GetLastError());
    return window;
}

int sumi_explorer_platform_is_windows(void) { return 1; }

static void preserve_shell_clipboard(DWORD before, char **error)
{
    DWORD sequence = GetClipboardSequenceNumber(), process = 0;
    HWND owner;
    DWORD thread;
    HRESULT result;
    if (!sequence || sequence == before) return;
    owner = GetClipboardOwner();
    if (!owner) return;
    thread = GetWindowThreadProcessId(owner, &process);
    if (process != GetCurrentProcessId() || thread != GetCurrentThreadId()) return;
    /* Arbitrary handlers do not expose their original OleSetClipboard object.
     * OleIsCurrentClipboard cannot validate an OleGetClipboard consumer proxy.
     * Recheck the newly published same-thread ownership immediately before
     * materializing it for the balanced OLE shutdown. */
    if (GetClipboardOwner() != owner || GetClipboardSequenceNumber() != sequence) return;
    result = OleFlushClipboard();
    if (FAILED(result) && error)
        *error = g_strdup_printf("Shell command was invoked, but preserving its clipboard data failed "
                                 "(HRESULT 0x%08lx); do not retry the command",
                                 (unsigned long)(ULONG)result);
}

int sumi_explorer_platform_shell(void *surface, const char *const *paths,
                                unsigned count, char **error)
{
    PIDLIST_ABSOLUTE objects[SUMI_MAX_PATHS] = {0};
    PCUITEMID_CHILD children[SUMI_MAX_PATHS] = {0};
    PIDLIST_ABSOLUTE common = NULL;
    IShellFolder *folder = NULL;
    IContextMenu *context = NULL;
    MenuBridge bridge = {0};
    HMENU menu = NULL;
    HWND parent, owner = NULL;
    HRESULT result;
    POINT point;
    unsigned i, command_count = 0;
    int outcome = -1;
    if (validate(paths, count, error) < 0) return -1;
    if (!surface) return fail(error, "Windows Shell menu requires a live persistent GTK window");
    parent = surface_handle(surface, error);
    if (!parent) return -1;
    result = OleInitialize(NULL);
    if (FAILED(result)) return hresult_error(error, "Initialize Shell OLE apartment", result);
    for (i = 0; i < count; ++i) {
        gunichar2 *wide = g_utf8_to_utf16(paths[i], -1, NULL, NULL, NULL);
        SFGAOF attributes = 0;
        PIDLIST_ABSOLUTE candidate;
        if (!wide) { fail(error, "Cannot encode native path"); goto cleanup; }
        result = SHParseDisplayName((LPCWSTR)wide, NULL, &objects[i], SFGAO_FILESYSTEM, &attributes);
        g_free(wide);
        if (FAILED(result)) { hresult_error(error, "Resolve filesystem Shell object", result); goto cleanup; }
        if (!(attributes & SFGAO_FILESYSTEM)) { fail(error, "Native menu requires filesystem Shell objects"); goto cleanup; }
        candidate = ILCloneFull(objects[i]);
        if (!candidate || !ILRemoveLastID(candidate)) {
            CoTaskMemFree(candidate);
            fail(error, "Native menu requires objects with a filesystem parent");
            goto cleanup;
        }
        if (!common) common = candidate;
        else {
            BOOL same = ILIsEqual(common, candidate);
            CoTaskMemFree(candidate);
            if (!same) { fail(error, "Native menu does not support mixed parent folders"); goto cleanup; }
        }
        children[i] = ILFindLastID(objects[i]);
    }
    result = SHBindToParent(objects[0], &IID_IShellFolder, (void **)&folder, &children[0]);
    if (FAILED(result)) { hresult_error(error, "Bind filesystem parent", result); goto cleanup; }
    result = IShellFolder_GetUIObjectOf(folder, parent, count, children,
                                       &IID_IContextMenu, NULL, (void **)&context);
    if (FAILED(result)) { hresult_error(error, "Acquire native Shell menu", result); goto cleanup; }
    IContextMenu_QueryInterface(context, &IID_IContextMenu3, (void **)&bridge.menu3);
    IContextMenu_QueryInterface(context, &IID_IContextMenu2, (void **)&bridge.menu2);
    owner = menu_owner(parent, &bridge, error);
    if (!owner) goto cleanup;
    menu = CreatePopupMenu();
    if (!menu) { win32_error(error, "Create native popup menu", GetLastError()); goto cleanup; }
    result = IContextMenu_QueryContextMenu(context, menu, 0, 1, 0x7fff, CMF_NORMAL | CMF_EXPLORE);
    if (FAILED(result)) { hresult_error(error, "Populate native Shell menu", result); goto cleanup; }
    command_count = HRESULT_CODE(result);
    if (!command_count) { fail(error, "No native Shell commands are available"); goto cleanup; }
    if (!GetCursorPos(&point)) { win32_error(error, "Read menu position", GetLastError()); goto cleanup; }
    SetForegroundWindow(parent);
    SetLastError(ERROR_SUCCESS);
    {
        UINT selected = (UINT)TrackPopupMenuEx(menu, TPM_RETURNCMD | TPM_RIGHTBUTTON,
                                              point.x, point.y, owner, NULL);
        if (!selected) {
            DWORD code = GetLastError();
            /* TPM_RETURNCMD uses zero for cancellation as well as failure.
             * Shell handlers can leave LastError set inside a successful loop. */
            if (bridge.menu_entered && bridge.menu_exited) outcome = 0;
            else win32_error(error, "Track native Shell menu", code ? code : ERROR_GEN_FAILURE);
        } else if (selected > 0x7fff || selected - 1 >= command_count)
            fail(error, "Shell returned an out-of-range command");
        else {
            CMINVOKECOMMANDINFOEX invoke = {0};
            UINT offset = selected - 1;
            DWORD clipboard_before;
            invoke.cbSize = sizeof(invoke);
            invoke.fMask = CMIC_MASK_UNICODE | CMIC_MASK_PTINVOKE;
            /* A Shell verb can retain this owner for modeless UI after return.
             * The menu-only window is destroyed below, so it must not own UI. */
            invoke.hwnd = parent;
            invoke.lpVerb = MAKEINTRESOURCEA(offset);
            invoke.lpVerbW = MAKEINTRESOURCEW(offset);
            invoke.nShow = SW_SHOWNORMAL;
            invoke.ptInvoke = point;
            clipboard_before = GetClipboardSequenceNumber();
            result = IContextMenu_InvokeCommand(context, (LPCMINVOKECOMMANDINFO)&invoke);
            if (FAILED(result)) hresult_error(error, "Invoke native Shell command", result);
            else {
                outcome = 1;
                preserve_shell_clipboard(clipboard_before, error);
            }
        }
    }
cleanup:
    if (menu) DestroyMenu(menu);
    if (owner) DestroyWindow(owner);
    if (bridge.menu3) IContextMenu3_Release(bridge.menu3);
    if (bridge.menu2) IContextMenu2_Release(bridge.menu2);
    if (context) IContextMenu_Release(context);
    if (folder) IShellFolder_Release(folder);
    CoTaskMemFree(common);
    for (i = 0; i < count; ++i) CoTaskMemFree(objects[i]);
    OleUninitialize();
    return outcome;
}

int sumi_explorer_platform_permissions(void *surface, const char *path, char **error)
{
    HWND parent;
    HRESULT result;
    gunichar2 *wide;
    int outcome;
    if (validate(&path, 1, error) < 0) return -1;
    parent = surface_handle(surface, error);
    if (surface && !parent) return -1;
    result = CoInitializeEx(NULL, COINIT_APARTMENTTHREADED | COINIT_DISABLE_OLE1DDE);
    if (FAILED(result)) return hresult_error(error, "Initialize Security editor COM apartment", result);
    wide = g_utf8_to_utf16(path, -1, NULL, NULL, NULL);
    SetLastError(ERROR_SUCCESS);
    outcome = SHObjectProperties(parent, SHOP_FILEPATH, (LPCWSTR)wide, L"Security")
        ? 1 : win32_error(error, "Invoke Windows Security property page", GetLastError());
    g_free(wide);
    CoUninitialize();
    return outcome;
}

static char *metadata_win32_failure(char **error, const char *operation, DWORD code)
{
    if (error && !*error)
        *error = g_strdup_printf("%s failed (Win32 error %lu)", operation, (unsigned long)code);
    return NULL;
}

static int metadata_component(const WCHAR *start, size_t length)
{
    size_t base = 0;
    if (!length || start[length - 1] == '.' || start[length - 1] == ' ') return 0;
    while (base < length && start[base] != '.') ++base;
    if ((base == 3 && (!_wcsnicmp(start, L"CON", 3) || !_wcsnicmp(start, L"PRN", 3) ||
                      !_wcsnicmp(start, L"AUX", 3) || !_wcsnicmp(start, L"NUL", 3))) ||
        (base == 6 && !_wcsnicmp(start, L"CLOCK$", 6)) ||
        (base == 6 && !_wcsnicmp(start, L"CONIN$", 6)) ||
        (base == 7 && !_wcsnicmp(start, L"CONOUT$", 7))) return 0;
    if (base == 4 && (!_wcsnicmp(start, L"COM", 3) || !_wcsnicmp(start, L"LPT", 3)) &&
        ((start[3] >= '1' && start[3] <= '9') || start[3] == 0x00b9 ||
          start[3] == 0x00b2 || start[3] == 0x00b3)) return 0;
    for (size_t i = 0; i < length; ++i)
        if (start[i] < 32 || wcschr(L":*?\"<>|", start[i])) return 0;
    return 1;
}

char *sumi_explorer_platform_metadata_stamp(const char *path, char **error)
{
    enum { CAPACITY = 32768 };
    gunichar2 *wide = NULL;
    WCHAR *normalized = NULL, *extended = NULL, *final = NULL;
    WCHAR volume[50] = {0}, filesystem[32] = {0};
    HANDLE file = INVALID_HANDLE_VALUE;
    FILE_ID_INFO identity = {0};
    FILE_BASIC_INFO basic = {0};
    FILE_STANDARD_INFO standard = {0};
    FILE_ATTRIBUTE_TAG_INFO attributes = {0};
    char guid[37] = {0}, identifier[33] = {0}, *stamp = NULL;
    DWORD length, drive;
    const DWORD flags = FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS;
    const DWORD sharing = FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE;
    if (!metadata_validate(path, error)) return NULL;
    if (!(((path[0] >= 'A' && path[0] <= 'Z') || (path[0] >= 'a' && path[0] <= 'z')) &&
          path[1] == ':' && (path[2] == '/' || path[2] == '\\')) || strchr(path + 2, ':'))
        return metadata_failure(error, "Metadata requires a local rooted drive path without streams or devices");
    wide = g_utf8_to_utf16(path, -1, NULL, NULL, NULL);
    if (!wide || wcslen((WCHAR *)wide) >= CAPACITY - 4) {
        metadata_failure(error, "Metadata path exceeds the supported UTF-16 bound"); goto cleanup;
    }
    for (size_t i = 0; wide[i]; ++i) if (wide[i] == '/') wide[i] = '\\';
    normalized = HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, CAPACITY * sizeof(WCHAR));
    extended = HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, CAPACITY * sizeof(WCHAR));
    final = HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, CAPACITY * sizeof(WCHAR));
    if (!normalized || !extended || !final) { metadata_failure(error, "Cannot allocate bounded metadata paths"); goto cleanup; }
    /* Rooted drive paths need no process CWD. Normalize their ordinary dot
     * aliases lexically, then add the extended prefix; GetFullPathNameW itself
     * otherwise retains a MAX_PATH opt-in requirement for ordinary input. */
    normalized[0] = wide[0]; normalized[1] = ':'; normalized[2] = '\\';
    length = 3;
    for (size_t i = 3; wide[i];) {
        size_t first = i;
        while (wide[i] && wide[i] != '\\') ++i;
        size_t count = i - first;
        if (wide[i]) ++i;
        if (!count || (count == 1 && wide[first] == '.')) continue;
        if (count == 2 && wide[first] == '.' && wide[first + 1] == '.') {
            while (length > 3 && normalized[length - 1] != '\\') --length;
            if (length > 3) --length;
            normalized[length] = 0;
            continue;
        }
        if (!metadata_component((WCHAR *)wide + first, count)) {
            metadata_failure(error, "Metadata path contains an unsupported device or component"); goto cleanup;
        }
        if (length + count + 1 >= CAPACITY - 4) {
            metadata_failure(error, "Normalized metadata path exceeds the UTF-16 bound"); goto cleanup;
        }
        if (length > 3) normalized[length++] = '\\';
        memcpy(normalized + length, wide + first, count * sizeof(WCHAR));
        length += (DWORD)count;
        normalized[length] = 0;
    }
    memcpy(extended, L"\\\\?\\", 4 * sizeof(WCHAR));
    memcpy(extended + 4, normalized, (length + 1) * sizeof(WCHAR));
    /* OPEN_REPARSE_POINT protects only the last component. Inspect every
     * ancestor without following it before opening the normalized target. */
    for (DWORD i = 7; extended[i]; ++i) if (extended[i] == '\\') {
        FILE_ATTRIBUTE_TAG_INFO ancestor = {0};
        extended[i] = 0;
        HANDLE parent = CreateFileW(extended, FILE_READ_ATTRIBUTES, sharing, NULL, OPEN_EXISTING, flags, NULL);
        extended[i] = '\\';
        if (parent == INVALID_HANDLE_VALUE) { metadata_win32_failure(error, "Open metadata ancestor", GetLastError()); goto cleanup; }
        BOOL queried = GetFileInformationByHandleEx(parent, FileAttributeTagInfo, &ancestor, sizeof(ancestor));
        DWORD code = queried ? ERROR_SUCCESS : GetLastError();
        CloseHandle(parent);
        if (!queried) { metadata_win32_failure(error, "Query metadata ancestor", code); goto cleanup; }
        if (ancestor.FileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) {
            metadata_failure(error, "Metadata does not follow reparse components"); goto cleanup;
        }
    }
    file = CreateFileW(extended, FILE_READ_ATTRIBUTES, sharing, NULL, OPEN_EXISTING, flags, NULL);
    if (file == INVALID_HANDLE_VALUE) { metadata_win32_failure(error, "Open metadata object", GetLastError()); goto cleanup; }
#define QUERY_METADATA(kind, output) do { \
    if (!GetFileInformationByHandleEx(file, kind, &(output), sizeof(output))) { \
        metadata_win32_failure(error, "Query " #kind, GetLastError()); goto cleanup; \
    } \
} while (0)
    QUERY_METADATA(FileIdInfo, identity);
    QUERY_METADATA(FileBasicInfo, basic);
    QUERY_METADATA(FileStandardInfo, standard);
    QUERY_METADATA(FileAttributeTagInfo, attributes);
#undef QUERY_METADATA
    if (standard.DeletePending || (attributes.FileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) ||
        (basic.FileAttributes & FILE_ATTRIBUTE_REPARSE_POINT)) {
        metadata_failure(error, "Metadata rejects delete-pending objects and reparse points"); goto cleanup;
    }
    if (standard.EndOfFile.QuadPart < 0 || !standard.NumberOfLinks) {
        metadata_failure(error, "Metadata provider returned unsupported file information"); goto cleanup;
    }
    length = GetFinalPathNameByHandleW(file, final, CAPACITY, FILE_NAME_OPENED | VOLUME_NAME_GUID);
    if (!length || length >= CAPACITY || length < 49 || wcsncmp(final, L"\\\\?\\Volume{", 11) ||
        final[47] != '}' || final[48] != '\\') {
        metadata_failure(error, "Metadata requires a handle-resolved local volume GUID"); goto cleanup;
    }
    memcpy(volume, final, 49 * sizeof(WCHAR));
    drive = GetDriveTypeW(volume);
    if (drive != DRIVE_FIXED && drive != DRIVE_REMOVABLE) {
        metadata_failure(error, "Metadata requires a verified local fixed or removable volume"); goto cleanup;
    }
    if (!GetVolumeInformationByHandleW(file, NULL, 0, NULL, NULL, NULL, filesystem, 32)) {
        metadata_win32_failure(error, "Query metadata filesystem", GetLastError()); goto cleanup;
    }
    if (_wcsicmp(filesystem, L"NTFS")) { metadata_failure(error, "Metadata supports local NTFS only"); goto cleanup; }
    for (unsigned i = 0; i < 36; ++i) {
        WCHAR c = final[11 + i];
        if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F') || c == '-')) {
            metadata_failure(error, "Metadata volume GUID is not ASCII"); goto cleanup;
        }
        guid[i] = (char)(c >= 'A' && c <= 'F' ? c + ('a' - 'A') : c);
    }
    for (unsigned i = 0; i < 16; ++i) {
        static const char hex[] = "0123456789abcdef";
        identifier[i * 2] = hex[identity.FileId.Identifier[i] >> 4];
        identifier[i * 2 + 1] = hex[identity.FileId.Identifier[i] & 15];
    }
    stamp = g_strdup_printf("ntfs-v1|volume=%s|serial=%016llx|id=%s|created=%016llx|changed=%016llx|written=%016llx"
                            "|attributes=%08lx|size=%016llx|links=%08lx|directory=%u|tag=%08lx", guid,
                            (unsigned long long)identity.VolumeSerialNumber, identifier,
                            (unsigned long long)basic.CreationTime.QuadPart, (unsigned long long)basic.ChangeTime.QuadPart,
                            (unsigned long long)basic.LastWriteTime.QuadPart, (unsigned long)basic.FileAttributes,
                            standard.Directory ? 0ULL : (unsigned long long)standard.EndOfFile.QuadPart,
                            (unsigned long)standard.NumberOfLinks, standard.Directory ? 1u : 0u,
                            (unsigned long)attributes.ReparseTag);
cleanup:
    if (file != INVALID_HANDLE_VALUE) CloseHandle(file);
    if (normalized) HeapFree(GetProcessHeap(), 0, normalized);
    if (extended) HeapFree(GetProcessHeap(), 0, extended);
    if (final) HeapFree(GetProcessHeap(), 0, final);
    g_free(wide);
    return stamp;
}

#else
int sumi_explorer_platform_is_windows(void) { return 0; }

int sumi_explorer_platform_shell(void *surface, const char *const *paths,
                                unsigned count, char **error)
{
    (void)surface;
    if (validate(paths, count, error) < 0) return -1;
    return fail(error, "Windows Shell menus are unsupported on this platform");
}

int sumi_explorer_platform_permissions(void *surface, const char *path, char **error)
{
    (void)surface;
    if (validate(&path, 1, error) < 0) return -1;
    return fail(error, "Windows Security property pages are unsupported on this platform");
}

char *sumi_explorer_platform_metadata_stamp(const char *path, char **error)
{
    if (!metadata_validate(path, error)) return NULL;
    return metadata_failure(error, "Local NTFS metadata is unsupported on this platform");
}
#endif
