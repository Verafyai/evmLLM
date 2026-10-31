"""Fill README tables from results/*.json (so README numbers come from real runs)."""
import glob
import json

from evmllm.demos import DEMOS

R = json.load(open("results/demos_ref.json"))
J = json.load(open("results/judge.json"))
circ = sorted((r for f in glob.glob("results/circuit_*.json") for r in json.load(open(f))["runs"]), key=lambda r: r["row"])
num = {d[1]: i + 1 for i, d in enumerate(DEMOS)}
demo = "\n".join(f"| {num[r['contract']]} | {r['contract']} | {r['input']} | `{r['outcome'] if len(r['outcome']) < 40 else r['outcome'][:8] + '...' + r['outcome'][-6:]}` | {r['gas_used']:,} | {r['evm_steps']:,} | {r['tokens']:,} |" for r in R)
judge = "\n".join(f"| {s['title']} | `{s['tx']['call']}` | {s['vm']['outcome'][:28]} | **{s['verdict']['decision'].upper()}** ({s['verdict']['score']}) | {'; '.join(s['verdict']['reasons'])} |" for s in J)
ok = [r for r in circ if r["match"]]
summ = f"{len(ok)}/{len(circ)} runs, {sum(r['tokens'] for r in ok):,} tokens" if circ else "pending"
table = ("Runs on the compiled weights (each generated token fed back as the next input):\n\n| Contract | Input | Tokens | Result |\n|---|---|---|---|\n" +
         "\n".join(f"| {r['contract']} | {r['input']} | {r['tokens']:,} | {'exact match' if r['match'] else 'mismatch at ' + str(r['first_mismatch'])} |" for r in circ)) if circ else ""
s = open("README.md").read()
for k, v in {"DEMO_TABLE": demo, "JUDGE_TABLE": judge, "CIRCUIT_SUMMARY": summ, "CIRCUIT_TABLE": table}.items():
    s = s.replace(k, v)
open("README.md", "w").write(s)
