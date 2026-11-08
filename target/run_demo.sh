#!/bin/sh
# Usage: run_demo.sh <out.kev> [--inversion|--fixed]
# Run from the directory holding the binaries, on the target.
out="$1"
flag="$2"

./control -d 8 &
./fusion $flag -d 8 &
./sensor -d 8 &
sleep 2
tracelogger -w -s 3 -f "$out"
wait
