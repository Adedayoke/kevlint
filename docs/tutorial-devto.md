---
title: Run QNX 8.0 on Windows with WSL2 and capture your first kernel trace
published: false
tags: qnx, wsl, embedded, tutorial
canonical_url: https://github.com/Adedayoke/kevlint/blob/main/docs/tutorial-qnx-wsl.md
---

*Written for the QNX Global Hackathon. The original lives [on GitHub](https://github.com/Adedayoke/kevlint/blob/main/docs/tutorial-qnx-wsl.md).*

By the end of this tutorial you will have QNX 8.0 running in a virtual machine on your Windows laptop, and you will have recorded and read a real kernel trace: every thread switch, with priorities and timestamps.

| | |
|---|---|
| **You need** | A Windows laptop. No QNX experience and no extra hardware. |
| **Disk space** | About 7 GB free in WSL. The installer lists SDP 8.0 at about 6.4 GB; it took 3.5 GB on disk in our install. |
| **Downloads** | For metered connections: the Ubuntu 24.04 WSL image is about 360 MB, the QNX Software Center installer about 100 MB, `qemu-system-x86` about 49 MB, and SDP 8.0 several GB (6.4 GB installed, per the installer). Skip the 20 GB Quick Start package: step 4 builds the image locally instead. |
| **Memory** | The virtual machine uses 1 GB. This setup runs on a laptop with 8 GB of RAM. |
| **Total time** | About 90 minutes, most of it in steps 1 to 3. Step times are estimates. |
| **Cost** | Free: the QNX license is for personal, non-commercial use. |

Here is what you are building. The numbers on the right are the steps that set up each layer.

```text
Windows laptop
└── WSL2: Ubuntu 24.04                     steps 2, 3
    ├── QNX SDP 8.0 in ~/qnx800            step 3 (qcc, mkqnximage, traceprinter)
    └── QEMU                               steps 4, 5
        └── QNX 8.0 guest                  step 6 (SSH on localhost:2222)
            └── tracelogger                step 7 (writes the kernel trace)
```

### How this tutorial was checked

- **Steps 4 to 7 were replayed on 2026-11-09** on freshly built images, with these commands. The only differences were the folder and the SSH port (2223 instead of 2222). Each step is marked **Tested on a fresh image**.
- **Steps 1 to 3 come from the original setup** and were not replayed, because they happen on qnx.com and in graphical installers. Each is marked **Not replayed**. Screens and button names may differ from what you see.
- Anything else we could not confirm is marked **Unverified** where it appears.
- This combination is not an officially documented one. QNX's guide says [the QSTI for QEMU runs on Ubuntu 22.04 or 24.04](https://www.qnx.com/developers/docs/qnxeverywhere/com.qnx.doc.target_images/topic/qsti_qemu/about.html), and that page does not mention WSL.

---

## Step 1: Get a free QNX license

⏱ About 10 minutes · **Not replayed**

1. In your Windows browser, go to [qnx.com/getqnx](https://www.qnx.com/getqnx) and request the free non-commercial license (QNX Everywhere). You need a myQNX account.
2. In myQNX, **accept** the license and **deploy** it to your account.

**You should see:** the license listed in myQNX as deployed. The installer in step 3 needs it.

> **Troubleshooting**
> - **The license doesn't show up in the installer in step 3.** Come back here and check that you both accepted and deployed it. Accepting alone is not the last step.

---

## Step 2: Install WSL2 with Ubuntu 24.04

⏱ About 15 minutes · **Not replayed**

In Windows PowerShell:

```powershell
wsl --install -d Ubuntu-24.04
```

Use 24.04 specifically. QNX documents 22.04 and 24.04, and newer Ubuntu releases are untested here. Windows may ask you to reboot and to create a Linux username and password (**Unverified**: this depends on your Windows setup).

Open the Ubuntu terminal and bring it up to date:

```bash
sudo apt update && sudo apt upgrade -y
cd ~
pwd
grep VERSION_ID /etc/os-release
echo $DISPLAY
```

**You should see:**

```text
/home/<your-linux-name>
VERSION_ID="24.04"
:0
```

The `:0` means WSLg is available, so Linux graphical apps can open on your Windows desktop.

**Work in your Linux home folder for everything that follows, not under `/mnt/c`.** `/mnt/c` is your Windows drive, and nothing in this tutorial has been tested there.

> **Troubleshooting**
> - **`VERSION_ID` isn't 24.04.** You may have another distribution as the default. Run `wsl --list --verbose` in PowerShell and open the Ubuntu-24.04 one.
> - **`$DISPLAY` is empty.** Step 3's installer needs a display, so sort this out before going on. WSLg comes with current WSL versions; `wsl --update` in PowerShell is the first thing to try (**Unverified**: we did not hit this).

---

## Step 3: Install QNX SDP 8.0

⏱ About 30 minutes, mostly download · **Not replayed**

1. In your Windows browser, download the **QNX Software Center** installer for **Linux Hosts**. Choose Linux even though you're on Windows, because it will run inside WSL.
2. Copy it into WSL and run it as your normal user, with no `sudo`. Replace `<you>` with your Windows user name. Our file was `qnx-setup-2.0.4-202501021438-linux.run` (about 104 MB); yours may have a newer version.

   ```bash
   cp /mnt/c/Users/<you>/Downloads/qnx-setup-*-linux.run ~/
   chmod +x ~/qnx-setup-*-linux.run
   ~/qnx-setup-*-linux.run
   ```

3. The Software Center went into `~/qnx/qnxsoftwarecenter` on our machine. Its window opens on the Windows desktop through WSLg. If it isn't already open, start it:

   ```bash
   ~/qnx/qnxsoftwarecenter/qnxsoftwarecenter
   ```

4. Sign in, then install **QNX SDP 8.0** (ours was Build 141, shown with an Installed Size of about 6.4 GB) into the folder `~/qnx800`. Type the full path, `/home/<your-linux-name>/qnx800`.

> **Skip the Quick Start QEMU image package.** The installer listed it at `Installed Size: 20.0 GB`. The image you build in step 4 is about 170 MB.

Check the install. **You will run this `source` line in every new terminal that uses the QNX tools:**

```bash
source ~/qnx800/qnxsdp-env.sh
which qcc mkqnximage traceprinter
```

**You should see** three paths:

```text
/home/<your-linux-name>/qnx800/host/linux/x86_64/usr/bin/qcc
/home/<your-linux-name>/qnx800/host/common/bin/mkqnximage
/home/<your-linux-name>/qnx800/host/linux/x86_64/usr/bin/traceprinter
```

> **Troubleshooting**
> - **`Script is being run, should be sourced`.** You ran `./qnxsdp-env.sh`. Use `source` as shown, so it can change your current shell.
> - **`which` prints nothing.** The `source` line didn't run in this terminal, or SDP went into a different folder. Check with `ls ~/qnx800`.
> - **The installer window doesn't open.** Test WSLg by itself: `sudo apt install -y x11-apps`, then `xeyes`. If that doesn't open a window either, the problem is WSLg, not QNX.

---

## Step 4: Build a QNX image

⏱ About 5 minutes · **Tested on a fresh image**

Install QEMU and create an SSH key. Skip the `ssh-keygen` line if you already have `~/.ssh/id_ed25519`: running it again would ask to overwrite your key.

```bash
sudo apt install -y qemu-system-x86 bridge-utils
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519
```

(`bridge-utils` is only used by the network check in `mkqnximage --run`, which step 5 replaces. It was installed on the machine we tested on.)

Build the image in a new, empty folder. **The folder must be `~/qnxvm`**, because the launch script in step 5 expects it.

```bash
source ~/qnx800/qnxsdp-env.sh
mkdir ~/qnxvm
cd ~/qnxvm
mkqnximage --type=qemu --arch=x86_64 --ssh-ident=$HOME/.ssh/id_ed25519.pub --build
```

Note the `$HOME`. The shell doesn't expand `~` after an `=` in the middle of an argument, so `--ssh-ident=~/.ssh/...` would pass a literal `~` to `mkqnximage`.

The stock image can't accept SSH logins yet, because it doesn't include `sshd-auth`, a helper program that this `sshd` refuses to start without. Add it to the image, along with the links `sshd` looks for in `/usr/libexec`, then rebuild:

```bash
cat >> local/snippets/ifs_env.custom <<'EOF'
procmgr_symlink /system/bin/sshd-session /usr/libexec/sshd-session
procmgr_symlink /system/bin/sshd-auth /usr/libexec/sshd-auth
EOF
cat >> local/snippets/system_files.custom <<'EOF'
bin/sshd-auth=usr/libexec/sshd-auth
EOF
mkqnximage --build
```

Your options are remembered in `local/options`, so the second build doesn't need them again. Each build took about 7 seconds for us.

We tested this both ways. Without these lines, `sshd` stops immediately with `/usr/libexec/sshd-auth does not exist or is not executable`, and with them everything in steps 6 and 7 works. QNX's 8.0.2 networking release notes describe the same snippet files for adding `sshd-session`.

Check the result:

```bash
ls output/ifs.bin output/disk-qemu.vmdk
grep sshd-auth output/build/system.build output/build/ifs.build
```

**You should see:**

```text
output/disk-qemu.vmdk  output/ifs.bin
output/build/system.build:bin/sshd-auth=usr/libexec/sshd-auth
output/build/ifs.build:procmgr_symlink /system/bin/sshd-auth /usr/libexec/sshd-auth
```

> **Troubleshooting**
> - **`mkqnximage: command not found`.** Run the `source` line from step 3 in this terminal.
> - **It refuses to build in a non-empty directory.** `mkqnximage` won't create a new image in a folder that already has files. Use a new empty folder.
> - **You used `~` by mistake in `--ssh-ident`.** Run the full command again with `$HOME`.
> - **The `grep` prints nothing.** You appended the snippets but didn't rebuild, or you were in a different folder. Run `cd ~/qnxvm`, then `mkqnximage --build`.
> - **A warning for later:** `--build` rebuilds every partition, including `/data`. Rebuilding after step 6 erases the files you create there, so you'd repeat step 6.

---

## Step 5: Start QNX with networking that works under WSL

⏱ About 5 minutes · **Tested on a fresh image**

The obvious command is `mkqnximage --run`, but it uses bridge networking. Under WSL the guest only got a link-local address: `ifconfig` showed `vtnet0` at `169.254.237.236`, which you can't reach for SSH.

The fix is a small launch script. It starts QEMU the way `mkqnximage --run` does on a machine without KVM, with one change: QEMU's user-mode networking, which forwards port 2222 on Linux to the guest's SSH port.

```bash
cat > ~/qnxvm/run.sh <<'EOF'
#!/bin/bash
cd ~/qnxvm
qemu-system-x86_64 -smp 2 --cpu max -m 1G \
  -drive file=output/disk-qemu.vmdk,if=ide,id=drv0 \
  -netdev user,id=net0,hostfwd=tcp::2222-:22 \
  -device virtio-net-pci,netdev=net0,mac=52:54:00:ff:98:86 \
  -pidfile output/qemu.pid -nographic -kernel output/ifs.bin -serial mon:stdio \
  -object rng-random,filename=/dev/urandom,id=rng0 -device virtio-rng-pci,rng=rng0
EOF
chmod +x ~/qnxvm/run.sh
~/qnxvm/run.sh
```

Two things about this script:

- `--cpu max` with no KVM means QEMU emulates the CPU in software. It's enough for this tutorial, but **don't treat timings as real-time measurements**.
- The MAC address is a fixed value. It worked on images whose generated MAC was different, so you don't need to change it.

This terminal is now the **QNX console**, a root shell with no login. Boot took under 20 seconds for us.

**You should see** boot messages ending with a `#` prompt, including these two lines:

```text
---> Starting sshd
Memory fault (core dumped)
```

**That memory fault is expected.** The stock `sshd` crashes at every boot, and step 6 deals with it.

At the `#` prompt, type:

```sh
ifconfig vtnet0
```

**You should see** a line containing:

```text
inet 10.0.2.15 netmask 0xffffff00 broadcast 10.0.2.255
```

Leave this terminal open, and open a second Ubuntu terminal for the next steps. To shut the virtual machine down later, press **Ctrl-A, then X** in the console.

> **Troubleshooting**
> - **`Could not set up host forwarding rule 'tcp::2222-:22'`.** Something already holds port 2222, usually another copy of QEMU. Find it with `pgrep -af qemu-system`.
> - **`vtnet0` shows `169.254.x.x`.** You started the image with `mkqnximage --run`. Stop it and use `~/qnxvm/run.sh`.
> - **`vtnet0` has no `inet` line yet.** Wait a few seconds and run `ifconfig vtnet0` again.
> - **`qemu-system-x86_64: command not found`.** The `apt install` in step 4 didn't complete.

---

## Step 6: Get SSH working

⏱ About 10 minutes · **Tested on a fresh image**

From your **second** terminal, see the problem for yourself:

```bash
ssh -p 2222 root@localhost
```

**You should see:** `Connection reset by 127.0.0.1 port 2222`. QEMU accepts the connection, but no working `sshd` is behind it. The stock configuration, `/system/etc/ssh/sshd_config`, has `UsePAM yes`, and turning PAM off is what stops the crash. You will run your own `sshd` with PAM off.

Paste this whole block into the **QNX console**:

```sh
cat > /data/home/root/sshd_nopam.conf <<'EOF'
HostKey /data/var/ssh/ssh_host_ed25519_key
UsePAM no
PasswordAuthentication no
PidFile none
AuthorizedKeysFile .ssh/authorized_keys
Subsystem sftp /system/bin/sftp-server
SshdSessionPath /system/bin/sshd-session
PermitUserEnvironment yes
EOF
cat > /data/home/root/.ssh/environment <<'EOF'
PATH=/proc/boot:/system/bin
EOF
chmod 600 /data/home/root/.ssh/environment
cat > /data/home/root/start_ssh.sh <<'EOF'
/system/bin/sshd -D -e -f /data/home/root/sshd_nopam.conf > /tmp/sshd.log 2>&1 &
EOF
sh /data/home/root/start_ssh.sh
```

What the less obvious pieces do:

| Piece | Why it's there |
|---|---|
| `UsePAM no` | Avoids the PAM crash. |
| `PasswordAuthentication no` | Log in only with the key you built into the image in step 4. |
| `Subsystem sftp` | The `scp` on Ubuntu 24.04 needs it to copy files. |
| `SshdSessionPath` | Where this image keeps `sshd-session`. |
| `PermitUserEnvironment yes` and `.ssh/environment` | Remote commands get `PATH=/proc/boot:/system/bin`, so they can find `tracelogger`. |
| `sshd -D -e` | Keeps `sshd` in the foreground and sends its log to stderr, which goes to `/tmp/sshd.log`. The `&` runs it in the background. |

From your **second** terminal, wait a few seconds and test it. Answer `yes` to the first-connection host key question.

```bash
ssh -p 2222 root@localhost uname -a
ssh -p 2222 root@localhost 'echo $PATH; which tracelogger'
```

**You should see:**

```text
QNX noname 8.0.0 2026/06/05-16:26:03EDT x86pc x86_64
/proc/boot:/system/bin
/system/bin/tracelogger
```

The `sshd` you just started doesn't survive a reboot. After every boot, run `sh /data/home/root/start_ssh.sh` on the console.

> **Troubleshooting**
> - **The paste comes out garbled, with `^M` characters or lines run together.** This happened on a first paste and worked on the second. Press Ctrl-C, then paste again one `cat` block at a time, from the `cat` line to its `EOF`, followed by the `chmod` and `sh` lines. Pasting a block again overwrites the file it writes. Check the result with `cat /data/home/root/sshd_nopam.conf`.
> - **`Connection reset by 127.0.0.1 port 2222`.** Your `sshd` isn't running. On the console, run `cat /tmp/sshd.log` to see why, then `sh /data/home/root/start_ssh.sh`.
> - **The log says `/usr/libexec/sshd-auth does not exist or is not executable`.** The snippets from step 4 aren't in your image. Add them, rebuild, boot, and repeat this step.
> - **`Connection refused`.** QEMU itself isn't running. Start it with `~/qnxvm/run.sh`.
> - **`REMOTE HOST IDENTIFICATION HAS CHANGED`.** You probably built a new image in a new folder, which creates new host keys. Run `ssh-keygen -R '[localhost]:2222'` and connect again.
> - **`tracelogger: not found` over SSH.** The `.ssh/environment` file is missing or `PermitUserEnvironment yes` didn't make it into the config. Look at both on the console.

---

## Step 7: Capture and read your first kernel trace

⏱ About 10 minutes · **Tested on a fresh image**

The kernel in this image is the instrumented one, so it can log its own events. `tracelogger` saves them to a file. From your **second** terminal:

```bash
ssh -p 2222 root@localhost 'tracelogger -w -s 3 -f /tmp/test.kev'
```

This runs for about 3 seconds (3.4 to 4.8 s in our runs) and **prints nothing when it succeeds**.

- `-s 3` records for 3 seconds.
- `-f` names the output file.
- `-w` selects wide mode. **Always use it: only wide mode includes each thread's priority and policy.** Here's the same kind of event from two real traces:

  ```text
  without -w   t:0x486e342fc13 CPU:00 THREAD :THRUNNING  pid:1 tid:3
  with -w      t:0x4df2976264c CPU:00 THREAD :THRUNNING  pid:1 tid:3 priority:255 policy:8
  ```

Copy the file to Linux and decode it:

```bash
source ~/qnx800/qnxsdp-env.sh
mkdir -p ~/traces
cd ~/traces
scp -P 2222 root@localhost:/tmp/test.kev .
traceprinter -f test.kev > test.txt
ls -la test.kev
wc -l test.txt
```

**You should see:** a `.kev` file of a few hundred KB (ours was 333,741 bytes on an idle system) and about 20,000 lines of text. Your exact numbers will differ.

### Read the header

The top of `test.txt` describes the capture. Two lines matter most:

```text
  TRACE_CYCLES_PER_SEC:: 2648529944
TRACE_TRACELOGGER_ARGS:: tracelogger -w -s 3 -f /tmp/test.kev
```

Timestamps are in CPU cycles, and the rate changed between boots for us, so read yours from your own file. Check for `-w` in the second line.

### Read an event

Here are three real lines for one thread, with the lines between them removed:

```text
t:0x128a4c0787 CPU:01 THREAD          :THSEM              pid:725013 tid:4 priority:10 policy:2
t:0x128a920384 CPU:00 THREAD          :THREADY            pid:725013 tid:4 priority:10 policy:2
t:0x128acf8778 CPU:01 THREAD          :THRUNNING          pid:725013 tid:4 priority:10 policy:2
```

| Field | Meaning |
|---|---|
| `t:0x...` | The timestamp, in cycles (hexadecimal). |
| `CPU:01` | The CPU that logged the event. |
| `THREAD :THSEM` | The event class, then the event: this thread's new state. |
| `pid`, `tid` | Which process and thread. |
| `priority:10` | The thread's priority at that moment. Only wide mode has this. |
| `policy:2` | The scheduling policy. In `sched.h`, 1 is `SCHED_FIFO` and 2 is `SCHED_RR`. |

The state tells you what the thread is doing:

| State | Meaning |
|---|---|
| `THRUNNING` | Running on a CPU right now. |
| `THREADY` | Able to run, but waiting for a CPU. |
| `THSEM` | Blocked on a semaphore. |
| `THRECEIVE` | Waiting to receive a message: normal for an idle server. |

So the three lines tell a short story. The thread blocked on a semaphore, became ready, then got a CPU.

### Turn timestamps into time

Subtract two timestamps and divide by `TRACE_CYCLES_PER_SEC`:

```bash
python3 -c "print((0x128a920384 - 0x128a4c0787) / 2648529944 * 1000, 'ms')"
```

**You should see** about `1.73 ms`: how long the thread above stayed blocked. It then waited about 1.52 ms in `THREADY` for a CPU. Remember that QEMU emulates the CPU, so use these numbers for practice, not as measurements.

### Explore

Count the states in your trace:

```bash
grep -oE 'THREAD +:TH[A-Z_]+' test.txt | sed 's/.*://' | sort | uniq -c | sort -rn | head -4
```

**You should see** something like:

```text
   1605 THRUNNING
   1448 THREADY
    523 THINTR
    308 THRECEIVE
```

Find out who a `pid` is, using a `pid` from your own trace:

```bash
grep -A2 -E '^ +pid:725013$' test.txt | grep name
```

Names come from multi-line `PROCCREATE_NAME` and `PROCTHREAD_NAME` events. That `pid` was `system/bin/tracelogger`: the trace recorded the tool that took it.

Don't be surprised by the start of the file. Tracing begins with a dump of the current state of every thread, so you'll see many `THCREATE` lines and states that repeat ones logged moments earlier.

> **Troubleshooting**
> - **No `priority:` on `THREAD` lines.** You left out `-w`. Capture again.
> - **`traceprinter: command not found`.** Run the `source` line in this terminal.
> - **The trace is much shorter than 3 seconds.** `/tmp` may be full: `tracelogger` can exit with status 0 and print nothing while the file stops where the disk did. Check with `ssh -p 2222 root@localhost df -h /tmp`. `/tmp` lives on the small `/data` partition (about 50 MB free on a fresh image), and a busy system can write about 2 MB/s. Delete old traces with `rm /tmp/*.kev`.
> - **`scp: Connection closed`.** `sshd` isn't running, or the `Subsystem sftp` line is missing. See step 6.

---

## Step 8 (optional): Check the trace with kevlint

⏱ About 5 minutes · **Tested on a fresh trace**

[kevlint](https://github.com/Adedayoke/kevlint) checks traces against timing rules, such as "no priority inversion", and needs only Python 3. Keep using the same Ubuntu terminal.

```bash
cd ~
git clone https://github.com/Adedayoke/kevlint.git
cd kevlint
PYTHONPATH=host python3 -m kevlint check ~/traces/test.txt --html ~/traces/first.html
```

**You should see** something like this, with different counts:

```text
rule: priority-inversion

0 violation(s)
blocks: 61 total, 3 evaluated, 58 not evaluated
  not evaluated: 1 (block still open when the trace ended)
  not evaluated: 1 (blocked in THMUON_MUTEX, which this rule does not check)
  not evaluated: 56 (no matching wait call in the trace)
PASS: 0 violation(s) in 3 evaluated block(s); 58 block(s) not evaluated
```

**Read the second half of that PASS line.** On an idle system kevlint could only evaluate 3 blocks of 61, so this PASS says very little. To see what a real finding looks like, run it on the trace that ships with the repository:

```bash
PYTHONPATH=host python3 -m kevlint check fixtures/demo_inversion.txt --html ~/traces/inversion.html
```

**You should see** a last result line starting with `FAIL: 10 violation(s)`.

Open the HTML report in your Windows browser in either of two ways:

- From Ubuntu, run `explorer.exe ~/traces/inversion.html`.
- Paste `\\wsl.localhost\Ubuntu-24.04\home\<your-linux-name>\traces\inversion.html` into the browser's address bar.

> **Troubleshooting**
> - **`No module named kevlint`.** Run the command from inside the `kevlint` folder, with `PYTHONPATH=host` at the front.
> - **It exits with status 1.** That means violations were found, not that kevlint failed. Status 2 means it couldn't use the trace.

---

## You did it

You now have QNX 8.0 on your laptop and a kernel trace you can read.

### What survives what

| | Reboot of the VM | Rebuilding with `mkqnximage --build` |
|---|---|---|
| `/data`, including your SSH files from step 6 | ✅ Kept | ❌ Erased |
| `/tmp` (a link to `/data/var/tmp`), including traces | ✅ Kept in our test | ❌ Erased |
| The running `sshd` | ❌ Start it again | ❌ Start it again, after repeating step 6 |

Because `/tmp` is on `/data`, old traces keep using its limited space until you delete them.

### Coming back later

In one terminal, boot the image, and at the `#` prompt start SSH:

```bash
~/qnxvm/run.sh
```

```sh
sh /data/home/root/start_ssh.sh
```

In a second terminal, you're ready to go:

```bash
source ~/qnx800/qnxsdp-env.sh
ssh -p 2222 root@localhost
```

To stop the virtual machine, press **Ctrl-A, then X** on the console.

### Where to go next

- Compile a program for the target with `qcc -Vgcc_ntox86_64 -o out file.c`, copy it over with `scp -P 2222`, and trace it.
- Read [kevlint's README](https://github.com/Adedayoke/kevlint/blob/main/README.md) to see how a trace can fail a CI build.
- See [QNX trace quirks](https://github.com/Adedayoke/kevlint/blob/main/docs/qnx-trace-quirks.md) for behaviour we found while working with real traces.
