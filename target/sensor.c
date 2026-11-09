/* sensor: two MsgSend clients of fusion.
 *
 * B wakes just after A, while fusion is still working on A's sample, so B's
 * send finds the server busy. That is the case missing from test_wide.txt.
 */
#include "kevdemo.h"

#define PERIOD_NS 50000000u

struct sender_cfg {
    const char *name;
    int prio;
    uint16_t source;
    uint64_t offset_ns;
};

/* Set once at the deadline. A flag, not a time check in the loop: it adds no kernel
 * call to the trace, and `next` falls behind real time when the pipeline is overloaded.
 */
static volatile int stop;

static void *sender(void *arg)
{
    const struct sender_cfg *c = arg;
    uint32_t seq = 0;
    uint64_t next;
    int coid;

    setup_self(c->name, c->prio, CPU_PIPELINE);
    coid = connect_retry(FUSION_NAME);
    next = (now_ns() / PERIOD_NS + 1) * PERIOD_NS + c->offset_ns;
    while (!stop) {
        struct sample s = { MSG_SAMPLE, c->source, seq++ };

        sleep_until_ns(next);
        if (MsgSend(coid, &s, sizeof s, NULL, 0) == -1)
            die("MsgSend");
        next += PERIOD_NS;
    }
    return NULL;
}

int main(int argc, char **argv)
{
    static const struct sender_cfg a = { "sensor_a", PRIO_SENSOR_A, 1, 0 };
    static const struct sender_cfg b = { "sensor_b", PRIO_SENSOR_B, 2, 2000000u };
    pthread_t ta, tb;

    uint64_t deadline = now_ns() + (uint64_t)arm_backstop(argc, argv) * 1000000000u;

    check(pthread_create(&ta, NULL, sender, (void *)&a), "pthread_create");
    check(pthread_create(&tb, NULL, sender, (void *)&b), "pthread_create");
    /* Above the pipeline, so an overloaded pipeline cannot delay the stop. */
    setup_self("sensor_timer", PRIO_TIMER, CPU_PIPELINE);
    sleep_until_ns(deadline);
    stop = 1;
    pthread_join(ta, NULL);
    pthread_join(tb, NULL);
    return 0;
}
