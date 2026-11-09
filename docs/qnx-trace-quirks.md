# QNX trace quirks

Behavior measured on the setup below, and how kevlint handles it. The measurements
say what happens, not why. We could not tell whether the kernel or `traceprinter` is
responsible for the user-event behavior, so treat any cause as unknown.

Setup: QNX 8.0.0 x86_64 (`uname -a`: `QNX noname 8.0.0 2026/06/05-16:26:03EDT x86pc
x86_64`) on QEMU with 2 CPUs, decoded on the host with `traceprinter` from SDP 8.0.

## User string events lose or cut their text

The call is `TraceEvent(_NTO_TRACE_INSERTUSRSTREVENT, event, string)`.

| String length (bytes) | What `traceprinter` shows | `TraceEvent` returns |
|---|---|---|
| Multiple of 8, tested 8 to 136 in steps of 8, and 192 | No `STR:` field, only `d0:`, `d1:`, ... words | 0 |
| Other lengths from 1 to 140, and 255 | `STR:"..."` with the full text | 0 |
| 256 and longer, tested up to 8000 | `STR:"..."` cut to the first 255 characters | 0 |

- `TraceEvent` returns 0 in every case, so the caller cannot detect either problem.
- Every multiple of 8 we tested below 256 loses its text. We did not test 200 to 248.
  A 256-byte or longer string is cut to 255 bytes, which is not a multiple of 8, so
  it does show a `STR:` field.
- The result does not depend on `-w`: 7, 8, 9, 16 and 256 bytes behaved the same in
  wide and fast mode.
- With a multiple-of-8 length, the decoded words hold exactly the string's bytes and
  no word contains the terminating zero. For example, 8 bytes give `d0` and `d1`
  only. A string of 9 bytes gives four words, with the terminator in `d2`.
- `traceprinter` does not escape `"` or `\` in `STR:`.
- `TraceEvent` also returns 0 when no logger is running, and nothing records the event.

Decoded output for 7, 8, 9, 16 and 256 bytes (`tracelogger -w`). The 256-byte line is
shortened here:

```
t:0x2cf92c057cd5 CPU:00 USREVENT        :EVENT:100          STR:"aaaaaaa", d0:0x61616161 d1:0x00616161
t:0x2cf92c90492f CPU:00 USREVENT        :EVENT:100         , d0:0x61616161 d1:0x61616161
t:0x2cf92c922751 CPU:00 USREVENT        :EVENT:100          STR:"aaaaaaaaa", d0:0x61616161 d1:0x61616161 d2:0x00000061 d3:0x00000000
t:0x2cf92c9394fb CPU:00 USREVENT        :EVENT:100         , d0:0x61616161 d1:0x61616161 d2:0x61616161 d3:0x61616161
t:0x2cf92c9a2582 CPU:00 USREVENT        :EVENT:100          STR:"aaa...255 characters...aaa", d0:0x61616161 ...
```

### How it was measured

1. A probe wrote strings under `tracelogger -w -s 2` and printed each return value,
   and also once with no logger running.
2. A sweep wrote every length from 1 to 140, plus 192, 255, 256, 257, 320, 384, 511,
   512, 513, 1000, 1024, 2048 and 4096, in three rounds. All 459 events were recorded,
   in order, so each decoded line could be matched to its length. Results were the
   same in all three rounds.
3. A minimal program wrote 7, 8, 9, 16 and 256 bytes under `tracelogger -w` and
   `tracelogger` without `-w`.

### What kevlint does about it

`kevlintd` limits marker text to 128 bytes and adds a `pid:<n> tid:<n> ` prefix, so
the string it logs is at most 159 bytes, below the 255-byte cut. If the length is a
multiple of 8, it appends one space. It strips trailing spaces from the client's
text first, so the host can remove the padding without guessing.

`host/kevlint/parser.py` counts an event 100 line it cannot read as an undecoded
marker, and the terminal output reports that count.

`target/kevlintd_test.c lengths` writes every text length from 1 to 128 through
`kevlintd`. In the capture `fixtures/kevlintd_lengths.txt`, all 128 decode exactly,
none are undecoded, and 16 were padded. `host/tests/test_markers_parser.py` checks
that file.
