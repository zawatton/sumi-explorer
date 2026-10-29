/* Executable validation and explicit unsupported behavior; no simulated OS UI. */
#include "platform.h"
#include <glib.h>
#include <string.h>

static void expect_error(const char *const *paths, unsigned count, const char *reason)
{
    char *error = NULL;
    g_assert_cmpint(sumi_explorer_platform_shell(NULL, paths, count, &error), ==, -1);
    g_assert_nonnull(error);
    g_assert_nonnull(strstr(error, reason));
    g_free(error);
}

static void platform_identity(void)
{
#ifdef _WIN32
    g_assert_cmpint(sumi_explorer_platform_is_windows(), ==, 1);
#else
    g_assert_cmpint(sumi_explorer_platform_is_windows(), ==, 0);
#endif
}

static void path_count(void)
{
    const char *paths[] = {"/fixture/file"};
    expect_error(NULL, 1, "1..256");
    expect_error(paths, 0, "1..256");
    expect_error(paths, 257, "1..256");
    {
        const char *missing[] = {NULL};
        expect_error(missing, 1, "null path");
    }
}

static void path_utf8(void)
{
    const char *bad[] = {"/fixture/\xff", "/fixture/\xed\xa0\x80",
                         "/fixture/\xf4\x90\x80\x80", "/fixture/\xc0\xaf"};
    unsigned i;
    for (i = 0; i < G_N_ELEMENTS(bad); ++i) expect_error(&bad[i], 1, "scalar UTF-8");
#ifndef _WIN32
    {
        const char *paths[] = {"/fixture/ 日本語;\n\t\\ spaced "};
        expect_error(paths, 1, "unsupported");
    }
#endif
}

static void path_identity(void)
{
    const char *relative[] = {"relative"}, *empty[] = {""};
    expect_error(relative, 1, "absolute");
    expect_error(empty, 1, "scalar UTF-8");
#ifndef _WIN32
    {
        const char *duplicate[] = {"/fixture/same", "/fixture/same"};
        expect_error(duplicate, 2, "duplicate");
    }
#endif
}

static void path_budget(void)
{
    char *large = g_malloc0(1024 * 1024 + 2);
    const char *paths[256];
    unsigned i;
    memset(large, 'x', 1024 * 1024 + 1);
    large[0] = '/';
    paths[0] = large;
    expect_error(paths, 1, "1 MiB");
    g_free(large);
#ifndef _WIN32
    for (i = 0; i < 256; ++i) paths[i] = g_strdup_printf("/fixture/path%u", i);
    expect_error(paths, 256, "unsupported");
    for (i = 0; i < 256; ++i) g_free((void *)paths[i]);
#else
    (void)i;
#endif
}

static void permission_contract(void)
{
    char *error = NULL;
    g_assert_cmpint(sumi_explorer_platform_permissions(NULL, NULL, &error), ==, -1);
    g_assert_nonnull(error);
    g_free(error);
    g_assert_cmpint(sumi_explorer_platform_permissions(NULL, "relative", NULL), ==, -1);
#ifndef _WIN32
    error = NULL;
    g_assert_cmpint(sumi_explorer_platform_permissions(NULL, "/fixture/日本語", &error), ==, -1);
    g_assert_nonnull(strstr(error, "unsupported"));
    g_free(error);
#endif
}

int main(int argc, char **argv)
{
    g_test_init(&argc, &argv, NULL);
    g_test_add_func("/platform/identity", platform_identity);
    g_test_add_func("/platform/count", path_count);
    g_test_add_func("/platform/utf8", path_utf8);
    g_test_add_func("/platform/paths", path_identity);
    g_test_add_func("/platform/budget", path_budget);
    g_test_add_func("/platform/permissions", permission_contract);
    return g_test_run();
}
