/* Native runtime tests: filesystem Shell objects, menu loop and COM ABI.
 * Include the adapter to exercise its static window procedure without adding
 * production test APIs. A generated include directory supplies mutants. */
#include <stdio.h>
#include <stdlib.h>
#include <wchar.h>
#include <platform.c>

/* Public GIO ABI. Keep SDK-only syntax checks independent of glibconfig.h. */
typedef struct _GFile GFile;
extern GFile *g_file_new_for_path(const char *);
extern char *g_file_get_path(GFile *);
extern int g_file_query_exists(GFile *, void *);
extern void g_object_unref(void *);

static int failures;
#define CHECK(condition, name) do { \
    int passed = !!(condition); \
    printf("%s %s\n", passed ? "PASS" : "FAIL", name); \
    fflush(stdout); if (!passed) ++failures; \
} while (0)

static unsigned forwarded;
static HRESULT STDMETHODCALLTYPE fake_query(IContextMenu2 *self, REFIID iid, void **out)
{ (void)self; (void)iid; *out = NULL; return E_NOINTERFACE; }
static ULONG STDMETHODCALLTYPE fake_ref(IContextMenu2 *self)
{ (void)self; return 1; }
static HRESULT STDMETHODCALLTYPE fake_populate(IContextMenu2 *self, HMENU menu,
    UINT index, UINT first, UINT last, UINT flags)
{ (void)self; (void)menu; (void)index; (void)first; (void)last; (void)flags; return E_NOTIMPL; }
static HRESULT STDMETHODCALLTYPE fake_invoke(IContextMenu2 *self, LPCMINVOKECOMMANDINFO info)
{ (void)self; (void)info; return E_NOTIMPL; }
static HRESULT STDMETHODCALLTYPE fake_string(IContextMenu2 *self, UINT_PTR id,
    UINT flags, UINT *reserved, LPSTR output, UINT capacity)
{ (void)self; (void)id; (void)flags; (void)reserved; (void)output; (void)capacity; return E_NOTIMPL; }
static HRESULT STDMETHODCALLTYPE fake_message(IContextMenu2 *self, UINT message, WPARAM w, LPARAM l)
{ (void)self; (void)message; (void)w; (void)l; ++forwarded; return S_OK; }
static IContextMenu2Vtbl fake_vtable = {
    fake_query, fake_ref, fake_ref, fake_populate, fake_invoke, fake_string, fake_message
};

static BOOL CALLBACK count_owned(HWND window, LPARAM value)
{
    WCHAR name[80];
    GetClassNameW(window, name, 80);
    if (!wcscmp(name, L"SumiExplorerPlatformMenuOwner")) ++*(unsigned *)value;
    return TRUE;
}
static unsigned owner_count(void)
{
    unsigned count = 0;
    EnumThreadWindows(GetCurrentThreadId(), count_owned, (LPARAM)&count);
    return count;
}
static void test_forwarding(void)
{
    IContextMenu2 fake = {&fake_vtable};
    MenuBridge bridge = {&fake, NULL};
    char *error = NULL;
    HWND owner = menu_owner(NULL, &bridge, &error);
    CHECK(owner != NULL && !error, "IContextMenu2-only owner creation");
    if (owner) {
        DRAWITEMSTRUCT draw = {0};
        MEASUREITEMSTRUCT measure = {0};
        draw.CtlType = ODT_MENU; measure.CtlType = ODT_MENU;
        CHECK(SendMessageW(owner, WM_DRAWITEM, 0, (LPARAM)&draw) == TRUE,
              "IContextMenu2 WM_DRAWITEM handled return");
        CHECK(SendMessageW(owner, WM_MEASUREITEM, 0, (LPARAM)&measure) == TRUE,
              "IContextMenu2 WM_MEASUREITEM handled return");
        CHECK(forwarded == 2, "IContextMenu2 owner-draw dispatch count");
        SendMessageW(owner, WM_INITMENUPOPUP, 0, 0);
        SendMessageW(owner, WM_MENUCHAR, 0, 0);
        CHECK(forwarded == 3, "IContextMenu2 init forwarded and menuchar excluded");
        DestroyWindow(owner);
    }
    g_free(error);
    CHECK(owner_count() == 0, "owner-draw window cleanup");
}

static HMENU active_menu;
static HWND active_owner;
static HHOOK observe_hook;
static int cancel_menu, automation_done;
static ULONGLONG menu_started;
static unsigned menu_initializations, menu_exits, timer_callbacks;
static UINT automation_target_id, observed_selection;

/* Observe only this thread's adapter-owned menu, without replacing its proc. */
static LRESULT CALLBACK observe_menu(int code, WPARAM w, LPARAM l)
{
    DWORD saved_error = GetLastError();
    if (code >= 0) {
        const CWPSTRUCT *event = (const CWPSTRUCT *)l;
        WCHAR name[80];
        GetClassNameW(event->hwnd, name, 80);
        if (event->message == WM_INITMENUPOPUP &&
            !wcscmp(name, L"SumiExplorerPlatformMenuOwner")) {
            active_owner = event->hwnd;
            active_menu = (HMENU)event->wParam;
            ++menu_initializations;
            printf("DIAG menu-init elapsed_ms=%llu owner=%p menu=%p items=%d\n",
                   (unsigned long long)(GetTickCount64() - menu_started),
                   (void *)active_owner, (void *)active_menu, GetMenuItemCount(active_menu));
        }
        if (event->hwnd == active_owner && event->message == WM_EXITMENULOOP) ++menu_exits;
        if (event->hwnd == active_owner && event->message == WM_MENUSELECT &&
            HIWORD(event->wParam) != 0xffff && !(HIWORD(event->wParam) & MF_POPUP)) {
            observed_selection = LOWORD(event->wParam);
        }
    }
    LRESULT result = CallNextHookEx(observe_hook, code, w, l);
    SetLastError(saved_error);
    return result;
}

static void drive_menu_action(void)
{
    ++timer_callbacks;
    if (!active_owner || !IsWindow(active_owner)) return;
    if (cancel_menu || GetTickCount64() - menu_started > 8000) {
        automation_done = cancel_menu ? 1 : -1;
        printf("DIAG menu-cancel requested=%d elapsed_ms=%llu incoming_last_error=%lu\n",
               cancel_menu, (unsigned long long)(GetTickCount64() - menu_started),
               (unsigned long)GetLastError());
        SendMessageW(active_owner, WM_CANCELMODE, 0, 0);
        return;
    }
    if (automation_done) return;
    MenuBridge *bridge = (MenuBridge *)GetWindowLongPtrW(active_owner, GWLP_USERDATA);
    IContextMenu *context = bridge ? (IContextMenu *)(bridge->menu2
        ? (void *)bridge->menu2 : (void *)bridge->menu3) : NULL;
    if (!context || !active_menu) return;
    for (int i = 0; i < GetMenuItemCount(active_menu); ++i) {
        UINT id = GetMenuItemID(active_menu, i);
        WCHAR verb[128] = {0};
        RECT rect;
        if (id == (UINT)-1 || id < 1 || id > 0x7fff) continue;
        if (FAILED(IContextMenu_GetCommandString(context, id - 1, GCS_VERBW,
            NULL, (LPSTR)verb, 128)) || wcscmp(verb, L"copy")) continue;
        if (!GetMenuItemRect(NULL, active_menu, (UINT)i, &rect)) continue;
        POINT point = {(rect.left + rect.right) / 2, (rect.top + rect.bottom) / 2};
        DWORD process = 0;
        HWND hit = WindowFromPoint(point);
        WCHAR name[32];
        GetWindowThreadProcessId(hit, &process);
        GetClassNameW(hit, name, 32);
        if (process != GetCurrentProcessId() || wcscmp(name, L"#32768")) continue;
        /* Inject only after identifying an actual popup in this process. */
        INPUT clicks[2] = {{0}, {0}};
        clicks[0].type = INPUT_MOUSE; clicks[0].mi.dwFlags = MOUSEEVENTF_LEFTDOWN;
        clicks[1].type = INPUT_MOUSE; clicks[1].mi.dwFlags = MOUSEEVENTF_LEFTUP;
        if (SetCursorPos(point.x, point.y) && SendInput(2, clicks, sizeof(INPUT)) == 2) {
            automation_target_id = id;
            automation_done = 1;
            printf("DIAG menu-copy-click id=%u elapsed_ms=%llu point=%ld,%ld popup=%p\n",
                   id, (unsigned long long)(GetTickCount64() - menu_started),
                   (long)point.x, (long)point.y, (void *)hit);
        }
        return;
    }
}

static VOID CALLBACK drive_menu(HWND window, UINT message, UINT_PTR timer, DWORD time)
{
    DWORD saved_error = GetLastError();
    (void)window; (void)message; (void)timer; (void)time;
    drive_menu_action();
    SetLastError(saved_error);
}

static void reset_menu_observation(void)
{
    automation_done = 0; active_owner = NULL; active_menu = NULL;
    menu_initializations = 0; menu_exits = 0; timer_callbacks = 0;
    automation_target_id = 0; observed_selection = 0;
    menu_started = GetTickCount64();
}

static void log_shell_result(const char *action, int result, const char *error, DWORD last_error)
{
    printf("DIAG shell-%s result=%d error=%s last_error=%lu elapsed_ms=%llu automation=%d "
           "init=%u exits=%u timers=%u clicked_id=%u selected_id=%u\n", action, result,
           error ? error : "<none>", (unsigned long)last_error,
           (unsigned long long)(GetTickCount64() - menu_started), automation_done,
           menu_initializations, menu_exits, timer_callbacks, automation_target_id, observed_selection);
    fflush(stdout);
}

static void log_wide_path(const char *label, const WCHAR *path)
{
    char utf8[4096] = {0};
    int converted = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, path, -1,
                                       utf8, sizeof(utf8), NULL, NULL);
    printf("DIAG %s utf16_length=%llu utf8=%s\n", label,
           (unsigned long long)wcslen(path), converted ? utf8 : "<conversion failed>");
}

/* A Shell clipboard provider can complete work through its own thread windows.
 * Pump only this test thread's messages and dispatch only its process windows.
 * The caller retains the original five-second observation deadline. */
static void pump_owned_messages(void)
{
    MSG event;
    for (unsigned i = 0; i < 64 && PeekMessageW(&event, NULL, 0, 0, PM_REMOVE); ++i) {
        DWORD process = 0;
        if (event.message == WM_QUIT) { PostQuitMessage((int)event.wParam); break; }
        if (event.hwnd) GetWindowThreadProcessId(event.hwnd, &process);
        if (!event.hwnd || process == GetCurrentProcessId()) {
            TranslateMessage(&event);
            DispatchMessageW(&event);
        }
    }
    MsgWaitForMultipleObjectsEx(0, NULL, 50, QS_ALLINPUT, MWMO_INPUTAVAILABLE);
}

static void test_shell(const char *const *paths, const WCHAR *first, const WCHAR *second)
{
    char *error = NULL;
    observe_hook = SetWindowsHookExW(WH_CALLWNDPROC, observe_menu, NULL, GetCurrentThreadId());
    UINT_PTR timer = SetTimer(NULL, 0, 50, drive_menu);
    CHECK(observe_hook && timer, "owned real menu automation installed");
    if (!observe_hook || !timer) goto done;
    cancel_menu = 1; reset_menu_observation();
    int result = sumi_explorer_platform_shell(NULL, paths, 2, &error);
    DWORD returned_error = GetLastError();
    log_shell_result("cancel", result, error, returned_error);
    CHECK(result == 0 && !error && automation_done == 1, "real COM multi-selection menu cancellation");
    CHECK(owner_count() == 0, "canceled menu owner cleanup");
    g_free(error); error = NULL;
    int opened = OpenClipboard(NULL);
    CHECK(opened, "clipboard open before Copy");
    if (opened) { EmptyClipboard(); CloseClipboard(); }
    DWORD clipboard_before = GetClipboardSequenceNumber();
    cancel_menu = 0; reset_menu_observation();
    result = sumi_explorer_platform_shell(NULL, paths, 2, &error);
    returned_error = GetLastError();
    log_shell_result("copy", result, error, returned_error);
    CHECK(result == 1 && !error && automation_done == 1, "real COM Copy menu invocation");
    int matched = 0, paths_logged = 0;
    unsigned attempts = 0, open_failures = 0;
    UINT seen_count = 0;
    DWORD open_error = 0, data_error = 0;
    ULONGLONG clipboard_started = GetTickCount64();
    log_wide_path("expected-first", first); log_wide_path("expected-second", second);
    while (!matched && GetTickCount64() - clipboard_started < 5000) {
        ++attempts;
        if (OpenClipboard(NULL)) {
            SetLastError(ERROR_SUCCESS);
            HDROP drop = (HDROP)GetClipboardData(CF_HDROP);
            data_error = GetLastError();
            seen_count = drop ? DragQueryFileW(drop, 0xffffffff, NULL, 0) : 0;
            if (drop && seen_count == 2) {
                WCHAR a[32768] = {0}, b[32768] = {0};
                DragQueryFileW(drop, 0, a, 32768); DragQueryFileW(drop, 1, b, 32768);
                if (!paths_logged) {
                    log_wide_path("clipboard-first", a); log_wide_path("clipboard-second", b);
                    paths_logged = 1;
                }
                matched = (!wcscmp(a, first) && !wcscmp(b, second)) ||
                          (!wcscmp(a, second) && !wcscmp(b, first));
            }
            CloseClipboard();
        } else { ++open_failures; open_error = GetLastError(); }
        if (!matched) pump_owned_messages();
    }
    DWORD clipboard_process = 0;
    HWND clipboard_owner = GetClipboardOwner();
    GetWindowThreadProcessId(clipboard_owner, &clipboard_process);
    printf("DIAG clipboard matched=%d elapsed_ms=%llu attempts=%u open_failures=%u open_error=%lu "
           "hdrop_available=%d count=%u data_error=%lu sequence_before=%lu sequence_after=%lu "
           "owner=%p owner_pid=%lu test_pid=%lu\n", matched,
           (unsigned long long)(GetTickCount64() - clipboard_started), attempts, open_failures,
           (unsigned long)open_error, IsClipboardFormatAvailable(CF_HDROP), seen_count,
           (unsigned long)data_error, (unsigned long)clipboard_before,
           (unsigned long)GetClipboardSequenceNumber(), (void *)clipboard_owner,
           (unsigned long)clipboard_process, (unsigned long)GetCurrentProcessId());
    if (OpenClipboard(NULL)) {
        UINT format = 0;
        printf("DIAG clipboard-format-ids");
        for (unsigned i = 0; i < 16 && (format = EnumClipboardFormats(format)); ++i)
            printf(" %u", format);
        printf("\n");
        CloseClipboard();
    }
    CHECK(matched, "Copy clipboard CF_HDROP contains both frozen Unicode filesystem targets");
    CHECK(owner_count() == 0, "invoked menu owner cleanup");
done:
    if (timer) KillTimer(NULL, timer);
    if (observe_hook) UnhookWindowsHookEx(observe_hook);
    g_free(error);
}

static void test_error(const char *const *paths, unsigned count, const char *name)
{
    char *error = NULL;
    int result = sumi_explorer_platform_shell(NULL, paths, count, &error);
    CHECK(result == -1 && error && *error, name);
    g_free(error);
}

int main(int argc, char **argv)
{
    CHECK(sumi_explorer_platform_is_windows() == 1, "native Windows target");
    test_forwarding();
    const char *relative[] = {"relative.txt"};
    const char *drive_relative[] = {"C:relative.txt"};
    const char *bad_utf8[] = {"C:\\bad-\xed\xa0\x80"};
    test_error(relative, 1, "relative path rejected");
    test_error(drive_relative, 1, "drive-relative path rejected");
    test_error(bad_utf8, 1, "non-scalar UTF-8 rejected");
    test_error(NULL, 0, "empty selection rejected");
    char *permission_error = NULL;
    CHECK(sumi_explorer_platform_permissions(NULL, relative[0], &permission_error) == -1
          && permission_error && *permission_error, "Security editor relative path rejected before invocation");
    g_free(permission_error);
    if (argc == 2 && !strcmp(argv[1], "--unit-only")) return failures ? 1 : 0;
    WCHAR temp[MAX_PATH], root[MAX_PATH], first[MAX_PATH], second[MAX_PATH];
    int created = GetTempPathW(MAX_PATH, temp) && GetTempFileNameW(temp, L"sum", 0, root);
    CHECK(created, "temporary fixture identity");
    if (!created) return 1;
    DeleteFileW(root);
    if (!CreateDirectoryW(root, NULL)) { CHECK(0, "fixture directory created"); return 1; }
    swprintf(first, MAX_PATH, L"%ls\\\u65e5\u672c\u8a9e space.txt", root);
    swprintf(second, MAX_PATH, L"%ls\\\u5225\u306e\u30d5\u30a1\u30a4\u30eb.txt", root);
    const char bytes[] = "Sumi native fixture\r\n";
    const WCHAR *wide_paths[] = {first, second};
    char *paths[2] = {NULL, NULL};
    for (unsigned i = 0; i < 2; ++i) {
        HANDLE file = CreateFileW(wide_paths[i], GENERIC_WRITE, 0, NULL, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, NULL);
        DWORD written = 0;
        CHECK(file != INVALID_HANDLE_VALUE && WriteFile(file, bytes, sizeof(bytes), &written, NULL)
              && written == sizeof(bytes), "Unicode fixture file bytes written");
        if (file != INVALID_HANDLE_VALUE) CloseHandle(file);
        int length = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, wide_paths[i], -1, NULL, 0, NULL, NULL);
        paths[i] = calloc((size_t)length, 1);
        if (!paths[i] || !WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, wide_paths[i], -1,
                                             paths[i], length, NULL, NULL)) return 1;
        GFile *gio = g_file_new_for_path(paths[i]);
        char *roundtrip = g_file_get_path(gio);
        CHECK(roundtrip && !strcmp(roundtrip, paths[i]) && g_file_query_exists(gio, NULL),
              "GIO native absolute Unicode path roundtrip and existence");
        g_free(roundtrip); g_object_unref(gio);
    }
    const char *frozen[] = {paths[0], paths[1]};
    const char *duplicates[] = {paths[0], paths[0]};
    test_error(duplicates, 2, "duplicate frozen paths rejected");
    char *absent = g_strdup_printf("%s.missing", paths[0]);
    const char *missing[] = {absent};
    test_error(missing, 1, "nonexistent Unicode filesystem Shell object errors");
    g_free(absent);
    test_shell(frozen, first, second);
    printf("NOTRUN Security page visual observation, cancellation and ACL Apply (manual Windows GUI acceptance)\n");
    for (unsigned i = 0; i < 2; ++i) {
        char readback[sizeof(bytes)] = {0}; DWORD size = 0;
        HANDLE file = CreateFileW(wide_paths[i], GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, 0, NULL);
        CHECK(file != INVALID_HANDLE_VALUE && ReadFile(file, readback, sizeof(readback), &size, NULL)
            && size == sizeof(bytes) && !memcmp(bytes, readback, sizeof(bytes)), "Shell Copy preserved source bytes");
        if (file != INVALID_HANDLE_VALUE) CloseHandle(file);
        CHECK(DeleteFileW(wide_paths[i]), "temporary file cleanup");
        free(paths[i]);
    }
    CHECK(RemoveDirectoryW(root), "temporary directory cleanup");
    printf("RESULT failures=%d; native invocation does not certify external verb completion or ACL Apply\n", failures);
    return failures ? 1 : 0;
}
