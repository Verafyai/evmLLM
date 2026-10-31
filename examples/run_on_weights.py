"""Run one EVM job on Qwenputer's compiled weights and check it token by token.

  PYTHONPATH=. python examples/run_on_weights.py            # tiny inline program
  PYTHONPATH=. python examples/run_on_weights.py --row 10   # demo row 10, gcd(1071, 462)
"""
import argparse

import numpy as np
import torch
from chipformer.rv import core, fastexec
from chipformer.rv import spec as S

from evmllm.demos import describe, jobs
from evmllm.host import Job, machine, run_ref

ap = argparse.ArgumentParser()
ap.add_argument("--row", type=int, help="demo row (see evmllm/demos.py)")
ap.add_argument("--threads", type=int, default=4)
a = ap.parse_args()
torch.set_num_threads(a.threads)

if a.row is None:  # 5 + 7, stored to slot 0 and returned
    job, rtype = Job(code=bytes.fromhex("6007600501805f555f5260205ff3"), trace=True), "uint256"
else:
    *_, rtype, job = list(jobs())[a.row]

ref = run_ref(job)                        # bit-exact reference machine: result + full token trace
print("reference:", describe(ref.ret, ref.status, rtype), f"gas {ref.gas_used}, {len(ref._toks)} tokens")

c = core.build()                          # compile the RV32IM CPU into transformer weights (no training)
fr = fastexec.FreeRun(c, 1, "cpu", False, sparse=True)
core.snapshot(c, machine(job))            # firmware ROM + EVM job block into the circuit's RAM state
fr.load(0, S.tok("GO"))
out = fr.run(len(ref._toks) - 1)[0]       # every predicted token is fed back as the next input
expect = np.asarray(ref._toks[1:])
ok = bool((np.asarray(out) == expect).all())
out_bytes = bytes(S.family(int(t))[1] for t in out if S.family(int(t))[0] == "BYTE")
print("weights:", "MATCH" if ok else "DIFF", f"({len(expect)} tokens)")
print("printed by the weights:", out_bytes.decode(errors="replace").strip())
