CC=gcc

all: sgx_enable

%.o: %.c
	$(CC) -c $< 

sgx_enable: sgx_enable.o
	$(CC) -o $@ $?

clean:
	rm -f *.o sgx_enable
