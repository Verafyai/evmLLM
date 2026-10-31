void exit(int c){for(;;){register long a0 __asm__("a0")=c; register long a7 __asm__("a7")=93; __asm__ volatile("ecall"::"r"(a0),"r"(a7));}}
