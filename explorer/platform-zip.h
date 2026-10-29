#ifndef SUMI_EXPLORER_ZIP_PLATFORM_H
#define SUMI_EXPLORER_ZIP_PLATFORM_H

#include <gio/gio.h>
G_BEGIN_DECLS

typedef struct SumiExplorerZipIO SumiExplorerZipIO;

/* Windows-only synchronous OS I/O for ZIP callers; other platforms return
 * G_IO_ERROR_NOT_SUPPORTED. Unlike the Shell bridge, errors are typed GError
 * allocations, freed with g_error_free(). An existing *error is preserved.
 * Returned GFileInfo objects are owned references, released with g_object_unref.
 * Paths and caller buffers are borrowed; path queries retain no path storage.
 * Source handles deny write/delete sharing. Snapshots are newly created owned
 * files, never adopted by pathname, and are deleted by handle on final close.
 * Cancellation is checked before operations and between bounded I/O chunks;
 * partial writes are possible and must not be replayed by the caller.
 * close consumes the opaque owner on success or failure, is never cancelled,
 * and always attempts cleanup even when *error already contains an error.
 * Preserve a primary operation error by using a separate close-error slot.
 * No function owns a GCancellable. Do not close a borrowed alias twice.
 */
GFileInfo *sumi_explorer_zip_path_query(const char *path, GFileQueryInfoFlags flags,
                                      GCancellable *cancel, GError **error);
SumiExplorerZipIO *sumi_explorer_zip_open_source(const char *path,
                                               GCancellable *cancel, GError **error);
SumiExplorerZipIO *sumi_explorer_zip_new_snapshot(GCancellable *cancel, GError **error);
gssize sumi_explorer_zip_io_read(SumiExplorerZipIO *io, void *buffer, gsize capacity,
                               GCancellable *cancel, GError **error);
gboolean sumi_explorer_zip_io_write_all(SumiExplorerZipIO *io, const void *buffer,
                                      gsize length, GCancellable *cancel, GError **error);
gint64 sumi_explorer_zip_io_seek(SumiExplorerZipIO *io, gint64 offset, GSeekType whence,
                               GCancellable *cancel, GError **error);
GFileInfo *sumi_explorer_zip_io_query(SumiExplorerZipIO *io,
                                    GCancellable *cancel, GError **error);
gboolean sumi_explorer_zip_io_close(SumiExplorerZipIO *io, GError **error);

G_END_DECLS
#endif
