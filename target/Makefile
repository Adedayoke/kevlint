CC = qcc -Vgcc_ntox86_64
CFLAGS = -Wall

all: sensor fusion control

%: %.c kevdemo.h
	$(CC) $(CFLAGS) -o $@ $<

clean:
	rm -f sensor fusion control

.PHONY: all clean
