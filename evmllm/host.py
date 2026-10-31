"""Host side: pack an EVM job into the firmware's RAM, run it on the Qwenputer reference RV32
machine (ref.py) or on the compiled circuit (fastexec), parse the printed result, and run the
same job on py-evm (Shanghai) as the reference EVM."""
import struct
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ELF = ROOT / "firmware" / "evm.elf"
CODE_MAX, CD_MAX, NPRE = 3072, 256, 8
MAGIC = 0x45564D31
STATUS = {0: "success", 1: "revert", 2: "out of gas", 3: "invalid opcode", 4: "stack error",
          5: "bad jump", 6: "build limit"}


@dataclass
class Job:
    code: bytes
    calldata: bytes = b""
    gas: int = 1_000_000
    storage: dict = field(default_factory=dict)  # preset slot -> value
    caller: int = 0xCA11E7
    value: int = 0
    address: int = 0xC0DE
    trace: bool = False


@dataclass
class Result:
    status: int
    gas_used: int
    ret: bytes
    storage: dict  # changed slots only
    ops: list = field(default_factory=list)  # opcode trace (firmware trace mode)
    rv_instructions: int = 0
    tokens: int = 0

    def key(self):
        return (self.status, self.gas_used, self.ret if self.status <= 1 else b"",
                tuple(sorted(self.storage.items())) if self.status == 0 else ())


def u256(v: int) -> bytes:
    v %= 1 << 256
    return b"".join(struct.pack("<I", (v >> (32 * i)) & 0xFFFFFFFF) for i in range(8))


def job_bytes(j: Job) -> bytes:
    assert len(j.code) <= CODE_MAX and len(j.calldata) <= CD_MAX and len(j.storage) <= NPRE
    b = struct.pack("<6I", MAGIC, len(j.code), len(j.calldata), int(j.trace), j.gas, len(j.storage))
    b += u256(j.caller) + u256(j.value) + u256(j.address)
    b += j.code.ljust(CODE_MAX, b"\0") + j.calldata.ljust(CD_MAX, b"\0")
    ks = list(j.storage.items()) + [(0, 0)] * (NPRE - len(j.storage))
    b += b"".join(u256(k) for k, _ in ks) + b"".join(u256(v) for _, v in ks)
    return b


_prog = None
_job_addr = None


def program():
    global _prog, _job_addr
    if _prog is None:
        from chipformer.rv import elf
        _prog = elf.program(ELF)
        nm = subprocess.run(["riscv64-unknown-elf-nm", str(ELF)], capture_output=True, text=True).stdout
        _job_addr = next(int(l.split()[0], 16) for l in nm.splitlines() if l.endswith(" job"))
    return _prog, _job_addr


def machine(j: Job):
    """A fresh ref.Machine with the job in RAM."""
    import copy
    from chipformer.rv import ref
    prog, addr = program()
    p = copy.copy(prog)
    data = bytearray(prog.data)
    jb = job_bytes(j)
    assert int.from_bytes(data[addr:addr + 4], "little") == MAGIC
    data[addr:addr + len(jb)] = jb
    p.data = bytes(data)
    return ref.Machine.from_program(p)


def parse(out: str, trace: bool) -> Result:
    lines = out.split("\n")
    head = lines[0]
    ops = []
    i = head.index("s")
    if trace:
        t = head[:i]
        ops = [int(t[k:k + 2], 16) for k in range(0, len(t), 2)]
    parts = head[i:].split(" ")
    st = int(parts[0][1:], 16)
    gas = int(parts[1][1:], 16)
    ret = bytes.fromhex(parts[2][1:]) if len(parts) > 2 else b""
    stor = {}
    for l in lines[1:]:
        if l.startswith("k"):
            k, v = l[1:].split("=")
            stor[int(k, 16)] = int(v, 16)
    return Result(st, gas, ret, stor, ops)


def run_ref(j: Job, max_tokens=10**8) -> Result:
    """The firmware on the Qwenputer reference RV32 machine (bit-exact model of the circuit)."""
    from chipformer.rv import ref
    m = machine(j)
    toks = m.run(max_tokens)
    assert m.fault is None, f"RV fault {m.fault}"
    r = parse(bytes(m.out).decode(), j.trace)
    r.rv_instructions = ref.trace_stats(toks)["instructions"]
    r.tokens = len(toks)
    r._toks = toks
    return r


# ---------------- reference EVM: py-evm ----------------
_chain = None


def run_pyevm(j: Job, extra_keys=()) -> Result:
    from eth import constants
    from eth.chains.base import MiningChain
    from eth.db.atomic import AtomicDB
    from eth.vm.forks import ShanghaiVM
    from eth.vm.message import Message
    global _chain
    if _chain is None:
        klass = MiningChain.configure(vm_configuration=((0, ShanghaiVM),), chain_id=1)
        _chain = klass.from_genesis(AtomicDB(), {
            "difficulty": 0, "gas_limit": 30_000_000, "timestamp": 0, "nonce": b"\0" * 8,
            "extra_data": b"", "coinbase": constants.ZERO_ADDRESS, "base_fee_per_gas": 0})
    vm = _chain.get_vm()
    state = vm.state
    addr = j.address.to_bytes(20, "big")
    sender = j.caller.to_bytes(20, "big")
    state.set_code(addr, j.code)
    state.set_balance(sender, j.value + 10**18)
    for k, v in j.storage.items():
        state.set_storage(addr, k, v)
    state.persist()  # makes the preset values the "original" values
    state.lock_changes()
    msg = Message(gas=j.gas, to=addr, sender=sender, value=j.value, data=j.calldata, code=j.code,
                  should_transfer_value=False)
    tx_ctx = state.get_transaction_context_class()(gas_price=1, origin=sender)
    comp = state.computation_class.apply_message(state, msg, tx_ctx)
    if comp.is_success:
        st = 0
    elif comp.is_error and type(comp.error).__name__ == "Revert":
        st = 1
    else:
        name = type(comp.error).__name__
        st = {"OutOfGas": 2, "InsufficientStack": 4, "FullStack": 4, "InvalidJumpDestination": 5,
              "InvalidInstruction": 3, "StackDepthLimit": 4, "WriteProtection": 3}.get(name, 3)
    used = j.gas - comp.get_gas_remaining() if st <= 1 else j.gas
    stor = {}
    if st == 0:
        keys = set(j.storage) | set(extra_keys) | candidate_slots(j)
        for k in keys:
            v = state.get_storage(addr, k)
            if v != j.storage.get(k, 0):
                stor[k] = v
    return Result(st, used, bytes(comp.output) if st <= 1 else b"", stor)


def candidate_slots(j: Job) -> set:
    """Slots read back from py-evm: 0..63, the caller, and every PUSH constant in the code."""
    ks = set(range(64)) | {j.caller, j.address}
    c, i = j.code, 0
    while i < len(c):
        op = c[i]
        if 0x60 <= op <= 0x7F:
            n = op - 0x5F
            ks.add(int.from_bytes(c[i + 1:i + 1 + n], "big"))
            i += n
        i += 1
    for k in range(0, len(j.calldata), 32):  # calldata words too (addresses, keys)
        ks.add(int.from_bytes(j.calldata[k:k + 32].ljust(32, b"\0"), "big"))
        if k + 4 <= len(j.calldata):
            ks.add(int.from_bytes(j.calldata[k + 4:k + 36].ljust(32, b"\0"), "big"))
    return ks
