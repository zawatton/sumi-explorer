#ifndef SUMI_EXPLORER_PLATFORM_H
#define SUMI_EXPLORER_PLATFORM_H

#ifdef __cplusplus
extern "C" {
#endif

/* Call on the UI thread with a live GdkSurface owned by the caller.
 * Shell invocation requires this persistent window, kept alive for any native
 * modeless UI; NULL is an explicit error. Permissions may use NULL ownership.
 * No foreign window is subclassed.
 * Paths are frozen, NUL-terminated scalar UTF-8 filesystem identities.
 * Returns 1 for actual native invocation, 0 for menu cancellation, -1 on error.
 * Invocation never claims that a Shell verb completed or an ACL was applied.
 * A non-NULL error output receives a GLib allocation, released with g_free().
 * Neither function takes ownership of the surface, path array or strings.
 */
int sumi_explorer_platform_is_windows(void);
int sumi_explorer_platform_shell(void *gdk_surface, const char *const *paths,
                                 unsigned count, char **error);
int sumi_explorer_platform_permissions(void *gdk_surface, const char *path,
                                       char **error);

#ifdef __cplusplus
}
#endif
#endif
