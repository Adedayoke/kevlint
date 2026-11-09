/* kevlintd_test: exercises a running kevlintd on the target.
 *
 *   kevlintd_test errors    every error code and the status file
 *   kevlintd_test lengths   one marker of each length from 1 to the maximum
 *   kevlintd_test cpu       markers from CPU 0, then from CPU 1
 *   kevlintd_test hold      opens the marker file and sleeps until killed
 *
 * Exits 0 only if every check in the chosen mode passed.
 */
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/neutrino.h>
#include <unistd.h>

#include "kevlintd.h"

static int failures;

static void check_bool(const char *name, int ok, const char *detail)
{
    printf("%s %s%s%s\n", ok ? "ok  " : "FAIL", name, ok ? "" : ": ", ok ? "" : detail);
    if (!ok)
        failures++;
}

static int open_or_die(const char *path, int flags)
{
    int fd = open(path, flags);

    if (fd == -1) {
        perror(path);
        exit(1);
    }
    return fd;
}

/* Expects the write to fail with this errno, or succeed with this count if 0. */
static void expect_write(const char *name, int fd, const void *data, size_t size, int want_errno)
{
    char detail[80];
    ssize_t got;

    errno = 0;
    got = write(fd, data, size);
    snprintf(detail, sizeof detail, "returned %zd, errno %d (%s)", got, errno, strerror(errno));
    if (want_errno == 0)
        check_bool(name, got == (ssize_t)size, detail);
    else
        check_bool(name, got == -1 && errno == want_errno, detail);
}

static void test_write_errors(void)
{
    static char big[1024 * 1024];
    char text[KEVLINT_MARKER_MAX + 2];
    int fd = open_or_die(KEVLINT_MARKER_PATH, O_WRONLY);

    memset(text, 'q', sizeof text);
    expect_write("empty write returns 0", fd, "", 0, 0);
    expect_write("only a newline is accepted", fd, "\n", 1, 0);
    expect_write("only spaces are accepted", fd, "   ", 3, 0);
    expect_write("maximum length", fd, text, KEVLINT_MARKER_MAX, 0);
    text[KEVLINT_MARKER_MAX] = '\n';
    expect_write("maximum length plus newline", fd, text, KEVLINT_MARKER_MAX + 1, 0);
    text[KEVLINT_MARKER_MAX] = 'q';
    expect_write("one byte too long", fd, text, KEVLINT_MARKER_MAX + 1, EMSGSIZE);
    expect_write("two bytes too long", fd, text, KEVLINT_MARKER_MAX + 2, EMSGSIZE);
    memset(big, 'q', sizeof big);
    expect_write("1 MiB is rejected", fd, big, sizeof big, EMSGSIZE);
    expect_write("embedded newline", fd, "a\nb", 3, EINVAL);
    expect_write("embedded NUL", fd, "a\0b", 3, EINVAL);
    expect_write("control byte", fd, "a\x01z", 3, EINVAL);
    expect_write("non-ASCII byte", fd, "caf\xc3\xa9", 5, EINVAL);

    errno = 0;
    {
        ssize_t got = pwrite(fd, "offset", 6, 4);
        char detail[80];

        snprintf(detail, sizeof detail, "returned %zd, errno %d (%s)", got, errno, strerror(errno));
        check_bool("write at an offset gives ENOSYS", got == -1 && errno == ENOSYS, detail);
    }
    close(fd);
}

static void test_directions(void)
{
    char byte;
    char detail[80];
    ssize_t got;
    int fd = open_or_die(KEVLINT_MARKER_PATH, O_WRONLY);

    errno = 0;
    got = read(fd, &byte, 1);
    snprintf(detail, sizeof detail, "returned %zd, errno %d (%s)", got, errno, strerror(errno));
    check_bool("reading a marker opened write-only", got == -1 && errno == EBADF, detail);
    close(fd);

    /* These opens succeed or fail depending on root's rights; either way the
     * handlers must refuse the wrong direction. */
    fd = open(KEVLINT_MARKER_PATH, O_RDWR);
    if (fd != -1) {
        errno = 0;
        got = read(fd, &byte, 1);
        snprintf(detail, sizeof detail, "returned %zd, errno %d (%s)", got, errno, strerror(errno));
        check_bool("reading a marker opened read-write", got == -1 && errno == EBADF, detail);
        close(fd);
    } else {
        printf("note opening the marker O_RDWR failed: %s\n", strerror(errno));
    }

    fd = open(KEVLINT_STATUS_PATH, O_RDWR);
    if (fd != -1) {
        errno = 0;
        got = write(fd, "x", 1);
        snprintf(detail, sizeof detail, "returned %zd, errno %d (%s)", got, errno, strerror(errno));
        check_bool("writing status opened read-write", got == -1 && errno == EBADF, detail);
        close(fd);
    } else {
        printf("note opening the status O_RDWR failed: %s\n", strerror(errno));
    }
}

static void test_status_reads(void)
{
    char whole[512], parts[512];
    char detail[80];
    size_t used = 0;
    ssize_t got;
    int fd = open_or_die(KEVLINT_STATUS_PATH, O_RDONLY);

    got = read(fd, whole, sizeof whole - 1);
    check_bool("status has text", got > 0, "empty");
    whole[got > 0 ? got : 0] = '\0';
    fputs(whole, stdout);
    check_bool("status has every field",
               strstr(whole, "version: ") && strstr(whole, "uptime_s: ")
                   && strstr(whole, "markers_written: ") && strstr(whole, "markers_rejected: "),
               whole);
    got = read(fd, parts, 1);
    check_bool("read past the end returns 0", got == 0, "not at end of file");
    close(fd);

    /* One byte at a time must be one consistent snapshot. */
    fd = open_or_die(KEVLINT_STATUS_PATH, O_RDONLY);
    while (used < sizeof parts - 1 && (got = read(fd, parts + used, 1)) == 1)
        used++;
    parts[used] = '\0';
    snprintf(detail, sizeof detail, "got %zu bytes", used);
    check_bool("one-byte reads end at end of file", got == 0, detail);
    check_bool("one-byte reads return every field", strstr(parts, "markers_rejected: ") != NULL, parts);

    check_bool("rewind reads from the start", lseek(fd, 0, SEEK_SET) == 0, strerror(errno));
    got = read(fd, parts, 8);
    check_bool("rewind returns the start of the text", got == 8 && memcmp(parts, "version:", 8) == 0, "wrong text");
    close(fd);
}

static void test_lengths(void)
{
    char text[KEVLINT_MARKER_MAX + 1];
    int fd = open_or_die(KEVLINT_MARKER_PATH, O_WRONLY);
    unsigned length;

    for (length = 1; length <= KEVLINT_MARKER_MAX; length++) {
        memset(text, 'a' + (length % 26), length);
        if (write(fd, text, length) != (ssize_t)length) {
            perror("write");
            failures++;
        }
    }
    close(fd);
    printf("wrote lengths 1 to %u\n", KEVLINT_MARKER_MAX);
}

static void write_from_cpu(int fd, unsigned mask, const char *text)
{
    int i;

    if (ThreadCtl(_NTO_TCTL_RUNMASK, (void *)(uintptr_t)mask) == -1) {
        perror("ThreadCtl");
        exit(1);
    }
    for (i = 0; i < 20; i++) {
        if (write(fd, text, strlen(text)) == -1) {
            perror("write");
            failures++;
        }
        usleep(1000);
    }
}

static void test_cpu(void)
{
    int fd = open_or_die(KEVLINT_MARKER_PATH, O_WRONLY);

    write_from_cpu(fd, 0x1u, "client on cpu0");
    write_from_cpu(fd, 0x2u, "client on cpu1");
    close(fd);
}

int main(int argc, char **argv)
{
    const char *mode = argc > 1 ? argv[1] : "";

    if (strcmp(mode, "errors") == 0) {
        test_write_errors();
        test_directions();
        test_status_reads();
    } else if (strcmp(mode, "lengths") == 0) {
        test_lengths();
    } else if (strcmp(mode, "cpu") == 0) {
        test_cpu();
    } else if (strcmp(mode, "hold") == 0) {
        open_or_die(KEVLINT_MARKER_PATH, O_WRONLY);
        for (;;)
            sleep(60);
    } else {
        fprintf(stderr, "usage: kevlintd_test errors|lengths|cpu|hold\n");
        return 2;
    }
    printf("%d failure(s)\n", failures);
    return failures ? 1 : 0;
}
