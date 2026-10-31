"""Judgement filter: a review step that runs AFTER the VM.

The VM result is exact and deterministic. This module is NOT part of the transformer weights:
it is ordinary Python (rule-based heuristics), plus a pluggable interface for an LLM judge.
It looks at amount, destination and historical pattern and returns allow / hold / block.

LLMJudge.prompt builds the full reviewer prompt. SimulatedLLM returns canned JSON for the
five scenarios. It is not a model call. A real model plugs in through complete(prompt) -> str.
"""
import json
from dataclasses import dataclass, field
from statistics import median
from typing import Callable, Optional


@dataclass
class Tx:
    contract: str
    call: str               # human-readable call, e.g. "withdraw(450 ETH)"
    sender: str
    to: str                 # destination of the value
    amount: float           # in the contract's unit (ETH, USD, tokens)
    unit: str
    t: int                  # unix seconds


@dataclass
class Context:
    history: list = field(default_factory=list)   # past Tx accepted for this account
    known: set = field(default_factory=set)       # destinations seen before / allow-listed
    balance: Optional[float] = None               # current balance in `unit`, if known
    cap: Optional[float] = None                   # contract-level cap, if any


@dataclass
class Verdict:
    decision: str           # allow | hold | block
    reasons: list
    score: int
    source: str = "rules"


def fmt(x):
    return f"{x / 1e9:,.2f}B" if x >= 1e9 else f"{x / 1e6:,.1f}M" if x >= 1e6 else f"{x:,.4g}"


VM_STATUS = ["success", "revert", "out of gas", "invalid opcode", "stack error", "bad jump", "build limit"]

# Obvious stand-in contract addresses (not real accounts). Same map in site/judge.js.
CONTRACT_ADDRESS = {
    "Vault": "0xc0ffee0000000000000000000000000000c0ffee",
    "Token": "0xc0de00000000000000000000000000000000c0de",
    "SpendCap": "0xca9000000000000000000000000000000000ca90",
}
CONTRACT_ADDRESS_FALLBACK = "0xc0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0"

SYSTEM_ROLE = (
    "You are the transaction reviewer for a wallet. "
    "The VM has already run the contract exactly. "
    "Decide allow, hold or block based on everything you know about the sender and receiver and the run rules. "
    "You may only make the decision stricter, never looser than the rule verdict."
)

RULES_TEXT = (
    "Compare the amount with the usual size (the median of this sender's past calls). "
    "100 times the usual size or more adds 45. 20 times or more adds 30. 5 times or more adds 10. "
    "No history adds 10.\n"
    "A destination that has never been seen adds 20.\n"
    "4 or more withdrawals within 10 minutes adds 25. If that burst would drain over half the balance, add 25 more.\n"
    "90 percent or more of the contract cap in one call adds 20.\n"
    "Emptying 90 percent or more of the balance adds 15.\n"
    "A score of 60 or more blocks. A score of 30 or more holds. Otherwise the rules allow.\n"
    "If the VM status is not success, the decision is block, because the code itself rejected the call.\n"
    "The reviewer may only make the decision stricter, never looser than the rule verdict."
)

REPLY_FORMAT = (
    'Reply with JSON only, one object: '
    '{"decision": "allow" or "hold" or "block", "reason": "one sentence"}'
)

# Shown next to every canned reply. Examples, the site, and tests use these. No model is called.
SIMULATED_LABEL = "SIMULATED model output, no real LLM was called"

# Worked examples: a contract call that is allowed, and a withdrawal that is blocked.
EXAMPLE_CALL = "known-transfer"
EXAMPLE_WITHDRAW = "whale-new-dest"

SIMULATED_REPLIES = {
    "whale-new-dest": {
        "decision": "block",
        "reason": (
            "Usual withdrawals from this sender are about 4 ETH to one known address. "
            "This call withdraws 450 ETH, about 111 times that size, and the destination has never been seen, "
            "so it should be blocked."
        ),
    },
    "known-transfer": {
        "decision": "allow",
        "reason": (
            "This sender has transferred about 2 to 4 tokens to this same destination several times. "
            "A transfer of 3 tokens matches that history, and the run rules allow it."
        ),
    },
    "rapid-drain": {
        "decision": "block",
        "reason": (
            "Three withdrawals of 490 ETH already happened in the last few minutes, and this call takes another 490 ETH. "
            "Together that is 1,960 ETH and would drain most of the balance, so it should be blocked."
        ),
    },
    "near-cap": {
        "decision": "hold",
        "reason": (
            "Past spends were about 35 to 42 million USD. This call spends 950 million USD, about 24 times the usual size "
            "and 95 percent of the cap, so it should be held for a person to review."
        ),
    },
    "over-cap": {
        "decision": "block",
        "reason": (
            "The VM result is a revert because the call exceeds the cap. "
            "The code itself rejected it, so the decision is block."
        ),
    },
}


def _get(obj, key, default=None):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj[key] if key in obj else default
    if hasattr(obj, key):
        return getattr(obj, key)
    return default


def short_addr(addr) -> str:
    a = str(addr).strip()
    if len(a) <= 12:
        return a
    return a[:6] + "..." + a[-4:]


def rel_time(seconds) -> str:
    """Positive gap, half-up, matching site/judge.js."""
    seconds = abs(int(seconds))
    if seconds >= 86400:
        return f"{(seconds + 43200) // 86400}d earlier"
    if seconds >= 3600:
        return f"{(seconds + 1800) // 3600}h earlier"
    return f"{(seconds + 30) // 60}m earlier"


def contract_address(name) -> str:
    return CONTRACT_ADDRESS.get(str(name), CONTRACT_ADDRESS_FALLBACK)


def dumps_reply(decision: str, reason: str) -> str:
    return json.dumps({"decision": decision, "reason": reason}, ensure_ascii=False, separators=(", ", ": "))


def vm_record(status, gas_used, return_value, logs=None) -> dict:
    return {
        "status": int(status),
        "gas_used": gas_used,
        "return_value": return_value or "",
        "logs": list(logs or []),
    }


def _money(value, unit) -> str:
    if value is None:
        return "not recorded"
    return f"{fmt(value)} {unit}"


def _hist_block(rows, tx_t) -> str:
    if not rows:
        return "- none"
    lines = []
    for h in rows:
        gap = rel_time(int(tx_t) - int(_get(h, "t")))
        lines.append(
            f"- {_get(h, 'call')} from {_get(h, 'sender')} to {_get(h, 'to')}, "
            f"{fmt(_get(h, 'amount'))} {_get(h, 'unit')}, {gap}"
        )
    return "\n".join(lines)


def build_prompt(tx, vm, ctx, prior) -> str:
    """Full reviewer prompt: role, request, exact VM result, history, rules, JSON reply shape."""
    call = str(_get(tx, "call") or "")
    kind = call.split("(", 1)[0].strip().lower()
    caddr = contract_address(_get(tx, "contract"))
    wallet = short_addr(_get(tx, "sender"))
    line1 = f"Wallet {wallet} has requested calling contract {short_addr(caddr)} function {call}."
    if kind == "withdraw":
        line2 = (
            f"Wallet {wallet} would like to withdraw {fmt(_get(tx, 'amount'))} {_get(tx, 'unit')}. "
            "Based on everything you know about the context and the run rules, should that be allowed?"
        )
    else:
        line2 = (
            f"Wallet {wallet} has requested calling this smart contract. "
            "Based on everything you know about the context and the run rules, is this allowed?"
        )
    status = int(_get(vm, "status"))
    status_word = VM_STATUS[status] if 0 <= status < len(VM_STATUS) else str(status)
    ret = _get(vm, "return_value") or "none"
    gas = _get(vm, "gas_used", None)
    gas_s = "not recorded" if gas is None else f"{int(gas):,}"
    logs = _get(vm, "logs", None) or []
    logs_s = "none" if not logs else "\n".join(f"- {item}" for item in logs)
    hist = list(_get(ctx, "history", None) or [])
    sender = str(_get(tx, "sender"))
    receiver = str(_get(tx, "to"))
    sender_rows = [h for h in hist if str(_get(h, "sender")) == sender] or hist
    receiver_rows = [h for h in hist if str(_get(h, "to")) == receiver]
    known = sorted(str(k) for k in list(_get(ctx, "known", None) or []))
    reasons = list(_get(prior, "reasons", None) or [])
    reason_text = "; ".join(str(r) for r in reasons) if reasons else "none"
    unit = _get(tx, "unit") or ""
    lines = [
        "Role",
        SYSTEM_ROLE,
        "",
        "Transaction request",
        line1,
        line2,
        "",
        "VM result (exact)",
        "The VM circuits already ran this contract. This result is exact. It is handed over here, into this prompt.",
        f"status: {status_word}",
        f"return value: {ret}",
        f"gas: {gas_s}",
        f"logs: {logs_s}",
        "",
        "Sender and receiver",
        f"sender: {sender}",
        f"receiver: {receiver}",
        f"contract: {_get(tx, 'contract')} at {caddr}",
        "",
        "Sender history",
        _hist_block(sender_rows, _get(tx, "t")),
        "",
        "Receiver history",
        _hist_block(receiver_rows, _get(tx, "t")),
        "",
        f"Known destinations: {', '.join(known) if known else 'none'}",
        f"Balance: {_money(_get(ctx, 'balance', None), unit)}",
        f"Cap: {_money(_get(ctx, 'cap', None), unit)}",
        "",
        "Run rules",
        RULES_TEXT,
        f"Rule verdict: {_get(prior, 'decision')} (score {int(_get(prior, 'score'))})",
        f"Reasons: {reason_text}",
        "",
        "Reply format",
        REPLY_FORMAT,
    ]
    return "\n".join(lines) + "\n"


def parse_model_text(text: str):
    """Pull {decision, reason} out of a model reply. Also accepts 'decision: reason'."""
    if text is None:
        return None
    raw = str(text).strip()
    if raw.startswith(SIMULATED_LABEL):
        raw = raw[len(SIMULATED_LABEL):].strip()
    data = None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        i, j = raw.find("{"), raw.rfind("}")
        if i >= 0 and j > i:
            try:
                data = json.loads(raw[i:j + 1])
            except json.JSONDecodeError:
                data = None
    if isinstance(data, dict):
        decision = str(data.get("decision", "")).strip().lower()
        if decision in ("allow", "hold", "block"):
            return decision, str(data.get("reason", "")).strip()
    head, sep, rest = raw.partition(":")
    head = head.strip().lower()
    if sep and head in ("allow", "hold", "block"):
        return head, rest.strip()
    return None


class SimulatedLLM:
    """Canned JSON per scenario. Not a model. complete(prompt) ignores the text and returns the canned reply."""

    LABEL = SIMULATED_LABEL

    def __init__(self, scenario_id: str):
        if scenario_id not in SIMULATED_REPLIES:
            raise KeyError(scenario_id)
        self.scenario_id = scenario_id

    def complete(self, prompt: str) -> str:
        row = SIMULATED_REPLIES[self.scenario_id]
        return dumps_reply(row["decision"], row["reason"])

    def labeled(self, prompt: str) -> str:
        return f"{self.LABEL}\n{self.complete(prompt)}"


def rules(tx: Tx, vm_status: int, ctx: Context, vm_note: str = "") -> Verdict:
    """Rule-based review. Scores add up; >= 60 blocks, >= 30 holds for a human."""
    if vm_status != 0:
        return Verdict("block", [f"VM: {vm_note or VM_STATUS[vm_status]}; the code itself rejected it, "
                                 "nothing to review"], 100, "vm")
    reasons, score = [], 0
    amounts = [h.amount for h in ctx.history]
    typical = median(amounts) if amounts else None
    new_dest = tx.to not in ctx.known and all(h.to != tx.to for h in ctx.history)
    if typical:
        ratio = tx.amount / typical
        if ratio >= 100:
            score += 45; reasons.append(f"{ratio:,.0f}x the usual size (median {fmt(typical)} {tx.unit})")
        elif ratio >= 20:
            score += 30; reasons.append(f"{ratio:,.0f}x the usual size (median {fmt(typical)} {tx.unit})")
        elif ratio >= 5:
            score += 10; reasons.append(f"{ratio:,.1f}x the usual size")
    elif tx.amount > 0:
        score += 10; reasons.append("no history for this account")
    if new_dest:
        score += 20; reasons.append(f"destination {tx.to} never seen before")
    window = [h for h in ctx.history if tx.t - h.t <= 600]
    if len(window) >= 3:
        moved = sum(h.amount for h in window) + tx.amount
        score += 25; reasons.append(f"{len(window) + 1} withdrawals in 10 minutes ({fmt(moved)} {tx.unit} total)")
        if ctx.balance and moved >= 0.5 * (ctx.balance + sum(h.amount for h in window)):
            score += 25; reasons.append("pattern would drain over half the balance")
    if ctx.cap and tx.amount >= 0.9 * ctx.cap:
        score += 20; reasons.append(f"{tx.amount / ctx.cap:.0%} of the contract cap in one call")
    if ctx.balance and tx.amount >= 0.9 * ctx.balance:
        score += 15; reasons.append("empties 90%+ of the balance")
    decision = "block" if score >= 60 else "hold" if score >= 30 else "allow"
    if not reasons:
        reasons.append("in line with history, known destination")
    return Verdict(decision, reasons, min(score, 100))


class LLMJudge:
    """Pluggable LLM judge. `complete` is any callable prompt -> text.

    The text should be JSON: {"decision": "allow"|"hold"|"block", "reason": "..."}.
    A legacy 'decision: reason' line is also accepted. Pass SimulatedLLM(scenario_id).complete
    for the canned stand-in. Pass a real model function the same way. With no complete, the
    judge abstains and the rules decide.
    """

    def __init__(self, complete: Optional[Callable[[str], str]] = None):
        self.complete = complete

    @staticmethod
    def prompt(tx, vm_status, ctx, prior, vm=None) -> str:
        if vm is None:
            vm = vm_record(vm_status, None, "")
        elif _get(vm, "status", None) is None:
            vm = dict(vm)
            vm["status"] = vm_status
        return build_prompt(tx, vm, ctx, prior)

    def __call__(self, tx, vm_status, ctx, prior: Verdict, vm=None) -> Optional[Verdict]:
        if self.complete is None:
            return None
        text = self.complete(self.prompt(tx, vm_status, ctx, prior, vm))
        parsed = parse_model_text(text)
        if not parsed:
            return None
        decision, reason = parsed
        return Verdict(decision, [reason or "no reason given"], prior.score, "llm")


def review(tx, vm_status, ctx, vm_note="", llm: Optional[LLMJudge] = None, vm=None) -> Verdict:
    """Rules first. An LLM judge may only make the decision stricter, never looser."""
    v = rules(tx, vm_status, ctx, vm_note)
    if llm and v.source != "vm":
        o = llm(tx, vm_status, ctx, v, vm)
        order = ["allow", "hold", "block"]
        if o and order.index(o.decision) > order.index(v.decision):
            return o
    return v
