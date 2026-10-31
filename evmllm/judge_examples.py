"""Five judgement-filter scenarios: exact VM run on the RV32 firmware (checked against py-evm),
then the rule-based review."""
import json
from dataclasses import asdict

from evmllm.demos import ART, E, USD, calldata, describe
from evmllm.host import Job, run_pyevm, run_ref
from evmllm.judge import Context, Tx, review

T0 = 1_790_000_000
A = {"ops": "0xa11ce00000000000000000000000000000a11ce0", "payroll": "0x9a7b000000000000000000000000000000009a7b",
     "unknown": "0x7e5700000000000000000000000000000000bad0", "bob": "0xb0b0000000000000000000000000000000000b0b"}


def hist(to, amounts, unit, start, gap, call):
    return [Tx("", (f"{call}(${a:,.0f})" if unit == "USD" else f"{call}({a:g} {unit})"), A["ops"], to, a, unit, start + i * gap) for i, a in enumerate(amounts)]


SCENARIOS = [
    dict(id="whale-new-dest", title="Valid withdrawal, 100x usual size, never-seen address",
         contract="Vault", sig="withdraw(uint256)", args=[450 * E], rtype="uint256",
         storage={0: 2000 * E, 1: 500 * E},
         tx=Tx("Vault", "withdraw(450 ETH)", A["ops"], A["unknown"], 450, "ETH", T0 + 90 * 86400),
         ctx=dict(history=hist(A["payroll"], [4, 4.5, 3.8, 4.2, 4, 4.1], "ETH", T0, 7 * 86400, "withdraw"),
                  known={A["payroll"]}, balance=2000)),
    dict(id="known-transfer", title="Ordinary transfer to a known address",
         contract="Token", sig="transfer(uint256,uint256,uint256)", args=[0, 1, 3 * E], rtype="uint256",
         storage={0: 100 * E, 1: 5 * E},
         tx=Tx("Token", "transfer(3 tokens)", A["ops"], A["bob"], 3, "tokens", T0 + 30 * 86400),
         ctx=dict(history=hist(A["bob"], [2, 3, 2.5, 3, 4], "tokens", T0, 5 * 86400, "transfer"),
                  known={A["bob"]}, balance=100)),
    dict(id="rapid-drain", title="Rapid repeated withdrawals, each one within the limit",
         contract="Vault", sig="withdraw(uint256)", args=[490 * E], rtype="uint256",
         storage={0: 530 * E, 1: 500 * E},
         tx=Tx("Vault", "withdraw(490 ETH)", A["ops"], A["unknown"], 490, "ETH", T0 + 480),
         ctx=dict(history=hist(A["unknown"], [490, 490, 490], "ETH", T0, 120, "withdraw"),
                  known={A["payroll"]}, balance=530)),
    dict(id="near-cap", title="Spend under the $1B cap, but 95% of it in one call",
         contract="SpendCap", sig="spend(uint256)", args=[950_000_000 * USD], rtype="uint256", storage={},
         tx=Tx("SpendCap", "spend($950,000,000)", A["ops"], A["payroll"], 950e6, "USD", T0 + 40 * 86400),
         ctx=dict(history=hist(A["payroll"], [40e6, 35e6, 42e6, 38e6], "USD", T0, 7 * 86400, "spend"),
                  known={A["payroll"]}, cap=1e9)),
    dict(id="over-cap", title="Spend over the $1B cap: the code itself reverts",
         contract="SpendCap", sig="spend(uint256)", args=[1_200_000_000 * USD], rtype="uint256", storage={},
         tx=Tx("SpendCap", "spend($1,200,000,000)", A["ops"], A["payroll"], 1.2e9, "USD", T0 + 41 * 86400),
         ctx=dict(history=hist(A["payroll"], [2e6, 1.5e6, 2.2e6], "USD", T0, 7 * 86400, "spend"),
                  known={A["payroll"]}, cap=1e9)),
]


def main():
    out = []
    for s in SCENARIOS:
        j = Job(bytes.fromhex(ART[s["contract"]]["runtime"]), calldata(s["sig"], s["args"]), storage=dict(s["storage"]))
        a, b = run_ref(j), run_pyevm(j)
        ctx = Context(**s["ctx"])
        note = describe(a.ret, a.status, s["rtype"])
        v = review(s["tx"], a.status, ctx, note)
        out.append({"id": s["id"], "title": s["title"], "contract": s["contract"], "sig": s["sig"],
                    "code": j.code.hex(), "calldata": j.calldata.hex(), "rtype": s["rtype"],
                    "storage": {hex(k): hex(v_) for k, v_ in j.storage.items()},
                    "vm": {"status": a.status, "gas_used": a.gas_used, "outcome": note,
                           "matches_pyevm": a.key() == b.key(), "rv_instructions": a.rv_instructions},
                    "tx": asdict(s["tx"]),
                    "ctx": {"history": [asdict(h) for h in ctx.history], "known": sorted(ctx.known),
                            "balance": ctx.balance, "cap": ctx.cap},
                    "verdict": asdict(v)})
        print(f"{s['id']:15s} VM {note:32s} pyevm={'match' if out[-1]['vm']['matches_pyevm'] else 'DIFF'}  "
              f"-> {v.decision.upper():5s} ({v.score}) {'; '.join(v.reasons)}")
    json.dump(out, open("results/judge.json", "w"), indent=1)


if __name__ == "__main__":
    main()
