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

/* Read-only metadata capability for verified local NTFS objects. Returns a
 * bounded versioned ASCII stamp, or NULL with an optional GLib-owned error.
 * The caller frees either allocation with g_free; an existing *error is never
 * replaced. Paths must be NUL-terminated scalar UTF-8 with no hidden NUL.
 * Reparse components, streams, device/network paths and unsupported filesystems
 * fail explicitly. Timestamps can be delayed/reset: this is no immutable
 * generation or atomic exclusion of external writers.
 * v1 fields: ntfs-v1|volume=GUID|serial=16hex|id=32hex|created=16hex|
 * changed=16hex|written=16hex|attributes=8hex|size=16hex|links=8hex|
 * directory=0/1|tag=8hex. Last-access time and pathname aliases are excluded. */
char *sumi_explorer_platform_metadata_stamp(const char *absolute_utf8_path,
                                           char **error);

#ifdef __cplusplus
}
#endif
#endif
