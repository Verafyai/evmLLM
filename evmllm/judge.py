"""Judgement filter: a review step that runs AFTER the VM.

The VM result is exact and deterministic. This module is NOT part of the transformer weights:
it is ordinary Python (rule-based heuristics), plus a pluggable interface for an LLM judge.
It looks at amount, destination and historical pattern and returns allow / hold / block.
"""
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
    """Pluggable LLM judge. `complete` is any callable prompt -> text that answers with
    'allow|hold|block: reason'. Without one, this stub abstains and the rules decide."""

    def __init__(self, complete: Optional[Callable[[str], str]] = None):
        self.complete = complete

    @staticmethod
    def prompt(tx: Tx, vm_status: int, ctx: Context, prior: Verdict) -> str:
        hist = "\n".join(f"- {h.call} to {h.to} at t={h.t}" for h in ctx.history[-20:]) or "- none"
        return (f"You review a transaction AFTER an exact EVM execution.\nVM status: {VM_STATUS[vm_status]}\n"
                f"Call: {tx.call} on {tx.contract} from {tx.sender} to {tx.to}\nHistory:\n{hist}\n"
                f"Rule-based verdict: {prior.decision} ({'; '.join(prior.reasons)})\n"
                "Answer 'allow', 'hold' or 'block', a colon, then one sentence of reasoning.")

    def __call__(self, tx, vm_status, ctx, prior: Verdict) -> Optional[Verdict]:
        if self.complete is None:
            return None
        text = self.complete(self.prompt(tx, vm_status, ctx, prior)).strip()
        d = text.split(":", 1)[0].strip().lower()
        if d not in ("allow", "hold", "block"):
            return None
        return Verdict(d, [text.split(":", 1)[-1].strip()], prior.score, "llm")


def review(tx, vm_status, ctx, vm_note="", llm: Optional[LLMJudge] = None) -> Verdict:
    """Rules first. An LLM judge may only make the decision stricter, never looser."""
    v = rules(tx, vm_status, ctx, vm_note)
    if llm and v.source != "vm":
        o = llm(tx, vm_status, ctx, v)
        order = ["allow", "hold", "block"]
        if o and order.index(o.decision) > order.index(v.decision):
            return o
    return v
