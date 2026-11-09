/* kevlintd: a QNX resource manager that turns writes into kernel trace markers.
 *
 *   /dev/kevlint/marker  each write() becomes one user string event
 *   /dev/kevlint/status  read for plain text: version, uptime and counters
 *
 * The channel is left at its default of priority inheritance on purpose. With
 * _NTO_CHF_FIXED_PRIORITY a marker from a high-priority thread could wait behind
 * lower-priority work, which would be a priority inversion caused by this tool.
 *
 * One thread serves every client, so markers reach the trace in a strict order.
 */
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <time.h>
#include <unistd.h>

struct kl_ocb;
#define IOFUNC_OCB_T struct kl_ocb
#include <sys/iofunc.h>
#include <sys/dispatch.h>
#include <sys/neutrino.h>
#include <sys/procmgr.h>
#include <sys/trace.h>

#include "kevlintd.h"

#define VERSION    "0.1.0"
#define STATUS_MAX 256u

/* "pid:<10 digits> tid:<10 digits> " is at most 30 bytes. */
#define PREFIX_MAX 30u
/* One byte of padding and the terminator. kevlintd's strings stay below the
 * 255 bytes that a user string event keeps (docs/qnx-trace-quirks.md). */
#define EVENT_MAX  (KEVLINT_MARKER_MAX + PREFIX_MAX + 2u)

/* Each open file has its own status text, so small reads stay consistent. */
struct kl_ocb {
    iofunc_ocb_t base;
    size_t text_len;
    char text[STATUS_MAX];
};

static uint64_t markers_written;
static uint64_t markers_rejected;
static unsigned open_files;
static uint64_t started_ns;

static uint64_t monotonic_ns(void)
{
    struct timespec ts;

    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000u + (uint64_t)ts.tv_nsec;
}

static IOFUNC_OCB_T *ocb_calloc(resmgr_context_t *ctp, IOFUNC_ATTR_T *attr)
{
    struct kl_ocb *ocb = calloc(1, sizeof *ocb);

    (void)ctp;
    (void)attr;
    if (ocb != NULL)
        open_files++;
    return ocb;
}

static void ocb_free(IOFUNC_OCB_T *ocb)
{
    open_files--;
    free(ocb);
}

/* Removes one trailing newline, so that `echo text > marker` works, then
 * trailing spaces, so that the padding added below is unambiguous. */
static size_t trimmed_length(const char *text, size_t length)
{
    if (length > 0 && text[length - 1] == '\n')
        length--;
    while (length > 0 && text[length - 1] == ' ')
        length--;
    return length;
}

static int printable(const char *text, size_t length)
{
    size_t i;

    for (i = 0; i < length; i++)
        if (text[i] < 0x20 || text[i] > 0x7e)
            return 0;
    return 1;
}

/* A user string event whose length is a multiple of 8 decodes without its
 * text, so one space is added to avoid those lengths. The host removes it. */
static int emit_marker(const resmgr_context_t *ctp, const char *text, size_t length)
{
    char event[EVENT_MAX];
    int used = snprintf(event, sizeof event, "pid:%d tid:%d %.*s", (int)ctp->info.pid,
                        (int)ctp->info.tid, (int)length, text);

    if (used % 8 == 0) {
        event[used++] = ' ';
        event[used] = '\0';
    }
    if (TraceEvent(_NTO_TRACE_INSERTUSRSTREVENT, KEVLINT_MARKER_EVENT, event) == -1)
        return errno;
    return EOK;
}

static int marker_write(resmgr_context_t *ctp, io_write_t *msg, RESMGR_OCB_T *ocb)
{
    char text[KEVLINT_MARKER_MAX + 1u];
    size_t nbytes, length;
    int status = iofunc_write_verify(ctp, msg, &ocb->base, NULL);

    if (status != EOK)
        return status;
    if ((msg->i.xtype & _IO_XTYPE_MASK) != _IO_XTYPE_NONE)
        return ENOSYS;

    nbytes = _IO_WRITE_GET_NBYTES(msg);
    /* Rejected whole: a short write would make callers send the rest as a
     * second marker. The extra byte allows for a trailing newline. */
    if (nbytes > sizeof text) {
        markers_rejected++;
        return EMSGSIZE;
    }
    if (nbytes > 0) {
        if (resmgr_msgread(ctp, text, nbytes, sizeof msg->i) != (ssize_t)nbytes)
            return EIO;
        length = trimmed_length(text, nbytes);
        if (length > KEVLINT_MARKER_MAX) {
            markers_rejected++;
            return EMSGSIZE;
        }
        if (!printable(text, length)) {
            markers_rejected++;
            return EINVAL;
        }
        if (length > 0) {
            status = emit_marker(ctp, text, length);
            if (status != EOK) {
                markers_rejected++;
                return status;
            }
            markers_written++;
        }
        ocb->base.attr->flags |= IOFUNC_ATTR_MTIME | IOFUNC_ATTR_CTIME | IOFUNC_ATTR_DIRTY_TIME;
    }
    _IO_SET_WRITE_NBYTES(ctp, nbytes);
    return _RESMGR_NPARTS(0);
}

static void take_status_snapshot(struct kl_ocb *ocb)
{
    int used = snprintf(ocb->text, sizeof ocb->text,
                        "version: %s\n"
                        "uptime_s: %llu\n"
                        "markers_written: %llu\n"
                        "markers_rejected: %llu\n"
                        "open_files: %u\n",
                        VERSION, (unsigned long long)((monotonic_ns() - started_ns) / 1000000000u),
                        (unsigned long long)markers_written, (unsigned long long)markers_rejected,
                        open_files);

    ocb->text_len = (size_t)used;
}

static int status_read(resmgr_context_t *ctp, io_read_t *msg, RESMGR_OCB_T *ocb)
{
    size_t wanted, left, count;
    int status = iofunc_read_verify(ctp, msg, &ocb->base, NULL);

    if (status != EOK)
        return status;
    if ((msg->i.xtype & _IO_XTYPE_MASK) != _IO_XTYPE_NONE)
        return ENOSYS;

    /* Offset 0 means a new read or a rewind, so the counters are refreshed. */
    if (ocb->base.offset == 0)
        take_status_snapshot(ocb);
    wanted = _IO_READ_GET_NBYTES(msg);
    left = (size_t)ocb->base.offset < ocb->text_len ? ocb->text_len - (size_t)ocb->base.offset : 0;
    count = wanted < left ? wanted : left;

    _IO_SET_READ_NBYTES(ctp, count);
    if (count == 0)
        return _RESMGR_NPARTS(0);
    SETIOV(ctp->iov, ocb->text + ocb->base.offset, count);
    ocb->base.offset += count;
    ocb->base.attr->flags |= IOFUNC_ATTR_ATIME | IOFUNC_ATTR_DIRTY_TIME;
    return _RESMGR_NPARTS(1);
}

/* Root can open either file in either direction, so the direction is enforced
 * here as well as by the file modes. */
static int wrong_direction_read(resmgr_context_t *ctp, io_read_t *msg, RESMGR_OCB_T *ocb)
{
    (void)ctp;
    (void)msg;
    (void)ocb;
    return EBADF;
}

static int wrong_direction_write(resmgr_context_t *ctp, io_write_t *msg, RESMGR_OCB_T *ocb)
{
    (void)ctp;
    (void)msg;
    (void)ocb;
    return EBADF;
}

static void die(const char *what)
{
    perror(what);
    exit(1);
}

static void usage(void)
{
    fprintf(stderr, "usage: kevlintd [-f] [-r runmask]\n"
                    "  -f  stay in the foreground\n"
                    "  -r  pin kevlintd to these CPUs instead of following each client's\n");
    exit(2);
}

/* _NTO_CHF_INHERIT_RUNMASK makes kevlintd run on its client's CPUs, so a marker
 * does not let other work slip in on the client's CPU while it waits. */
static dispatch_t *create_dispatch(int pinned)
{
    dispatch_t *dpp;

    if (pinned) {
        dpp = dispatch_create();
    } else {
        int chid = ChannelCreate(_NTO_CHF_UNBLOCK | _NTO_CHF_DISCONNECT | _NTO_CHF_COID_DISCONNECT
                                 | _NTO_CHF_INHERIT_RUNMASK);

        if (chid == -1)
            die("ChannelCreate");
        dpp = dispatch_create_channel(chid, 0);
    }
    if (dpp == NULL)
        die("dispatch_create");
    return dpp;
}

static void attach_file(dispatch_t *dpp, const char *path, iofunc_attr_t *attr, mode_t mode,
                        iofunc_mount_t *mount, const resmgr_connect_funcs_t *connect,
                        const resmgr_io_funcs_t *io)
{
    resmgr_attr_t resmgr_attr;

    memset(&resmgr_attr, 0, sizeof resmgr_attr);
    resmgr_attr.nparts_max = 1;
    resmgr_attr.msg_max_size = 2048;

    iofunc_attr_init(attr, S_IFREG | mode, NULL, NULL);
    attr->mount = mount;
    if (resmgr_attach(dpp, &resmgr_attr, path, _FTYPE_ANY, 0, connect, io, attr) == -1)
        die(path);
}

int main(int argc, char **argv)
{
    static iofunc_funcs_t ocb_funcs = { _IOFUNC_NFUNCS, ocb_calloc, ocb_free };
    static iofunc_mount_t mount = { .funcs = &ocb_funcs };
    static iofunc_attr_t marker_attr, status_attr;
    static resmgr_connect_funcs_t connect_funcs;
    static resmgr_io_funcs_t marker_io, status_io;
    dispatch_t *dpp;
    dispatch_context_t *ctp;
    unsigned long runmask = 0;
    int foreground = 0;
    int option;

    while ((option = getopt(argc, argv, "fr:")) != -1) {
        if (option == 'f')
            foreground = 1;
        else if (option == 'r')
            runmask = strtoul(optarg, NULL, 0);
        else
            usage();
    }
    if (access(KEVLINT_MARKER_PATH, F_OK) == 0) {
        fprintf(stderr, "kevlintd: %s already exists; is kevlintd running?\n", KEVLINT_MARKER_PATH);
        return 1;
    }
    if (runmask != 0 && ThreadCtl(_NTO_TCTL_RUNMASK, (void *)(uintptr_t)runmask) == -1)
        die("ThreadCtl");

    started_ns = monotonic_ns();
    dpp = create_dispatch(runmask != 0);

    iofunc_func_init(_RESMGR_CONNECT_NFUNCS, &connect_funcs, _RESMGR_IO_NFUNCS, &marker_io);
    iofunc_func_init(_RESMGR_CONNECT_NFUNCS, &connect_funcs, _RESMGR_IO_NFUNCS, &status_io);
    marker_io.write = marker_write;
    marker_io.read = wrong_direction_read;
    status_io.read = status_read;
    status_io.write = wrong_direction_write;

    attach_file(dpp, KEVLINT_MARKER_PATH, &marker_attr, 0222, &mount, &connect_funcs, &marker_io);
    attach_file(dpp, KEVLINT_STATUS_PATH, &status_attr, 0444, &mount, &connect_funcs, &status_io);

    ctp = dispatch_context_alloc(dpp);
    if (ctp == NULL)
        die("dispatch_context_alloc");

    /* Daemonize only now, so the paths exist once the starting shell continues. */
    if (!foreground && procmgr_daemon(EXIT_SUCCESS, 0) == -1)
        die("procmgr_daemon");

    for (;;) {
        if (dispatch_block(ctp) == NULL) {
            if (errno == EINTR)
                continue;
            die("dispatch_block");
        }
        dispatch_handler(ctp);
    }
}
