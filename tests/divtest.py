"""Targeted DIV/MOD check of the firmware against Python big ints (multi-limb divisors)."""
import random

from evmllm.host import Job, run_ref

random.seed(7)
M = 2**256


def rnd():
    bits = random.choice([33, 40, 63, 64, 65, 96, 128, 160, 200, 255, 256])
    v = random.getrandbits(bits) | (1 << (bits - 1))
    if random.random() < 0.2:
        v = (v >> 32) << 32  # zero low limbs
    if random.random() < 0.1:
        v = M - 1 - random.getrandbits(8)
    return v % M


bad = n = 0
for _ in range(400):
    a, b = rnd(), rnd()
    if random.random() < 0.5 and b > a:
        a, b = b, a
    if random.random() < 0.1:
        a = b * random.getrandbits(40) + random.getrandbits(8)
        a %= M
    code = (b"\x7f" + b.to_bytes(32, "big") + b"\x7f" + a.to_bytes(32, "big") + b"\x04\x5f\x52"
            + b"\x7f" + b.to_bytes(32, "big") + b"\x7f" + a.to_bytes(32, "big") + b"\x06\x60\x20\x52"
            + b"\x60\x40\x5f\xf3")
    r = run_ref(Job(code, b""))
    exp = (a // b).to_bytes(32, "big") + (a % b).to_bytes(32, "big")
    n += 1
    if r.ret != exp:
        bad += 1
        print("BAD", hex(a), hex(b))
print(f"div/mod: {n - bad}/{n} correct")
