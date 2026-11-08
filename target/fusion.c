/* fusion: middle of the IPC pipeline, plus a separate lock scenario.
 *
 * The lock scenario has three threads on CPU_LOCK, none of which touch IPC
 * messages: holder (10), hog (30) and rt (50).
 *
 *   default      holder releases the lock before waking rt and hog: no contention.
 *   --inversion  holder keeps a semaphore while waking rt and hog.
 *   --fixed      same sequence, with a PTHREAD_PRIO_INHERIT mutex.
 *
 * rt and hog wait on pulses rather than semaphores, so the only semaphore rt
 * ever blocks on is the lock under test.
 */
#include "kevdemo.h"

#include <semaphore.h>
#include <sys/netmgr.h>

#define CYCLE_MS     100u
#define FUSION_WORK  2000000ul
#define HOLD_WORK    2000000ul
#define HOG_WORK     20000000ul
#define RT_WORK      200000ul
#define PULSE_GO     _PULSE_CODE_MINAVAIL

enum mode { MODE_NORMAL, MODE_INVERSION, MODE_FIXED };

struct gate {
    int chid;
    int coid;
    int prio;
};

static enum mode mode = MODE_NORMAL;
static sem_t lock_sem;
static pthread_mutex_t lock_mutex;
static struct gate rt_gate, hog_gate;

static void lock_init(void)
{
    pthread_mutexattr_t attr;

    if (sem_init(&lock_sem, 0, 1) == -1)
        die("sem_init");
    check(pthread_mutexattr_init(&attr), "mutexattr_init");
    check(pthread_mutexattr_setprotocol(&attr, PTHREAD_PRIO_INHERIT), "setprotocol");
    check(pthread_mutex_init(&lock_mutex, &attr), "mutex_init");
}

static void lock_take(void)
{
    if (mode == MODE_FIXED)
        check(pthread_mutex_lock(&lock_mutex), "mutex_lock");
    else if (sem_wait(&lock_sem) == -1)
        die("sem_wait");
}

static void lock_give(void)
{
    if (mode == MODE_FIXED)
        check(pthread_mutex_unlock(&lock_mutex), "mutex_unlock");
    else if (sem_post(&lock_sem) == -1)
        die("sem_post");
}

static void gate_init(struct gate *g, int prio)
{
    g->prio = prio;
    g->chid = ChannelCreate(0);
    if (g->chid == -1)
        die("ChannelCreate");
    g->coid = ConnectAttach(ND_LOCAL_NODE, getpid(), g->chid, _NTO_SIDE_CHANNEL, 0);
    if (g->coid == -1)
        die("ConnectAttach");
}

static void gate_wait(struct gate *g)
{
    struct _pulse p;

    if (MsgReceivePulse(g->chid, &p, sizeof p, NULL) == -1)
        die("MsgReceivePulse");
}

/* Pulse priority matches the receiver, so waking it never changes its priority. */
static void gate_open(struct gate *g)
{
    if (MsgSendPulse(g->coid, g->prio, PULSE_GO, 0) == -1)
        die("MsgSendPulse");
}

static void *rt_thread(void *arg)
{
    (void)arg;
    setup_self("fusion_rt", PRIO_RT, CPU_LOCK);
    for (;;) {
        gate_wait(&rt_gate);
        lock_take();
        spin(RT_WORK);
        lock_give();
    }
    return NULL;
}

static void *hog_thread(void *arg)
{
    (void)arg;
    setup_self("fusion_hog", PRIO_HOG, CPU_LOCK);
    for (;;) {
        gate_wait(&hog_gate);
        spin(HOG_WORK);
    }
    return NULL;
}

static void *holder_thread(void *arg)
{
    (void)arg;
    setup_self("fusion_holder", PRIO_HOLDER, CPU_LOCK);
    for (;;) {
        sleep_ms(CYCLE_MS);
        lock_take();
        if (mode == MODE_NORMAL) {
            spin(HOLD_WORK);
            lock_give();
            gate_open(&rt_gate);
            gate_open(&hog_gate);
        } else {
            gate_open(&rt_gate);
            gate_open(&hog_gate);
            spin(HOLD_WORK);
            lock_give();
        }
    }
    return NULL;
}

static void parse_mode(int argc, char **argv)
{
    int i, inversion = 0, fixed = 0;

    for (i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--inversion") == 0)
            inversion = 1;
        else if (strcmp(argv[i], "--fixed") == 0)
            fixed = 1;
    }
    if (inversion && fixed) {
        fprintf(stderr, "fusion: --inversion and --fixed are exclusive\n");
        exit(2);
    }
    mode = fixed ? MODE_FIXED : inversion ? MODE_INVERSION : MODE_NORMAL;
}

int main(int argc, char **argv)
{
    pthread_t rt, hog, holder;
    name_attach_t *attach;
    int control;

    parse_mode(argc, argv);
    arm_duration(argc, argv);
    lock_init();
    gate_init(&rt_gate, PRIO_RT);
    gate_init(&hog_gate, PRIO_HOG);
    check(pthread_create(&rt, NULL, rt_thread, NULL), "pthread_create");
    check(pthread_create(&hog, NULL, hog_thread, NULL), "pthread_create");
    check(pthread_create(&holder, NULL, holder_thread, NULL), "pthread_create");

    setup_self("fusion_server", PRIO_FUSION, CPU_PIPELINE);
    attach = name_attach(NULL, FUSION_NAME, 0);
    if (attach == NULL)
        die("name_attach");
    control = connect_retry(CONTROL_NAME);
    for (;;) {
        union rx m;
        rcvid_t rcvid = server_receive(attach->chid, &m);

        spin(FUSION_WORK);
        if (MsgSend(control, &m.sample, sizeof m.sample, NULL, 0) == -1)
            die("MsgSend");
        if (MsgReply(rcvid, EOK, NULL, 0) == -1)
            die("MsgReply");
    }
    return 0;
}
