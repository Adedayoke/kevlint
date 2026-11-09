/* kevlintd.h: the interface between kevlintd and the programs that use it. */
#ifndef KEVLINTD_H
#define KEVLINTD_H

#define KEVLINT_MARKER_PATH "/dev/kevlint/marker"
#define KEVLINT_STATUS_PATH "/dev/kevlint/status"

/* The user event number that kevlintd uses. The host parser keeps only this one. */
#define KEVLINT_MARKER_EVENT 100

/* The longest marker text, not counting a trailing newline. */
#define KEVLINT_MARKER_MAX 128u

#endif
