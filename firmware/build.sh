#!/bin/bash
# Build the EVM firmware for the Qwenputer RV32 machine (rom.ld memory map).
set -euo pipefail
cd "$(dirname "$0")"
GCC=riscv64-unknown-elf-gcc
LIBGCC=$($GCC -march=rv32im -mabi=ilp32 -print-libgcc-file-name)
$GCC -march=rv32im_zba -mabi=ilp32 -O2 -ffreestanding -fno-builtin -nostdlib -nostartfiles -static \
  -T rom.ld -o evm.elf crt0.S evm.c exitstub.c $LIBGCC
riscv64-unknown-elf-size evm.elf
