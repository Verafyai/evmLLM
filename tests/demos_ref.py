"""Every demo input on the RV32 firmware (Qwenputer reference machine) vs py-evm."""
import json
import time

from evmllm.demos import describe, jobs
from evmllm.host import run_pyevm, run_ref

rows, ok_n = [], 0
for did, name, label, sig, rtype, j in jobs():
    t = time.time()
    a = run_ref(j)
    b = run_pyevm(j)
    ok = a.key() == b.key()
    ok_n += ok
    rows.append({"demo": did, "contract": name, "input": label, "calldata": j.calldata.hex(),
                 "ok": ok, "status": a.status, "gas_used": a.gas_used, "ret": a.ret.hex(),
                 "storage": {hex(k): hex(v) for k, v in a.storage.items()},
                 "outcome": describe(a.ret, a.status, rtype), "evm_steps": len(a.ops),
                 "ops": a.ops, "rv_instructions": a.rv_instructions, "tokens": a.tokens,
                 "ref_outcome": describe(b.ret, b.status, rtype), "ref_gas": b.gas_used})
    print(f"{'OK ' if ok else 'BAD'} #{did} {name:9s} {label:34s} {rows[-1]['outcome'][:40]:40s} "
          f"gas {a.gas_used:6d}/{b.gas_used:6d} steps {len(a.ops):5d} rv {a.rv_instructions:7d} "
          f"tok {a.tokens:7d} {time.time() - t:.1f}s", flush=True)
print(f"demos: {ok_n}/{len(rows)} match py-evm")
json.dump(rows, open("results/demos_ref.json", "w"), indent=1)
