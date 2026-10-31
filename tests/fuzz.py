"""Differential fuzz: random EVM programs on the RV32 firmware (Qwenputer reference machine)
versus py-evm (Shanghai). Compares status, gas used, return data and changed storage."""
import json
import random
import sys
import time

from evmllm.host import Job, run_pyevm, run_ref

EDGE = [0, 1, 2, 3, 31, 32, 255, 256, 2**32 - 1, 2**32, 2**64 - 1, 2**128, 2**255, 2**256 - 1,
        2**256 - 2, 10**18, 10**9]
BIN = [0x01, 0x02, 0x03, 0x04, 0x06, 0x0A, 0x10, 0x11, 0x12, 0x13, 0x14, 0x16, 0x17, 0x18,
       0x1A, 0x1B, 0x1C, 0x1D]
UN = [0x15, 0x19]


def push(v):
    b = v.to_bytes(32, "big").lstrip(b"\0") or b"\0"
    return bytes([0x5F + len(b)]) + b


def rand_val(rng):
    r = rng.random()
    if r < 0.4:
        return rng.choice(EDGE)
    if r < 0.6:
        return rng.randint(0, 300)
    if r < 0.8:
        return rng.getrandbits(rng.choice([8, 32, 64, 128]))
    return rng.getrandbits(256)


def gen(seed):
    rng = random.Random(seed)
    code = bytearray()
    depth = 0
    for _ in range(rng.randint(3, 14)):
        r = rng.random()
        if depth < 2 or r < 0.3:
            v = rand_val(rng)
            if rng.random() < 0.2 and v > 0:  # small exponents keep EXP cheap
                v %= 40
            code += push(v); depth += 1
        elif r < 0.65:
            op = rng.choice(BIN)
            if op == 0x0A:
                code += bytes([0x90]) + push(rng.randint(0, 300)) + bytes([0x90])  # base, small exp
                code = code[:-1]  # keep stack shape: replace top with exponent
                code += bytes([0x50]) + push(rng.randint(0, 300)) + bytes([0x90])
            code.append(op); depth -= 1
        elif r < 0.72:
            code.append(rng.choice(UN))
        elif r < 0.8:
            k = rng.randint(1, min(depth, 4)); code.append(0x7F + k); depth += 1
        elif r < 0.88:
            k = rng.randint(1, min(depth - 1, 4)); code.append(0x8F + k)
        elif r < 0.94:  # SSTORE top under a small key, then SLOAD it back
            key = rng.randint(0, 5)
            code += bytes([0x80]) + push(key) + bytes([0x55]) + push(key) + bytes([0x54, 0x18])
        else:  # MSTORE / MLOAD round trip
            off = rng.randint(0, 200)
            code += bytes([0x80]) + push(off) + bytes([0x52]) + push(off) + bytes([0x51, 0x01])
            depth -= 0
    # return the top word
    code += push(0) + bytes([0x52]) + push(32) + push(0) + bytes([0xF3])
    r = rng.random()
    if r < 0.04:  # error cases
        code = bytes([rng.choice([0x0C, 0xFE, 0x21])]) + code
    elif r < 0.08:
        code = push(rng.randint(0, 40)) + bytes([0x56]) + code
    elif r < 0.12:
        code = bytes([0x01]) + code  # stack underflow
    gas = rng.choice([1_000_000] * 9 + [rng.randint(21, 300)])
    pre = {k: rand_val(rng) for k in range(rng.randint(0, 3))}
    return Job(bytes(code), gas=gas, storage=pre)


def main(n=200, start=0):
    fails, results = [], []
    t0 = time.time()
    instr = 0
    for s in range(start, start + n):
        j = gen(s)
        a, b = run_ref(j), run_pyevm(j, extra_keys=())
        instr += a.rv_instructions
        ok = a.key() == b.key()
        results.append({"seed": s, "ok": ok, "status": a.status, "gas": a.gas_used,
                        "rv_instructions": a.rv_instructions})
        if not ok:
            fails.append(s)
            print("MISMATCH", s, j.code.hex(), "\n  fw ", a.key(), "\n  ref", b.key(), flush=True)
    print(f"fuzz: {n - len(fails)}/{n} match py-evm, {instr} RV32 instructions, "
          f"{time.time() - t0:.0f} s; failing seeds {fails[:20]}")
    return results, fails


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    res, fails = main(n)
    json.dump({"n": n, "pass": n - len(fails), "fails": fails, "runs": res},
              open("results/fuzz.json", "w"), indent=1)
