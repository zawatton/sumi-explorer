#ifndef SUMI_EXPLORER_PLATFORM_H
#define SUMI_EXPLORER_PLATFORM_H
#include "platform-zip.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Call on the UI thread with a live GdkSurface owned by the caller.
 * Shell invocation requires this persistent window, kept alive for any native
 * modeless UI; NULL is an explicit error. Permissions may use NULL ownership.
 * No foreign window is subclassed.
 * Paths are frozen, NUL-terminated scalar UTF-8 filesystem identities.
 * Windows accepts absolute forward-slash aliases and obtains an owned native
 * path for Shell APIs without changing inputs or physically resolving links.
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

/* Private profile and durable byte-storage primitives. No history is decoded
 * here. A journal has a separate stable kernel-held lock; replacing its data
 * never releases that lock. NULL name creates a new independently locked file.
 * Reads are bounded to 16 MiB, writes atomically replace and sync the file and
 * containing directory (MoveFileEx WRITE_THROUGH on Windows). Returned bytes
 * and profile paths are GLib-owned. The profile must outlive all its journals.
 * Errors fail closed; an unavailable profile never becomes volatile storage. */
void *sumi_explorer_platform_profile_open(void);
const char *sumi_explorer_platform_profile_path(void *profile);
void sumi_explorer_platform_profile_close(void *profile);
void *sumi_explorer_platform_journal_open(void *profile, const char *name);
/* Lock acquisition classification: status 0 acquired, 1 genuine kernel lock
 * contention, 2 invalid/unsafe/I/O failure. Only status 1 may be retried. */
void *sumi_explorer_platform_journal_open_status(void *profile, const char *name,
                                               int *status);
unsigned long long sumi_explorer_platform_journal_stamp(void *journal);
/* Entry status is independent of timestamps: 0 absent, 1 private regular
 * single-link entry, -1 inaccessible/unsafe/error. No read failure is absence. */
int sumi_explorer_platform_journal_status(void *journal);
char *sumi_explorer_platform_journal_read(void *journal, unsigned long long *size);
int sumi_explorer_platform_journal_write(void *journal, const void *data,
                                        unsigned long long size);
void sumi_explorer_platform_journal_close(void *journal);
/* Recovery ordering: sync one nofollow object's bytes (directories and parent
 * entries are fsynced on POSIX). Recurse in the caller. Windows file data is
 * flushed; directory metadata ordering requires recovery_rename WRITE_THROUGH.
 * Rename never replaces an existing destination or falls back across volumes.
 * Callers validate object identity/ownership separately, immediately before
 * these OS primitives. Error strings, if requested, are released with g_free. */
int sumi_explorer_platform_recovery_sync(const char *path, char **error);
/* POSIX mode inverse: acquire the object and parent before removing access,
 * then fchmod and flush that same descriptor plus its parent. Returns 1 only
 * when durable, 0 before any mutation attempt, 2 for an ambiguous mutation or
 * post-mutation barrier/close failure. An unreadable owned object may receive a
 * temporary read bit before acquiring its descriptor; failures from that first
 * mutation onward are ambiguous. Final mode and guarded identity are verified.
 * Windows portable chmod is unsupported. */
int sumi_explorer_platform_recovery_chmod(const char *path, unsigned mode, char **error);
int sumi_explorer_platform_recovery_rename(const char *source, const char *destination,
                                          char **error);
/* Flush the filesystem containing path, for opaque same-filesystem GIO Trash
 * operations whose backing parent cannot be opened through the URI. Unsupported
 * platforms return 0 before the caller starts such an inverse. */
int sumi_explorer_platform_recovery_filesystem(const char *path, char **error);

#ifdef __cplusplus
}
#endif
#endif
