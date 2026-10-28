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

#ifdef _WIN32
#define COBJMACROS
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <shlobj.h>
#include <shellapi.h>
extern HWND gdk_win32_surface_get_handle(void *);

typedef struct {
    IContextMenu2 *menu2;
    IContextMenu3 *menu3;
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
    parent = surface_handle(surface, error);
    if (surface && !parent) return -1;
    result = CoInitializeEx(NULL, COINIT_APARTMENTTHREADED | COINIT_DISABLE_OLE1DDE);
    if (FAILED(result)) return hresult_error(error, "Initialize Shell COM apartment", result);
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
            if (code) win32_error(error, "Track native Shell menu", code);
            else outcome = 0;
        } else if (selected > 0x7fff || selected - 1 >= command_count)
            fail(error, "Shell returned an out-of-range command");
        else {
            CMINVOKECOMMANDINFOEX invoke = {0};
            UINT offset = selected - 1;
            invoke.cbSize = sizeof(invoke);
            invoke.fMask = CMIC_MASK_UNICODE | CMIC_MASK_PTINVOKE;
            invoke.hwnd = parent;
            invoke.lpVerb = MAKEINTRESOURCEA(offset);
            invoke.lpVerbW = MAKEINTRESOURCEW(offset);
            invoke.nShow = SW_SHOWNORMAL;
            invoke.ptInvoke = point;
            result = IContextMenu_InvokeCommand(context, (LPCMINVOKECOMMANDINFO)&invoke);
            if (FAILED(result)) hresult_error(error, "Invoke native Shell command", result);
            else outcome = 1;
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
    CoUninitialize();
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
#endif
