CC = qcc -Vgcc_ntox86_64
CFLAGS = -Wall

all: sensor fusion control kevlintd kevlintd_test

%: %.c kevdemo.h kevlintd.h
	$(CC) $(CFLAGS) -o $@ $<

clean:
	rm -f sensor fusion control kevlintd kevlintd_test

.PHONY: all clean
