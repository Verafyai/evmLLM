"""Prompt builder, simulated replies, stricter-only handoff, and JS parity.

No model is called. SimulatedLLM is canned JSON, labeled as such.
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evmllm.judge import (  # noqa: E402
    EXAMPLE_CALL,
    EXAMPLE_WITHDRAW,
    SIMULATED_LABEL,
    SIMULATED_REPLIES,
    SYSTEM_ROLE,
    Context,
    LLMJudge,
    SimulatedLLM,
    Tx,
    build_prompt,
    parse_model_text,
    review,
    vm_record,
)

CASES = json.loads((ROOT / "results" / "judge.json").read_text())
ORDER = ["allow", "hold", "block"]


def tx_of(d):
    return Tx(**d)


def ctx_of(d):
    hist = [Tx(**h) for h in d["history"]]
    return Context(history=hist, known=set(d["known"]), balance=d.get("balance"), cap=d.get("cap"))


def vm_of(s):
    return vm_record(s["vm"]["status"], s["vm"]["gas_used"], s["vm"]["outcome"], logs=[])


def case(cid):
    return next(s for s in CASES if s["id"] == cid)


def main():
    assert "—" not in SYSTEM_ROLE and "–" not in SYSTEM_ROLE
    assert SYSTEM_ROLE.startswith("You are the transaction reviewer for a wallet.")
    assert "never looser than the rule verdict." in SYSTEM_ROLE

    prompts = {}
    for s in CASES:
        tx, ctx, vm = tx_of(s["tx"]), ctx_of(s["ctx"]), vm_of(s)
        prior = s["verdict"]
        prompt = build_prompt(tx, vm, ctx, prior)
        prompt_d = build_prompt(s["tx"], vm, s["ctx"], prior)
        assert prompt == prompt_d, s["id"]
        prompts[s["id"]] = prompt
        assert prompt.startswith("Role\n" + SYSTEM_ROLE + "\n")
        assert "Transaction request\n" in prompt
        assert "VM result (exact)\n" in prompt
        assert "handed over here, into this prompt." in prompt
        assert f"status: " in prompt and s["vm"]["outcome"] in prompt
        assert f"gas: {s['vm']['gas_used']:,}" in prompt
        assert "logs: none" in prompt
        assert "Sender history\n" in prompt and "Receiver history\n" in prompt
        assert "Run rules\n" in prompt
        assert f"Rule verdict: {prior['decision']} (score {prior['score']})" in prompt
        assert '"decision": "allow" or "hold" or "block"' in prompt
        assert "—" not in prompt and "–" not in prompt
        sim = SimulatedLLM(s["id"])
        labeled = sim.labeled(prompt)
        assert labeled.startswith(SIMULATED_LABEL + "\n"), labeled[:80]
        parsed = parse_model_text(labeled)
        assert parsed == (SIMULATED_REPLIES[s["id"]]["decision"], SIMULATED_REPLIES[s["id"]]["reason"])
        # Canned replies match the rule decision, so the final verdict does not get looser or stricter.
        final = review(tx, s["vm"]["status"], ctx, s["vm"]["outcome"], LLMJudge(sim.complete), vm)
        assert final.decision == prior["decision"], (s["id"], final, prior)
        assert final.source == prior["source"]
        assert SIMULATED_REPLIES[s["id"]]["decision"] == prior["decision"]

    call = prompts[EXAMPLE_CALL]
    assert "has requested calling this smart contract." in call
    assert "is this allowed?" in call
    assert "would like to withdraw" not in call
    withdraw = prompts[EXAMPLE_WITHDRAW]
    assert "would like to withdraw 450 ETH." in withdraw
    assert "should that be allowed?" in withdraw
    assert "never been seen" in SIMULATED_REPLIES[EXAMPLE_WITHDRAW]["reason"]
    assert "111 times" in SIMULATED_REPLIES[EXAMPLE_WITHDRAW]["reason"]
    assert SIMULATED_REPLIES[EXAMPLE_CALL]["decision"] == "allow"
    assert SIMULATED_REPLIES[EXAMPLE_WITHDRAW]["decision"] == "block"

    # Plug-in: a real complete() can only tighten.
    allow = case(EXAMPLE_CALL)
    tx, ctx, vm = tx_of(allow["tx"]), ctx_of(allow["ctx"]), vm_of(allow)

    def hold(prompt):
        assert "Role\n" in prompt and "is this allowed?" in prompt
        return '{"decision": "hold", "reason": "Hold it for a person to look."}'

    tightened = review(tx, 0, ctx, allow["vm"]["outcome"], LLMJudge(hold), vm)
    assert tightened.decision == "hold" and tightened.source == "llm"
    assert tightened.reasons == ["Hold it for a person to look."]

    def loosen(prompt):
        return '{"decision": "allow", "reason": "Looks fine to me."}'

    blocked = case(EXAMPLE_WITHDRAW)
    btx, bctx, bvm = tx_of(blocked["tx"]), ctx_of(blocked["ctx"]), vm_of(blocked)
    stayed = review(btx, 0, bctx, blocked["vm"]["outcome"], LLMJudge(loosen), bvm)
    assert stayed.decision == "block" and stayed.source == "rules"

    # A VM revert is already a block. The model is not asked to loosen it.
    over = case("over-cap")
    otx, octx = tx_of(over["tx"]), ctx_of(over["ctx"])
    asked = {"n": 0}

    def should_not_run(prompt):
        asked["n"] += 1
        return '{"decision": "allow", "reason": "no"}'

    vm_block = review(otx, over["vm"]["status"], octx, over["vm"]["outcome"], LLMJudge(should_not_run))
    assert vm_block.decision == "block" and vm_block.source == "vm" and asked["n"] == 0

    # Abstaining plug (no complete) leaves the rules in charge.
    bare = review(tx, 0, ctx, allow["vm"]["outcome"], LLMJudge(None), vm)
    assert bare.decision == "allow" and bare.source == "rules"

    node = subprocess.run(["node", str(ROOT / "tests" / "judge_prompt_parity.js")],
                          check=True, capture_output=True, text=True)
    js = json.loads(node.stdout)
    assert [row["id"] for row in js] == [s["id"] for s in CASES]
    for row, s in zip(js, CASES):
        assert row["prompt"] == prompts[row["id"]], row["id"]
        assert row["promptFromRules"] == prompts[row["id"]], row["id"]
        assert row["simulated"]["label"] == SIMULATED_LABEL
        assert row["simulated"]["json"] == SimulatedLLM(row["id"]).complete("")
        assert row["simulated"]["decision"] == SIMULATED_REPLIES[row["id"]]["decision"]
        assert row["simulated"]["reason"] == SIMULATED_REPLIES[row["id"]]["reason"]
        assert row["rules"]["decision"] == s["verdict"]["decision"]
        assert row["rules"]["score"] == s["verdict"]["score"]
        assert row["rules"]["reasons"] == s["verdict"]["reasons"]
        assert row["rules"]["source"] == s["verdict"]["source"]

    readme = (ROOT / "README.md").read_text()
    # The worked examples are pasted from this builder. Fail if they drift.
    if "How the handoff works" in readme:
        for cid in (EXAMPLE_CALL, EXAMPLE_WITHDRAW):
            assert prompts[cid] in readme, cid
            assert SimulatedLLM(cid).labeled(prompts[cid]) in readme
        for bit in ("handed over here", "SIMULATED model output, no real LLM was called",
                    "hand-set weights", "future work"):
            assert bit in readme, bit

    site_html = (ROOT / "site" / "index.html").read_text()
    site_js = (ROOT / "site" / "app.js").read_text()
    if "How the judge prompt works" in site_html:
        assert "handed over here" in site_html
        assert "SIMULATED" in site_html
        assert "heuristic outside the weights" in site_html.lower() or "heuristic outside the weights" in site_js.lower() or "NOT part of the weights" in site_html
        assert "Simulated model output" in site_html or "Simulated model output" in site_js

    # No em dash or en dash in the new judge copy.
    for path in (ROOT / "evmllm" / "judge.py", ROOT / "site" / "judge.js", ROOT / "evmllm" / "judge_examples.py"):
        text = path.read_text()
        assert "—" not in text and "–" not in text, path

    print(f"judge prompt tests passed ({len(CASES)} scenarios, JS parity included)")
    for cid in (EXAMPLE_CALL, EXAMPLE_WITHDRAW):
        print("\n" + "=" * 8 + " " + cid + " " + "=" * 8)
        print(prompts[cid], end="")
        print(SimulatedLLM(cid).labeled(prompts[cid]))


if __name__ == "__main__":
    main()
