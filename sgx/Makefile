CC=gcc

all: sgx_enable epc_sections

%.o: %.c
	$(CC) -c $<

sgx_enable: sgx_enable.o
	$(CC) -o $@ $?

epc_sections: epc_sections.o
	$(CC) -o $@ $?

clean:
	rm -f *.o sgx_enable epc_sections
