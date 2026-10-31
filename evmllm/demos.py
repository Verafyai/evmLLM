"""The ten evmLLM demo contracts with preset inputs."""
import json
from pathlib import Path

from eth_abi import decode, encode
from eth_utils import function_signature_to_4byte_selector as sel

from evmllm.host import Job

ART = json.loads((Path(__file__).resolve().parent.parent / "contracts" / "artifacts.json").read_text())
E = 10**18
USD = 10**6

# (id, contract, title, blurb, function signature, return type, preset storage, inputs[(label, args)])
DEMOS = [
    (1, "SpendCap", "Spend cap ($1B)", "Reverts when a USD amount (6 decimals) exceeds $1B.",
     "spend(uint256)", "uint256", {}, [
         ("$250,000", [250_000 * USD]), ("$999,999,999", [999_999_999 * USD]),
         ("$1,000,000,001 (over cap)", [1_000_000_001 * USD])]),
    (2, "Counter", "Counter", "SSTORE / SLOAD on one slot.", "add(uint256)", "uint256", {0: 41}, [
        ("add 1 to 41", [1]), ("add 1000 to 41", [1000])]),
    (3, "Fibonacci", "Fibonacci", "Iterative fib(n) with checked addition.", "fib(uint256)", "uint256", {}, [
        ("fib(10)", [10]), ("fib(90)", [90])]),
    (4, "Factorial", "Factorial", "n! with checked multiplication; 58! overflows and reverts.",
     "fact(uint256)", "uint256", {}, [("10!", [10]), ("57!", [57]), ("58! (overflow)", [58])]),
    (5, "GCD", "GCD", "Euclid's algorithm.", "gcd(uint256,uint256)", "uint256", {}, [
        ("gcd(1071, 462)", [1071, 462]), ("gcd(2^128, 6^40)", [2**128, 6**40])]),
    (6, "Prime", "Prime check", "Trial division up to sqrt(n).", "isPrime(uint256)", "bool", {}, [
        ("97", [97]), ("91", [91]), ("7919", [7919])]),
    (7, "Token", "Token transfer", "Balances of 8 accounts; a short balance reverts.",
     "transfer(uint256,uint256,uint256)", "uint256", {0: 100 * E, 1: 5 * E}, [
         ("account 0 sends 30 to 1", [0, 1, 30 * E]), ("account 1 sends 6 to 0 (short)", [1, 0, 6 * E])]),
    (8, "Vault", "Vault with withdrawal limit", "Per-withdrawal limit of 500 ETH.",
     "withdraw(uint256)", "uint256", {0: 2000 * E, 1: 500 * E}, [
         ("withdraw 200", [200 * E]), ("withdraw 600 (over limit)", [600 * E])]),
    (9, "Voting", "Voting tally", "8 voters, 3 candidates; double votes revert.",
     "vote(uint256,uint256)", "uint256", {0: 3, 1: 1, 3: 1 << 16}, [
         ("voter 0 votes for candidate 0", [0, 0]), ("voter 2 votes again (rejected)", [2, 1])]),
    (10, "Escrow", "Escrow release", "Releases only when the delivery code matches.",
     "release(uint256)", "uint256", {0: 25 * E, 1: 424242}, [
         ("code 424242", [424242]), ("code 111111 (condition not met)", [111111])]),
]


def calldata(sig, args):
    types = sig[sig.index("(") + 1:-1]
    types = [t for t in types.split(",") if t]
    return sel(sig) + encode(types, args)


def jobs():
    for did, name, title, blurb, sig, rtype, pre, inputs in DEMOS:
        code = bytes.fromhex(ART[name]["runtime"])
        for label, args in inputs:
            yield did, name, label, sig, rtype, Job(code, calldata(sig, args), storage=dict(pre), trace=True)


def describe(ret: bytes, status: int, rtype: str) -> str:
    if status == 1:
        if ret[:4] == bytes.fromhex("08c379a0"):
            return "revert: " + decode(["string"], ret[4:])[0]
        if ret[:4] == bytes.fromhex("4e487b71"):
            return f"revert: Panic(0x{decode(['uint256'], ret[4:])[0]:02x})"
        return "revert"
    if status == 0 and len(ret) >= 32:
        v = decode([rtype], ret[:32])[0]
        return f"returns {v}"
    return {0: "stop", 2: "out of gas", 3: "invalid opcode", 4: "stack error", 5: "bad jump",
            6: "build limit"}.get(status, "?")
