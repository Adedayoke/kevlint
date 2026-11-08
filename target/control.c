/* control: end of the IPC pipeline. Receives samples from fusion. */
#include "kevdemo.h"

#define CONTROL_WORK 500000ul

int main(int argc, char **argv)
{
    name_attach_t *attach;

    arm_duration(argc, argv);
    setup_self("control_server", PRIO_CONTROL, CPU_PIPELINE);
    attach = name_attach(NULL, CONTROL_NAME, 0);
    if (attach == NULL)
        die("name_attach");
    for (;;) {
        union rx m;
        rcvid_t rcvid = server_receive(attach->chid, &m);

        spin(CONTROL_WORK);
        if (MsgReply(rcvid, EOK, NULL, 0) == -1)
            die("MsgReply");
    }
    return 0;
}
