/* Native runtime tests: filesystem Shell objects, menu loop and COM ABI.
 * Include the adapter to exercise its static window procedure without adding
 * production test APIs. A generated include directory supplies mutants. */
#include <stdio.h>
#include <stdlib.h>
#include <wchar.h>
#define COBJMACROS
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <ole2.h>
#include <shlobj.h>

static HWND observed_verb_owner, modeless_fixture;
static unsigned observed_invocations;
static HRESULT observe_context_invoke(IContextMenu *context, LPCMINVOKECOMMANDINFO info)
{
    DWORD saved_error = GetLastError();
    observed_verb_owner = info->hwnd;
    ++observed_invocations;
    /* The retained modeless window uses exactly the owner passed to the real
     * verb. An ephemeral owner would destroy it during adapter cleanup. */
    modeless_fixture = CreateWindowExW(0, L"STATIC", L"Sumi modeless verb lifetime fixture",
        WS_OVERLAPPEDWINDOW, 0, 0, 100, 100, info->hwnd, NULL, GetModuleHandleW(NULL), NULL);
    printf("DIAG invoke-owner hwnd=%p modeless_fixture=%p\n", (void *)info->hwnd, (void *)modeless_fixture);
    SetLastError(saved_error);
    return context->lpVtbl->InvokeCommand(context, info);
}

/* Observe the actual OLE calls without substituting their behavior. */
static unsigned observed_flush_calls;
static HRESULT WINAPI observe_ole_initialize(void *reserved)
{
    HRESULT result = OleInitialize(reserved);
    DWORD saved_error = GetLastError();
    observed_flush_calls = 0;
    printf("DIAG ole-initialize hr=0x%08lx\n", (unsigned long)(ULONG)result);
    SetLastError(saved_error);
    return result;
}
static HRESULT WINAPI observe_ole_flush(void)
{
    DWORD process = 0;
    HWND owner = GetClipboardOwner();
    DWORD thread = GetWindowThreadProcessId(owner, &process);
    DWORD sequence = GetClipboardSequenceNumber();
    ++observed_flush_calls;
    HRESULT result = OleFlushClipboard();
    DWORD saved_error = GetLastError();
    printf("DIAG ole-flush hr=0x%08lx guarded_sequence=%lu owner=%p owner_pid=%lu owner_thread=%lu "
           "test_pid=%lu test_thread=%lu resulting_sequence=%lu\n", (unsigned long)(ULONG)result,
           (unsigned long)sequence, (void *)owner, (unsigned long)process, (unsigned long)thread,
           (unsigned long)GetCurrentProcessId(), (unsigned long)GetCurrentThreadId(),
           (unsigned long)GetClipboardSequenceNumber());
    SetLastError(saved_error);
    return result;
}
static void WINAPI observe_ole_uninitialize(void)
{
    DWORD sequence_before = GetClipboardSequenceNumber();
    OleUninitialize();
    DWORD saved_error = GetLastError();
    printf("DIAG ole-uninitialize sequence_before=%lu sequence_after=%lu hdrop_available=%d flush_calls=%u\n",
           (unsigned long)sequence_before, (unsigned long)GetClipboardSequenceNumber(),
           IsClipboardFormatAvailable(CF_HDROP), observed_flush_calls);
    SetLastError(saved_error);
}
#define OleInitialize observe_ole_initialize
#define OleFlushClipboard observe_ole_flush
#define OleUninitialize observe_ole_uninitialize
#undef IContextMenu_InvokeCommand
#define IContextMenu_InvokeCommand(context, info) observe_context_invoke((context), (info))
#include <platform.c>
#undef OleInitialize
#undef OleFlushClipboard
#undef OleUninitialize
#undef IContextMenu_InvokeCommand

/* Public GIO ABI. Keep SDK-only syntax checks independent of glibconfig.h. */
#ifndef __G_TYPES_H__
typedef struct _GFile GFile;
extern GFile *g_file_new_for_path(const char *);
extern char *g_file_get_path(GFile *);
extern int g_file_query_exists(GFile *, void *);
extern void g_object_unref(void *);
extern void *g_object_ref_sink(void *);
#endif
extern void gtk_init(void);
extern void *gtk_window_new(void);
extern void gtk_window_set_title(void *, const char *);
extern void gtk_window_set_default_size(void *, int, int);
extern void gtk_window_present(void *);
extern void gtk_window_destroy(void *);
extern void *gtk_native_get_surface(void *);
#ifndef __G_TYPES_H__
extern int g_main_context_iteration(void *, int);
#endif

static void *test_surface;
static HWND persistent_parent;

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
    MenuBridge bridge = {&fake, NULL, 0, 0};
    char *error = NULL;
    HWND owner = menu_owner(NULL, &bridge, &error);
    CHECK(owner != NULL && !error, "IContextMenu2-only owner creation");
    if (owner) {
        DRAWITEMSTRUCT draw = {0};
        MEASUREITEMSTRUCT measure = {0};
        draw.CtlType = ODT_MENU; measure.CtlType = ODT_MENU;
        SendMessageW(owner, WM_ENTERMENULOOP, TRUE, 0);
        SendMessageW(owner, WM_EXITMENULOOP, TRUE, 0);
        CHECK(bridge.menu_entered && bridge.menu_exited, "owned menu-loop entry and exit accounting");
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

static void probe_shell_separators(const char *native_path)
{
    char *forward = g_strdup(native_path), *canonical = NULL;
    GFile *file = NULL;
    PIDLIST_ABSOLUTE objects[3] = {NULL, NULL, NULL};
    gunichar2 *wide[3] = {NULL, NULL, NULL};
    SFGAOF attributes[3] = {0, 0, 0};
    HRESULT parsed[3] = {E_INVALIDARG, E_INVALIDARG, E_INVALIDARG};
    const char *labels[] = {"native", "raw-forward", "gio-canonical-forward"};
    CHECK(forward != NULL, "allocate owned Unicode separator probe alias");
    if (!forward) return;
    for (char *cursor = forward; *cursor; ++cursor) if (*cursor == '\\') *cursor = '/';
    file = g_file_new_for_path(forward);
    if (file) canonical = g_file_get_path(file);
    CHECK(canonical != NULL && file && g_file_query_exists(file, NULL),
          "GIO forward-slash Unicode alias names the existing fixture");
    const char *inputs[] = {native_path, forward, canonical};
    HRESULT initialized = CoInitializeEx(NULL, COINIT_APARTMENTTHREADED | COINIT_DISABLE_OLE1DDE);
    CHECK(SUCCEEDED(initialized), "initialize real COM separator probe apartment");
    if (FAILED(initialized)) goto done;
    for (unsigned i = 0; i < 3; ++i) {
        if (inputs[i]) wide[i] = g_utf8_to_utf16(inputs[i], -1, NULL, NULL, NULL);
        if (wide[i]) {
            log_wide_path(labels[i], (LPCWSTR)wide[i]);
            parsed[i] = SHParseDisplayName((LPCWSTR)wide[i], NULL, &objects[i],
                SFGAO_FILESYSTEM, &attributes[i]);
        }
        BOOL same = SUCCEEDED(parsed[0]) && SUCCEEDED(parsed[i]) && objects[0] && objects[i] &&
            ILIsEqual(objects[0], objects[i]);
        printf("DIAG Shell separator form=%s hresult=0x%08lx attributes=0x%08lx pidl_present=%d same_native_pidl=%d\n",
               labels[i], (unsigned long)parsed[i], (unsigned long)attributes[i], objects[i] != NULL, same);
    }
    CHECK(SUCCEEDED(parsed[0]) && objects[0] && (attributes[0] & SFGAO_FILESYSTEM),
          "native Unicode Shell parsing resolves a filesystem PIDL");
    CHECK(SUCCEEDED(parsed[2]) && objects[2] && (attributes[2] & SFGAO_FILESYSTEM) &&
          objects[0] && ILIsEqual(objects[0], objects[2]),
          "GIO-canonical forward Unicode alias resolves the same real Shell PIDL");
    /* Raw-forward parsing is observational until this Windows run proves the
     * cause. Do not turn the existing native Cancel/Copy checks into guesses. */
done:
    for (unsigned i = 0; i < 3; ++i) { CoTaskMemFree(objects[i]); g_free(wide[i]); }
    if (SUCCEEDED(initialized)) CoUninitialize();
    if (file) g_object_unref(file);
    g_free(canonical); g_free(forward);
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

static void test_shell(const char *const *paths, const WCHAR *first, const WCHAR *second, int forward_alias)
{
    char *error = NULL;
    observed_invocations = 0; observed_verb_owner = NULL; modeless_fixture = NULL;
    observe_hook = SetWindowsHookExW(WH_CALLWNDPROC, observe_menu, NULL, GetCurrentThreadId());
    UINT_PTR timer = SetTimer(NULL, 0, 50, drive_menu);
    CHECK(observe_hook && timer, "owned real menu automation installed");
    if (!observe_hook || !timer) goto done;
    cancel_menu = 1; reset_menu_observation();
    int result = sumi_explorer_platform_shell(test_surface, paths, 2, &error);
    DWORD returned_error = GetLastError();
    log_shell_result("cancel", result, error, returned_error);
    CHECK(result == 0 && !error && automation_done == 1, forward_alias
          ? "real COM forward-slash Unicode menu cancellation" : "real COM multi-selection menu cancellation");
    CHECK(owner_count() == 0, "canceled menu owner cleanup");
    g_free(error); error = NULL;
    int opened = OpenClipboard(persistent_parent), marker_ready = 0;
    CHECK(opened, "clipboard open before Copy");
    if (opened) {
        WCHAR marker_text[96] = {0};
        swprintf(marker_text, 96, L"Sumi owned Copy marker pid=%lu tick=%llu alias=%d",
            (unsigned long)GetCurrentProcessId(), (unsigned long long)GetTickCount64(), forward_alias);
        HGLOBAL marker = EmptyClipboard() ? GlobalAlloc(GMEM_MOVEABLE, sizeof(marker_text)) : NULL;
        void *memory = marker ? GlobalLock(marker) : NULL;
        if (memory) {
            memcpy(memory, marker_text, sizeof(marker_text));
            GlobalUnlock(marker);
            if (SetClipboardData(CF_UNICODETEXT, marker)) { marker_ready = 1; marker = NULL; }
        }
        if (marker) GlobalFree(marker);
        CloseClipboard();
    }
    CHECK(marker_ready && !IsClipboardFormatAvailable(CF_HDROP), "fresh owned clipboard marker excludes previous CF_HDROP");
    DWORD clipboard_before = GetClipboardSequenceNumber();
    cancel_menu = 0; reset_menu_observation();
    result = sumi_explorer_platform_shell(test_surface, paths, 2, &error);
    returned_error = GetLastError();
    log_shell_result("copy", result, error, returned_error);
    CHECK(result == 1 && !error && automation_done == 1, forward_alias
          ? "real COM forward-slash Unicode Copy invocation" : "real COM Copy menu invocation");
    CHECK(observed_invocations == 1 && observed_verb_owner == persistent_parent && IsWindow(persistent_parent),
          "Shell verb uses persistent GTK caller ownership");
    CHECK(modeless_fixture && IsWindow(modeless_fixture), "modeless verb window survives menu-owner cleanup");
    if (modeless_fixture && IsWindow(modeless_fixture)) DestroyWindow(modeless_fixture);
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
    CHECK(marker_ready && GetClipboardSequenceNumber() != clipboard_before && matched, forward_alias
          ? "forward-slash Copy CF_HDROP contains both original Unicode filesystem targets"
          : "Copy clipboard CF_HDROP contains both frozen Unicode filesystem targets");
    CHECK(owner_count() == 0, "invoked menu owner cleanup");
done:
    if (timer) KillTimer(NULL, timer);
    if (observe_hook) UnhookWindowsHookEx(observe_hook);
    g_free(error);
}

static void test_error(const char *const *paths, unsigned count, const char *name)
{
    char *error = NULL;
    int result = sumi_explorer_platform_shell(test_surface, paths, count, &error);
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
    const char *valid_without_surface[] = {"C:\\fixture"};
    test_error(valid_without_surface, 1, "Shell invocation without persistent surface explicitly rejected");
    gtk_init();
    void *gtk_window = gtk_window_new();
    g_object_ref_sink(gtk_window);
    gtk_window_set_title(gtk_window, "Sumi native Shell ownership fixture");
    gtk_window_set_default_size(gtk_window, 320, 200);
    gtk_window_present(gtk_window);
    ULONGLONG gtk_started = GetTickCount64();
    do {
        for (unsigned i = 0; i < 64 && g_main_context_iteration(NULL, 0); ++i) { }
        test_surface = gtk_native_get_surface(gtk_window);
        if (test_surface) persistent_parent = gdk_win32_surface_get_handle(test_surface);
        if (persistent_parent && IsWindow(persistent_parent)) break;
        Sleep(10);
    } while (GetTickCount64() - gtk_started < 5000);
    CHECK(test_surface && persistent_parent && IsWindow(persistent_parent), "real GTK surface and persistent native owner");
    if (!test_surface || !persistent_parent || !IsWindow(persistent_parent)) {
        gtk_window_destroy(gtk_window); g_object_unref(gtk_window); return 1;
    }
    WCHAR temp[MAX_PATH], root[MAX_PATH], first[MAX_PATH], second[MAX_PATH];
    int created = GetTempPathW(MAX_PATH, temp) && GetTempFileNameW(temp, L"sum", 0, root);
    CHECK(created, "temporary fixture identity");
    if (!created) { gtk_window_destroy(gtk_window); g_object_unref(gtk_window); return 1; }
    DeleteFileW(root);
    if (!CreateDirectoryW(root, NULL)) {
        CHECK(0, "fixture directory created"); gtk_window_destroy(gtk_window); g_object_unref(gtk_window); return 1;
    }
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
    probe_shell_separators(paths[0]);
    probe_shell_separators(paths[1]);
    const char *duplicates[] = {paths[0], paths[0]};
    test_error(duplicates, 2, "duplicate frozen paths rejected");
    char *absent = g_strdup_printf("%s.missing", paths[0]);
    const char *missing[] = {absent};
    test_error(missing, 1, "nonexistent Unicode filesystem Shell object errors");
    g_free(absent);
    test_shell(frozen, first, second, 0);
    char *forward[2] = {g_strdup(paths[0]), g_strdup(paths[1])};
    CHECK(forward[0] && forward[1], "allocate both owned forward-slash Unicode adapter targets");
    if (forward[0] && forward[1]) {
        for (unsigned i = 0; i < 2; ++i)
            for (char *cursor = forward[i]; *cursor; ++cursor) if (*cursor == '\\') *cursor = '/';
        const char *aliases[] = {forward[0], forward[1]};
        test_shell(aliases, first, second, 1);
    }
    g_free(forward[0]); g_free(forward[1]);
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
    gtk_window_destroy(gtk_window);
    g_object_unref(gtk_window);
    CHECK(!IsWindow(persistent_parent), "owned GTK window cleanup");
    test_surface = NULL;
    printf("RESULT failures=%d; native invocation does not certify external verb completion or ACL Apply\n", failures);
    return failures ? 1 : 0;
}
