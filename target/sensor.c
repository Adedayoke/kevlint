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

static void *sender(void *arg)
{
    const struct sender_cfg *c = arg;
    uint32_t seq = 0;
    uint64_t next;
    int coid;

    setup_self(c->name, c->prio, CPU_PIPELINE);
    coid = connect_retry(FUSION_NAME);
    next = (now_ns() / PERIOD_NS + 1) * PERIOD_NS + c->offset_ns;
    for (;;) {
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

    arm_duration(argc, argv);
    check(pthread_create(&ta, NULL, sender, (void *)&a), "pthread_create");
    check(pthread_create(&tb, NULL, sender, (void *)&b), "pthread_create");
    pthread_join(ta, NULL);
    return 0;
}
