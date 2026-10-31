// EVMLLM browser reference VM: a JavaScript port of firmware/evm.c (same opcode subset,
// same Shanghai gas rules, same build limits). This runs in your browser. It is NOT the
// transformer; it exists so you can run and check contracts live.
(function (root) {
  "use strict";
  const M = 1n << 256n, MASK = M - 1n, SIGN = 1n << 255n;
  const LIM = { CODE_MAX: 3072, CD_MAX: 256, MEM_MAX: 2048, STK_MAX: 96, SLOTS: 48 };
  const STATUS = ["success", "revert", "out of gas", "invalid opcode", "stack error", "bad jump", "build limit"];
  const NAMES = {
    0x00: "STOP", 0x01: "ADD", 0x02: "MUL", 0x03: "SUB", 0x04: "DIV", 0x06: "MOD", 0x0a: "EXP",
    0x10: "LT", 0x11: "GT", 0x12: "SLT", 0x13: "SGT", 0x14: "EQ", 0x15: "ISZERO", 0x16: "AND",
    0x17: "OR", 0x18: "XOR", 0x19: "NOT", 0x1a: "BYTE", 0x1b: "SHL", 0x1c: "SHR", 0x1d: "SAR",
    0x30: "ADDRESS", 0x33: "CALLER", 0x34: "CALLVALUE", 0x35: "CALLDATALOAD", 0x36: "CALLDATASIZE",
    0x37: "CALLDATACOPY", 0x38: "CODESIZE", 0x39: "CODECOPY", 0x3d: "RETURNDATASIZE", 0x50: "POP",
    0x51: "MLOAD", 0x52: "MSTORE", 0x53: "MSTORE8", 0x54: "SLOAD", 0x55: "SSTORE", 0x56: "JUMP",
    0x57: "JUMPI", 0x58: "PC", 0x59: "MSIZE", 0x5a: "GAS", 0x5b: "JUMPDEST", 0x5f: "PUSH0",
    0xf3: "RETURN", 0xfd: "REVERT",
  };
  for (let i = 1; i <= 32; i++) NAMES[0x5f + i] = "PUSH" + i;
  for (let i = 1; i <= 16; i++) { NAMES[0x7f + i] = "DUP" + i; NAMES[0x8f + i] = "SWAP" + i; }
  const opName = (op) => NAMES[op] || "INVALID(0x" + op.toString(16).padStart(2, "0") + ")";

  const toSigned = (a) => (a & SIGN ? a - M : a);
  const hexToBytes = (h) => {
    h = (h || "").replace(/^0x/, "").replace(/\s+/g, "");
    if (h.length % 2 || /[^0-9a-fA-F]/.test(h)) throw new Error("not valid hex");
    const b = new Uint8Array(h.length / 2);
    for (let i = 0; i < b.length; i++) b[i] = parseInt(h.substr(2 * i, 2), 16);
    return b;
  };
  const bytesToHex = (b) => Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
  const beToBig = (b, o, n) => { let v = 0n; for (let i = 0; i < n; i++) v = (v << 8n) | BigInt(o + i < b.length ? b[o + i] : 0); return v; };

  class Halt { constructor(s) { this.s = s; } }

  // job: {code, calldata (Uint8Array), gas, storage: {key(BigInt|string): val}, caller, value, address}
  function run(job, opts) {
    opts = opts || {};
    const maxTrace = opts.maxTrace ?? 50000;
    const code = job.code, n = code.length, cd = job.calldata || new Uint8Array(0);
    if (n > LIM.CODE_MAX || cd.length > LIM.CD_MAX) return { status: 6, statusName: STATUS[6], gasUsed: Number(job.gas || 1000000), ret: "", storage: {}, trace: [], steps: 0, error: "code or calldata over the build limit" };
    const gasLimit = BigInt(job.gas ?? 1000000);
    let gas = gasLimit;
    const stk = [], mem = new Uint8Array(LIM.MEM_MAX);
    let msizeWords = 0, pc = 0, steps = 0, retOff = 0, retLen = 0;
    const slots = new Map(); // key string -> {key, val, orig, warm, dirty}
    const slot = (k) => {
      const ks = k.toString();
      let s = slots.get(ks);
      if (!s) { if (slots.size === LIM.SLOTS) throw new Halt(6); s = { key: k, val: 0n, orig: 0n, warm: false, dirty: false }; slots.set(ks, s); }
      return s;
    };
    for (const [k, v] of Object.entries(job.storage || {})) { const s = slot(BigInt(k)); s.val = s.orig = BigInt(v) & MASK; }
    const jd = new Uint8Array(n);
    for (let i = 0; i < n; i++) { const op = code[i]; if (op === 0x5b) jd[i] = 1; else if (op >= 0x60 && op <= 0x7f) i += op - 0x5f; }
    const use = (g) => { g = BigInt(g); if (g > gas) { gas = 0n; throw new Halt(2); } gas -= g; };
    const need = (k) => { if (stk.length < k) throw new Halt(4); };
    const room = (k) => { if (stk.length + k > LIM.STK_MAX) throw new Halt(stk.length + k > 1024 ? 4 : 6); };
    const memcost = (w) => 3 * w + Math.floor((w * w) / 512);
    const fits32 = (a) => a < 0x100000000n;
    const expand = (off, len) => {
      if (len === 0n) return;
      if (!fits32(off) || !fits32(len) || off > BigInt(LIM.MEM_MAX) || len > BigInt(LIM.MEM_MAX) || off + len > BigInt(LIM.MEM_MAX)) {
        if (!fits32(off) || !fits32(len) || off + len > 0x100000n) throw new Halt(2);
        throw new Halt(6);
      }
      const w = Math.floor((Number(off + len) + 31) / 32);
      if (w > msizeWords) { use(memcost(w) - memcost(msizeWords)); msizeWords = w; }
    };
    const pop = () => stk.pop();
    const trace = [];
    let status;
    try {
      for (;;) {
        const op = pc < n ? code[pc] : 0;
        steps++;
        if (trace.length < maxTrace) {
          const e = { pc, op, gas: Number(gasLimit - gas) };
          if (opts.full) e.stack = stk.slice(-6).map((x) => x.toString(16));
          trace.push(e);
        }
        pc++;
        if (op >= 0x60 && op <= 0x7f) { const k = op - 0x5f; use(3); room(1); stk.push(beToBig(code, pc, k)); pc += k; continue; }
        if (op >= 0x80 && op <= 0x8f) { const k = op - 0x7f; use(3); need(k); room(1); stk.push(stk[stk.length - k]); continue; }
        if (op >= 0x90 && op <= 0x9f) { const k = op - 0x8f; use(3); need(k + 1); const L = stk.length - 1, t = stk[L]; stk[L] = stk[L - k]; stk[L - k] = t; continue; }
        let a, b, c;
        switch (op) {
          case 0x00: throw new Halt(0);
          case 0x01: use(3); need(2); a = pop(); b = pop(); stk.push((a + b) & MASK); break;
          case 0x02: use(5); need(2); a = pop(); b = pop(); stk.push((a * b) & MASK); break;
          case 0x03: use(3); need(2); a = pop(); b = pop(); stk.push((a - b + M) & MASK); break;
          case 0x04: use(5); need(2); a = pop(); b = pop(); stk.push(b === 0n ? 0n : a / b); break;
          case 0x06: use(5); need(2); a = pop(); b = pop(); stk.push(b === 0n ? 0n : a % b); break;
          case 0x0a: { need(2); a = pop(); b = pop(); const bl = Math.ceil(b.toString(2).length * (b === 0n ? 0 : 1) / 8); use(10 + 50 * bl);
            let r = 1n, base = a, e = b; while (e > 0n) { if (e & 1n) r = (r * base) & MASK; base = (base * base) & MASK; e >>= 1n; } stk.push(r); break; }
          case 0x10: use(3); need(2); a = pop(); b = pop(); stk.push(a < b ? 1n : 0n); break;
          case 0x11: use(3); need(2); a = pop(); b = pop(); stk.push(a > b ? 1n : 0n); break;
          case 0x12: use(3); need(2); a = pop(); b = pop(); stk.push(toSigned(a) < toSigned(b) ? 1n : 0n); break;
          case 0x13: use(3); need(2); a = pop(); b = pop(); stk.push(toSigned(a) > toSigned(b) ? 1n : 0n); break;
          case 0x14: use(3); need(2); a = pop(); b = pop(); stk.push(a === b ? 1n : 0n); break;
          case 0x15: use(3); need(1); a = pop(); stk.push(a === 0n ? 1n : 0n); break;
          case 0x16: use(3); need(2); a = pop(); b = pop(); stk.push(a & b); break;
          case 0x17: use(3); need(2); a = pop(); b = pop(); stk.push(a | b); break;
          case 0x18: use(3); need(2); a = pop(); b = pop(); stk.push(a ^ b); break;
          case 0x19: use(3); need(1); a = pop(); stk.push(MASK ^ a); break;
          case 0x1a: use(3); need(2); a = pop(); b = pop(); stk.push(a < 32n ? (b >> (8n * (31n - a))) & 255n : 0n); break;
          case 0x1b: use(3); need(2); a = pop(); b = pop(); stk.push(a < 256n ? (b << a) & MASK : 0n); break;
          case 0x1c: use(3); need(2); a = pop(); b = pop(); stk.push(a < 256n ? b >> a : 0n); break;
          case 0x1d: use(3); need(2); a = pop(); b = pop(); { const s = toSigned(b); stk.push((a < 256n ? s >> a : (s < 0n ? -1n : 0n)) & MASK); } break;
          case 0x30: use(2); room(1); stk.push(BigInt(job.address ?? 0xc0de)); break;
          case 0x33: use(2); room(1); stk.push(BigInt(job.caller ?? 0xca11e7)); break;
          case 0x34: use(2); room(1); stk.push(BigInt(job.value ?? 0)); break;
          case 0x35: use(3); need(1); a = pop(); stk.push(a >= BigInt(cd.length) ? 0n : beToBig(cd, Number(a), 32)); break;
          case 0x36: use(2); room(1); stk.push(BigInt(cd.length)); break;
          case 0x37: case 0x39: { need(3); const d = pop(), s = pop(), l = pop();
            if (!fits32(l)) throw new Halt(2);
            use(3 + 3 * Math.floor((Number(l) + 31) / 32)); expand(d, l);
            const src = op === 0x37 ? cd : code, lim = src.length;
            for (let j = 0; j < Number(l); j++) { const p = s + BigInt(j); mem[Number(d) + j] = p < BigInt(lim) ? src[Number(p)] : 0; }
            break; }
          case 0x38: use(2); room(1); stk.push(BigInt(n)); break;
          case 0x3d: use(2); room(1); stk.push(0n); break;
          case 0x50: use(2); need(1); pop(); break;
          case 0x51: use(3); need(1); a = pop(); expand(a, 32n); stk.push(beToBig(mem, Number(a), 32)); break;
          case 0x52: use(3); need(2); a = pop(); b = pop(); expand(a, 32n); for (let k = 0; k < 32; k++) mem[Number(a) + k] = Number((b >> BigInt(8 * (31 - k))) & 255n); break;
          case 0x53: use(3); need(2); a = pop(); b = pop(); expand(a, 1n); mem[Number(a)] = Number(b & 255n); break;
          case 0x54: { need(1); a = pop(); const s = slot(a); use(s.warm ? 100 : 2100); s.warm = true; stk.push(s.val); break; }
          case 0x55: { need(2); if (gas <= 2300n) throw new Halt(2); a = pop(); b = pop(); const s = slot(a);
            let cst = s.warm ? 0 : 2100; s.warm = true;
            if (s.val === b) cst += 100; else if (s.orig === s.val) cst += s.orig === 0n ? 20000 : 2900; else cst += 100;
            use(cst); s.val = b; s.dirty = true; break; }
          case 0x56: use(8); need(1); a = pop(); if (a >= BigInt(n) || !jd[Number(a)]) throw new Halt(5); pc = Number(a); break;
          case 0x57: use(10); need(2); a = pop(); b = pop(); if (b !== 0n) { if (a >= BigInt(n) || !jd[Number(a)]) throw new Halt(5); pc = Number(a); } break;
          case 0x58: use(2); room(1); stk.push(BigInt(pc - 1)); break;
          case 0x59: use(2); room(1); stk.push(BigInt(msizeWords * 32)); break;
          case 0x5a: use(2); room(1); stk.push(gas); break;
          case 0x5b: use(1); break;
          case 0x5f: use(2); room(1); stk.push(0n); break;
          case 0xf3: case 0xfd: need(2); a = pop(); b = pop(); stk.push(b, a); expand(a, b);
            retOff = fits32(b) && b !== 0n ? Number(a) : 0; retLen = Number(b & 0xffffffffn); throw new Halt(op === 0xf3 ? 0 : 1);
          default: throw new Halt(3);
        }
      }
    } catch (e) {
      if (!(e instanceof Halt)) throw e;
      status = e.s;
    }
    const used = status >= 2 ? gasLimit : gasLimit - gas;
    const storage = {};
    if (status === 0) for (const s of slots.values()) if (s.dirty && s.val !== s.orig) storage["0x" + s.key.toString(16)] = "0x" + s.val.toString(16);
    return { status, statusName: STATUS[status], gasUsed: Number(used), ret: status <= 1 ? bytesToHex(mem.slice(retOff, retOff + retLen)) : "", storage, trace, steps };
  }

  const api = { run, opName, hexToBytes, bytesToHex, LIM, STATUS, NAMES };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.EVM = api;
})(typeof self !== "undefined" ? self : this);
