"""Run demo jobs on the compiled Qwenputer circuit (fastexec.FreeRun, CPU, sparse weights).

Each row free-runs: the circuit's output code is decoded into the next token, whose embedding
is fed back as the next input. Every generated token is compared with the bit-exact reference
trace. Usage: python tests/circuit_run.py [demo-row indices...]
"""
import json
import sys
import time

import numpy as np
import torch
from chipformer.rv import core, fastexec
from chipformer.rv import spec as S

from evmllm.demos import jobs
from evmllm.host import machine, run_ref

torch.set_num_threads(int(__import__("os").environ.get("THREADS", "8")))
ALL = list(jobs())
pick = [int(a) for a in sys.argv[1:]] or list(range(len(ALL)))
sel = [ALL[i] for i in pick]
refs = []
for did, name, label, sig, rtype, j in sel:
    r = run_ref(j)
    refs.append(np.asarray(r._toks))
    print(f"#{did} {name} {label}: {len(r._toks)} ref tokens", flush=True)

t0 = time.time()
c = core.build()
fr = fastexec.FreeRun(c, len(sel), "cpu", False, sparse=True)
for row, (*_, j) in enumerate(sel):
    core.snapshot(c, machine(j))
    fr.load(row, S.tok("GO"))
print(f"init {time.time() - t0:.1f}s", flush=True)

need = [len(t) - 1 for t in refs]
got = [[] for _ in sel]
first_bad = [None] * len(sel)
done, t1, CH = 0, time.time(), 1000
while done < max(need):
    out = fr.run(CH)
    for row in range(len(sel)):
        lo, hi = done, min(done + CH, need[row])
        if lo >= hi:
            continue
        seg = out[row, :hi - lo]
        got[row].extend(seg.tolist())
        exp = refs[row][1 + lo:1 + hi]
        if first_bad[row] is None and not (seg == exp).all():
            first_bad[row] = lo + int(np.argmax(seg != exp))
    done += CH
    print(f"{done} steps, {done * len(sel) / (time.time() - t1):.0f} tok/s total, "
          f"mismatch rows {[i for i, b in enumerate(first_bad) if b is not None]}", flush=True)

summary = []
for row, (did, name, label, sig, rtype, j) in enumerate(sel):
    m = first_bad[row] is None and len(got[row]) == need[row]
    summary.append({"row": pick[row], "demo": did, "contract": name, "input": label,
                    "tokens": need[row], "match": m, "first_mismatch": first_bad[row]})
    np.save(f"results/circuit_{pick[row]}.tokens.npy", np.asarray(got[row], dtype=np.int32))
    print(("MATCH " if m else "DIFF  ") + f"#{did} {name} {label} {need[row]} tokens", flush=True)
json.dump({"elapsed_s": round(time.time() - t0, 1), "runs": summary},
          open(f"results/circuit_{'_'.join(map(str, pick))}.json", "w"), indent=1)
