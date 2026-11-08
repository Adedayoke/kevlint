/* kevdemo.h: shared by sensor, fusion and control. */
#ifndef KEVDEMO_H
#define KEVDEMO_H

#include <errno.h>
#include <pthread.h>
#include <sched.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/dispatch.h>
#include <sys/iomsg.h>
#include <sys/neutrino.h>
#include <time.h>
#include <unistd.h>

#define FUSION_NAME  "kevdemo_fusion"
#define CONTROL_NAME "kevdemo_control"

/* The lock scenario and the IPC pipeline run on separate CPUs. */
#define CPU_LOCK     0x1u
#define CPU_PIPELINE 0x2u

#define PRIO_SENSOR_A    10
#define PRIO_SENSOR_B    12
#define PRIO_FUSION      15
#define PRIO_CONTROL     20
#define PRIO_HOLDER      10
#define PRIO_HOG         30
#define PRIO_RT          50

#define MSG_SAMPLE 0x2000u
_Static_assert(MSG_SAMPLE > _IO_MAX, "message types must be outside the IO range");

struct sample {
    uint16_t type;
    uint16_t source;
    uint32_t seq;
};

union rx {
    uint16_t type;
    struct _pulse pulse;
    struct sample sample;
};

static inline void die(const char *what)
{
    perror(what);
    exit(1);
}

static inline void check(int err, const char *what)
{
    if (err != 0) {
        errno = err;
        die(what);
    }
}

/* Named, SCHED_FIFO, pinned: every demo thread calls this first. */
static inline void setup_self(const char *name, int prio, unsigned cpu_mask)
{
    struct sched_param sp;

    memset(&sp, 0, sizeof sp);
    sp.sched_priority = prio;
    check(pthread_setschedparam(pthread_self(), SCHED_FIFO, &sp), "setschedparam");
    if (ThreadCtl(_NTO_TCTL_RUNMASK, (void *)(uintptr_t)cpu_mask) == -1)
        die("ThreadCtl");
    check(pthread_setname_np(pthread_self(), name), "setname");
}

/* Work is a loop count, so no rule can depend on how long it takes. */
static inline void spin(unsigned long loops)
{
    volatile unsigned long i;

    for (i = 0; i < loops; i++) {
    }
}

static inline uint64_t now_ns(void)
{
    struct timespec ts;

    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000u + (uint64_t)ts.tv_nsec;
}

static inline void sleep_until_ns(uint64_t t)
{
    struct timespec ts;

    ts.tv_sec = (time_t)(t / 1000000000u);
    ts.tv_nsec = (long)(t % 1000000000u);
    check(clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &ts, NULL), "clock_nanosleep");
}

static inline void sleep_ms(unsigned ms)
{
    sleep_until_ns(now_ns() + (uint64_t)ms * 1000000u);
}

/* Each process ends itself with alarm(), so captures need no cleanup. */
static inline void arm_duration(int argc, char **argv)
{
    unsigned seconds = 10;
    int i;

    for (i = 1; i + 1 < argc; i++)
        if (strcmp(argv[i], "-d") == 0)
            seconds = (unsigned)strtoul(argv[i + 1], NULL, 10);
    alarm(seconds);
}

static inline int connect_retry(const char *name)
{
    int tries;

    for (tries = 0; tries < 100; tries++) {
        int coid = name_open(name, 0);

        if (coid != -1)
            return coid;
        sleep_ms(50);
    }
    die(name);
    return -1;
}

/* Returns the rcvid of the next sample, handling name_attach housekeeping. */
static inline rcvid_t server_receive(int chid, union rx *m)
{
    for (;;) {
        rcvid_t rcvid = MsgReceive(chid, m, sizeof *m, NULL);

        if (rcvid == -1)
            die("MsgReceive");
        if (rcvid == 0) {
            if (m->pulse.code == _PULSE_CODE_DISCONNECT)
                ConnectDetach(m->pulse.scoid);
            continue;
        }
        if (m->type == _IO_CONNECT) {
            MsgReply(rcvid, EOK, NULL, 0);
            continue;
        }
        if (m->type != MSG_SAMPLE) {
            MsgError(rcvid, ENOSYS);
            continue;
        }
        return rcvid;
    }
}

#endif
