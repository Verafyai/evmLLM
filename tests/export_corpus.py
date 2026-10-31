"""Export the fuzz programs and demo jobs with py-evm results, so other implementations
(the site's browser reference VM) can be checked against the same reference."""
import json
import sys

sys.path.insert(0, "tests")
from fuzz import gen  # noqa: E402

from evmllm.demos import jobs  # noqa: E402
from evmllm.host import run_pyevm  # noqa: E402


def row(j, r, **kw):
    return dict(kw, code=j.code.hex(), calldata=j.calldata.hex(), gas=j.gas,
                storage={hex(k): hex(v) for k, v in j.storage.items()},
                status=r.status, gas_used=r.gas_used, ret=r.ret.hex() if r.status <= 1 else "",
                changed={hex(k): hex(v) for k, v in r.storage.items()} if r.status == 0 else {})


out = [row(j, run_pyevm(j), kind="demo", label=f"{name}: {label}") for _, name, label, _, _, j in jobs()]
out += [row(gen(s), run_pyevm(gen(s)), kind="fuzz", label=f"seed {s}") for s in range(2000)]
json.dump(out, open("results/pyevm_corpus.json", "w"))
print(len(out), "cases")
