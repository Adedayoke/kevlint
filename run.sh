#!/bin/bash
cd ~/qnxvm
qemu-system-x86_64 -smp 2 --cpu max -m 1G \
  -drive file=output/disk-qemu.vmdk,if=ide,id=drv0 \
  -netdev user,id=net0,hostfwd=tcp::2222-:22 \
  -device virtio-net-pci,netdev=net0,mac=52:54:00:ff:98:86 \
  -pidfile output/qemu.pid -nographic -kernel output/ifs.bin -serial mon:stdio \
  -object rng-random,filename=/dev/urandom,id=rng0 -device virtio-rng-pci,rng=rng0
