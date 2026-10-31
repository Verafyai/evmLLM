// EVMLLM demo site. Live runs use the browser reference VM (evm.js); everything labeled
// precomputed comes from data.js, which evmllm/export_site.py writes from real runs.
(function () {
  "use strict";
  const D = window.EVMLLM_DATA, E = window.EVM, J = window.Judge;
  const $ = (s, r = document) => r.querySelector(s);
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  const n = (x) => Number(x).toLocaleString("en-US");
  const hexBig = (h) => BigInt(h.startsWith("0x") ? h : "0x" + (h || "0"));
  const opsOf = (hex) => { const o = []; for (let i = 0; i < hex.length; i += 2) o.push(parseInt(hex.slice(i, i + 2), 16)); return o; };

  // ---------- ABI helpers for display ----------
  function abiString(hex) { const len = Number(hexBig(hex.slice(64, 128))); const b = E.hexToBytes(hex.slice(128, 128 + 2 * len)); return new TextDecoder().decode(b); }
  function describe(ret, status, rtype) {
    if (status === 1) {
      if (ret.startsWith("08c379a0")) return "revert: " + abiString(ret.slice(8));
      if (ret.startsWith("4e487b71")) return "revert: Panic(0x" + hexBig(ret.slice(8, 72)).toString(16).padStart(2, "0") + ")";
      return "revert";
    }
    if (status === 0 && ret.length >= 64) { const v = hexBig(ret.slice(0, 64)); return "returns " + (rtype === "bool" ? (v ? "True" : "False") : v.toString()); }
    return ({ 0: "stop", 2: "out of gas", 3: "invalid opcode", 4: "stack error", 5: "bad jump", 6: "build limit" })[status] || "?";
  }
  const runJob = (code, p, full, maxTrace) => E.run({ code: E.hexToBytes(code), calldata: E.hexToBytes(p.calldata), storage: p.storage, gas: 1000000 }, { full, maxTrace });
  const sameStorage = (a, b) => JSON.stringify(Object.entries(a).sort()) === JSON.stringify(Object.entries(b).sort());
  const SAMPLE_BYTECODE = "6007600501805f555f5260205ff3"; // add 5 and 7, store the sum, return it
  let runToken = 0;
  const delay = (ms) => new Promise((r) => setTimeout(r, ms));
  const paint = () => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
  function clock() {
    const d = new Date(), p = (x, w) => String(x).padStart(w, "0");
    return `${p(d.getHours(), 2)}:${p(d.getMinutes(), 2)}:${p(d.getSeconds(), 2)}.${p(d.getMilliseconds(), 3)}`;
  }
  function flash(el) {
    if (!el) return;
    el.classList.remove("just-ran");
    void el.offsetWidth;
    el.classList.add("just-ran");
  }
  function scrollSmall(el) {
    if (!el || !window.matchMedia("(max-width: 768px)").matches) return;
    const header = document.querySelector("header.top");
    const gap = (header ? header.offsetHeight : 0) + 8;
    const top = el.getBoundingClientRect().top + window.scrollY - gap;
    window.scrollTo(0, Math.max(0, top));
  }
  function syncHeaderOffset() {
    const h = document.querySelector("header.top");
    if (h) document.documentElement.style.setProperty("--header-h", h.offsetHeight + "px");
  }
  syncHeaderOffset();
  window.addEventListener("resize", syncHeaderOffset);

  // ---------- hero chips ----------
  const st = D.stats;
  const chips = [[D.contracts.length, "Solidity demo contracts"], [`${st.demos_ok}/${st.demos_n}`, "demo runs match py-evm"],
    [`${n(st.fuzz)}/${n(st.fuzz_n)}`, "random programs match py-evm"]];
  if (st.js_vm_n) chips.push([`${n(st.js_vm)}/${n(st.js_vm_n)}`, "browser VM cases match py-evm"]);
  if (st.circuit_runs) chips.push([`${st.circuit_match}/${st.circuit_runs}`, `runs on the weights, ${n(st.circuit_tokens)} tokens exact`]);
  $("#chips").innerHTML = chips.map(([b, s]) => `<div class="chip"><b>${b}</b><span>${s}</span></div>`).join("");

  // ---------- explainer ----------
  const X = D.explain, xc = D.contracts.find((c) => c.name === X.contract), xp = xc.presets.find((p) => p.row === X.row);
  const xr = runJob(xc.runtime, xp, true, 100000);
  const fwOps = opsOf(xp.firmware.ops), traceOps = xr.trace.map((t) => t.op);
  const agree = fwOps.length === traceOps.length && fwOps.every((o, i) => o === traceOps[i]);
  $("#ex-label").textContent = `${xc.name}.gcd(1071, 462), ${n(fwOps.length)} EVM opcodes, ${n(xp.firmware.tokens)} tokens`;
  const code = E.hexToBytes(xc.runtime), tapeIdx = {};
  let tape = "";
  for (let pc = 0; pc < code.length; pc++) {
    const op = code[pc], k = op >= 0x60 && op <= 0x7f ? op - 0x5f : 0;
    let s = `<span data-pc="${pc}" class="op">${op.toString(16).padStart(2, "0")}</span>`;
    for (let i = 1; i <= k && pc + i < code.length; i++) s += code[pc + i].toString(16).padStart(2, "0");
    tape += s; tapeIdx[pc] = true; pc += k;
  }
  $("#ex-tape").innerHTML = tape;
  const stageFor = (op) => {
    const name = E.opName(op);
    if (/^(ADD|MUL|SUB|DIV|MOD|EXP|LT|GT|SLT|SGT|EQ|ISZERO|AND|OR|XOR|NOT|BYTE|SHL|SHR|SAR)$/.test(name)) return 2;
    if (/^(SLOAD|SSTORE|MLOAD|MSTORE|MSTORE8|CALLDATALOAD|CALLDATACOPY|CODECOPY|DUP\d+|SWAP\d+|POP|PUSH\d*)$/.test(name)) return 3;
    if (/^(RETURN|REVERT|STOP)$/.test(name)) return 4;
    return 1;
  };
  let k = 0, timer = null, prevStack = [];
  function tokHtml(names) { return names.map((t) => `<span class="f-${t.split("(")[0]}">${esc(t)}</span>`).join(""); }
  function show(i) {
    k = Math.max(0, Math.min(i, fwOps.length - 1));
    const t = xr.trace[k], next = xr.trace[k + 1];
    $("#ex-pos").textContent = `step ${k + 1} / ${fwOps.length}`;
    $("#ex-pc").textContent = t.pc;
    $("#ex-op").textContent = E.opName(t.op);
    $("#ex-gas").textContent = n(t.gas);
    $("#ex-ntok").textContent = X.op_tokens[k] ? X.op_tokens[k].length : "";
    const stack = (next ? next.stack : t.stack).slice().reverse();
    $("#ex-stack").innerHTML = stack.length ? stack.map((v, j) => `<li class="${j === 0 && JSON.stringify(stack) !== JSON.stringify(prevStack) ? "fresh" : ""}">0x${v}</li>`).join("") : "<li>(empty)</li>";
    prevStack = stack;
    document.querySelectorAll("#ex-tape span.cur").forEach((s) => s.classList.remove("cur"));
    const cur = $(`#ex-tape span[data-pc="${t.pc}"]`);
    if (cur) { cur.classList.add("cur"); const tp = $("#ex-tape"); tp.scrollTop = cur.offsetTop - tp.offsetTop - 40; }
    const sg = stageFor(t.op);
    document.querySelectorAll(".stage").forEach((s) => s.classList.toggle("on", +s.dataset.stage === 0 || +s.dataset.stage === 1 || +s.dataset.stage === sg));
    const lo = Math.max(0, k - 2);
    let h = k < 3 ? `<span class="opsep">setup: ${n(X.setup_tokens)} tokens of firmware start-up (job load, jumpdest scan); first 60 shown</span>${tokHtml(X.first_tokens)}` : "";
    for (let j = lo; j <= k; j++) h += `<span class="opsep">EVM step ${j + 1}: ${E.opName(fwOps[j])} at pc ${xr.trace[j].pc}, ${X.op_tokens[j].length} tokens</span>${tokHtml(X.op_tokens[j])}`;
    const tk = $("#ex-tokens"); tk.innerHTML = h; tk.scrollTop = tk.scrollHeight;
  }
  const stop = () => { clearInterval(timer); timer = null; $("#ex-play").textContent = "Play"; };
  $("#ex-play").onclick = () => {
    if (timer) return stop();
    $("#ex-play").textContent = "Pause";
    timer = setInterval(() => { if (k >= fwOps.length - 1) return stop(); show(k + 1); }, 1000 / +$("#ex-speed").value);
  };
  $("#ex-speed").oninput = () => { if (timer) { stop(); $("#ex-play").click(); } };
  $("#ex-step").onclick = () => { stop(); show(k + 1); };
  $("#ex-reset").onclick = () => { stop(); prevStack = []; show(0); };
  $("#ex-note").innerHTML = `Opcode sequence from the browser VM ${agree ? "<span class=ok>matches</span>" : "<span class=bad>differs from</span>"} the firmware's own opcode log (${n(fwOps.length)} ops). Token names: V and F0/F1/F2 = value chunks (F marks the final one), J = jump, ST/SS = RAM value controls, QS/QD = division quotient digits, BYTE = a printed character (the firmware prints each opcode as two hex BYTEs, which is how the stream is split per EVM step). Result: <b>${esc(xp.firmware.outcome)}</b>, gas ${n(xp.firmware.gas_used)}.` +
    (xp.circuit ? ` On the compiled weights this run reproduced all ${n(xp.circuit.tokens)} tokens ${xp.circuit.match ? "<span class=ok>exactly</span>" : "<span class=bad>with a mismatch</span>"}.` : "");
  show(0);

  // ---------- demos ----------
  let cur = D.contracts[0], curP = cur.presets[0], tab = "sol";
  $("#cards").innerHTML = D.contracts.map((c, i) => `<div class="card" data-i="${i}"><div class="n">${String(i + 1).padStart(2, "0")}</div><div class="t">${esc(c.title || c.name)}</div><div class="b">${esc(c.blurb)}</div></div>`).join("");
  document.querySelectorAll(".card").forEach((el) => el.onclick = () => { cur = D.contracts[+el.dataset.i]; curP = cur.presets[0]; renderPanel(); scheduleRun({ scroll: true }); });
  function pySnippet(c, p) {
    return `# Free-run this exact job on Qwenputer's compiled weights (CPU) and check every token.
# From the repo root, after the quickstart:
#   PYTHONPATH=. python examples/run_on_weights.py --row ${p.row}
import numpy as np, torch
from chipformer.rv import core, fastexec, spec as S
from evmllm.host import Job, machine, run_ref

job = Job(code=bytes.fromhex("${c.runtime.slice(0, 48)}..."),   # ${c.name} runtime, ${c.runtime.length / 2} bytes
          calldata=bytes.fromhex("${p.calldata}"),
          storage=${JSON.stringify(Object.fromEntries(Object.entries(p.storage).map(([a, b]) => [a, b])))}, trace=True)
ref = run_ref(job)                  # bit-exact reference: ${n(p.firmware.tokens)} tokens
c = core.build()                    # RV32IM CPU compiled into weights, no training
fr = fastexec.FreeRun(c, 1, "cpu", False, sparse=True)
core.snapshot(c, machine(job))      # firmware ROM + EVM job into the DeltaNet RAM state
fr.load(0, S.tok("GO"))
out = fr.run(len(ref._toks) - 1)[0] # each predicted token is fed back as input
assert (np.asarray(out) == np.asarray(ref._toks[1:])).all()`;
  }
  function renderPanel() {
    document.querySelectorAll(".card").forEach((el) => el.classList.toggle("sel", D.contracts[+el.dataset.i] === cur));
    const f = curP.firmware;
    const circ = curP.circuit ? (curP.circuit.match ? `<span class="ok">MATCH</span>: all ${n(curP.circuit.tokens)} generated tokens equal the reference` : `<span class="bad">DIFF</span> at token ${curP.circuit.first_mismatch}`) : `<span class="note">not run on the weights in this build (CPU time). Command below.</span>`;
    $("#panel").innerHTML = `
      <h3>${esc(cur.name)} <span class="note mono">${esc(cur.sig)}</span></h3><div class="note">${esc(cur.blurb)}</div>
      <div class="presets">${cur.presets.map((p, i) => `<button type="button" class="btn ghost small ${p === curP ? "sel" : ""}" data-p="${i}">${esc(p.label)}</button>`).join("")}<button type="button" class="btn" id="run">Run</button></div>
      <div class="note mono">calldata 0x${curP.calldata}${Object.keys(curP.storage).length ? ` &nbsp; preset storage ${esc(JSON.stringify(curP.storage))}` : ""}</div>
      <div id="ran-line" class="ran-line">Running...</div>
      <div class="res" id="result-pane">
        <div class="rbox"><div class="h"><em class="tag t-browser">Live run in your browser</em></div><div id="live" class="note">Running...</div></div>
        <div class="rbox"><div class="h"><em class="tag t-fw">Recorded run</em> firmware on ref machine</div>
          <div class="outcome">${esc(f.outcome)}</div>gas ${n(f.gas_used)}, ${n(opsOf(f.ops).length)} EVM ops, ${n(f.rv_instructions)} RV32 instructions, ${n(f.tokens)} tokens<br>
          <em class="tag t-pyevm">py-evm</em> ${curP.pyevm.match ? `<span class="ok">match</span> (status, return, gas ${n(curP.pyevm.gas_used)}, storage)` : `<span class="bad">differs</span>`}</div>
        <div class="rbox"><div class="h"><em class="tag t-circuit">Recorded run</em> circuit (weights)</div>${circ}</div>
      </div>
      <div id="trace"></div>
      <div class="tabs">${[["sol", "Solidity"], ["bytecode", "Bytecode"], ["py", "Python: build weights and run"], ["run", "How to run"]].map(([k2, l]) => `<button data-t="${k2}" class="${tab === k2 ? "sel" : ""}">${l}</button>`).join("")}</div>
      <pre class="code" id="tabbody"></pre>`;
    const body = { sol: cur.solidity, bytecode: `// ${cur.runtime.length / 2} bytes of runtime bytecode (solc 0.8.24, Shanghai)\n0x${cur.runtime}`, py: pySnippet(cur, curP),
      run: `# reference machine + py-evm check for all demo inputs (fast)\nPYTHONPATH=. python tests/demos_ref.py\n\n# this input on the compiled weights (slow on CPU: ${n(f.tokens)} tokens)\nPYTHONPATH=. python examples/run_on_weights.py --row ${curP.row}\n\n# several rows batched, writes results/circuit_<rows>.json\nTHREADS=4 PYTHONPATH=. python -u tests/circuit_run.py ${curP.row}\n\n# in the browser: this page runs evm.js, a JavaScript port of the same EVM\n# (checked against py-evm on ${st.js_vm_n ? n(st.js_vm_n) : "the full"} corpus)` }[tab];
    $("#tabbody").textContent = body;
    $("#tabbody").classList.toggle("wrap", tab === "bytecode");
    document.querySelectorAll(".presets [data-p]").forEach((b) => b.onclick = () => { curP = cur.presets[+b.dataset.p]; renderPanel(); scheduleRun({ scroll: true }); });
    document.querySelectorAll(".tabs button").forEach((b) => b.onclick = () => { tab = b.dataset.t; renderPanel(); scheduleRun({ scroll: false }); });
    $("#run").onclick = () => scheduleRun({ scroll: true });
  }
  function traceTable(r, limit) {
    const rows = r.trace.slice(0, limit).map((t, i) => `<tr><td>${i + 1}</td><td>${t.pc}</td><td>${E.opName(t.op)}</td><td>${n(t.gas)}</td><td>${t.stack ? t.stack.slice(-3).reverse().map((v) => "0x" + (v.length > 18 ? v.slice(0, 8) + "..." + v.slice(-6) : v)).join(", ") : ""}</td></tr>`).join("");
    return `<div class="note">Step trace (execution gas, without the 21,000 intrinsic; ${n(r.steps)} steps${r.steps > limit ? `, first ${n(limit)} shown` : ""}), stack top first before each op</div><div class="trace"><table><tr><th>#</th><th>pc</th><th>op</th><th>gas used</th><th>stack top 3</th></tr>${rows}</table></div>`;
  }
  function runLive() {
    const t0 = performance.now(), r = runJob(cur.runtime, curP, true, 100000), ms = performance.now() - t0, f = curP.firmware;
    const out = describe(r.ret, r.status, cur.rtype);
    const ops = r.trace.map((t) => t.op), fops = opsOf(f.ops);
    const checks = [["status", r.status === f.status], ["return data", r.ret === f.ret], ["gas", r.gasUsed === f.gas_used],
      ["storage", sameStorage(r.storage, f.storage)], ["opcode sequence", ops.length === fops.length && ops.every((o, i) => o === fops[i])]];
    $("#live").innerHTML = `<div class="outcome">${esc(out)}</div>gas ${n(r.gasUsed)}, ${n(r.steps)} ops, ${ms.toFixed(1)} ms<br>${Object.keys(r.storage).length ? `storage changes ${esc(JSON.stringify(r.storage))}<br>` : ""}vs firmware: ${checks.map(([l, ok]) => `${l} <span class="${ok ? "ok" : "bad"}">${ok ? "ok" : "differs"}</span>`).join(", ")}`;
    const line = $("#ran-line");
    if (line) line.textContent = `Ran just now: ${ms.toFixed(1)} ms, result below`;
    $("#trace").innerHTML = traceTable(r, 400);
    flash($("#result-pane"));
  }
  async function scheduleRun(opts) {
    opts = opts || {};
    const token = ++runToken;
    const button = $("#run");
    if (button) { button.disabled = true; button.textContent = "Running..."; }
    await paint();
    await delay(120);
    if (token !== runToken) return;
    try { runLive(); }
    catch (e) {
      const live = $("#live");
      if (live) live.innerHTML = `<p class="bad">${esc(e.message || String(e))}</p>`;
    }
    const b = $("#run");
    if (b && token === runToken) { b.disabled = false; b.textContent = "Run"; }
    if (opts.scroll && token === runToken) scrollSmall($("#ran-line") || $("#result-pane"));
  }
  renderPanel();
  scheduleRun({ scroll: false });

  // ---------- paste your own ----------
  async function runOwn() {
    const btn = $("#own-run"), sampleBtn = $("#own-sample");
    const prev = btn.textContent;
    btn.disabled = true;
    btn.textContent = "Running...";
    if (sampleBtn) sampleBtn.disabled = true;
    $("#own-out").innerHTML = `<p class="ran-line">Running...</p>`;
    await paint();
    await delay(120);
    try {
      const raw = $("#own-code").value.replace(/^0x/, "").replace(/\s+/g, ""), cd = $("#own-cd").value.replace(/^0x/, "").replace(/\s+/g, "");
      const bad = (s) => /[^0-9a-fA-F]/.test(s) || s.length % 2;
      const show = (html) => { $("#own-out").innerHTML = html; flash($("#own-out")); };
      if (!raw) { show(`<p class="bad">Paste EVM bytecode as hex, or pick a sample</p>`); return; }
      if (bad(raw) || bad(cd)) { show(`<p class="bad">Bytecode and calldata must be even-length hex.</p>`); return; }
      if (raw.length / 2 > E.LIM.CODE_MAX) { show(`<p class="bad">${n(raw.length / 2)} bytes is over the ${n(E.LIM.CODE_MAX)}-byte limit of this build.</p>`); return; }
      if (cd.length / 2 > E.LIM.CD_MAX) { show(`<p class="bad">Calldata is over the ${E.LIM.CD_MAX}-byte limit.</p>`); return; }
      const gas = Math.min(Math.max(parseInt($("#own-gas").value, 10) || 1000000, 0), 30000000);
      const t0 = performance.now();
      const r = E.run({ code: E.hexToBytes(raw), calldata: E.hexToBytes(cd), gas }, { full: true, maxTrace: 5000 });
      const ms = performance.now() - t0;
      show(`<p class="ran-line">Ran just now: ${ms.toFixed(1)} ms, result below</p><p><em class="tag t-browser">Live run in your browser</em> <b>${esc(r.statusName)}</b>, gas ${n(r.gasUsed)}, ${n(r.steps)} steps${r.ret ? `, returned 0x${esc(r.ret)}` : ""}${Object.keys(r.storage).length ? `, storage ${esc(JSON.stringify(r.storage))}` : ""}${r.error ? ` (${esc(r.error)})` : ""}</p>
      <p class="note">This run was not executed on the transformer. Unsupported opcodes (KECCAK256, CALL, CREATE, LOG, SDIV and others) halt as invalid opcode, the same way the firmware does. To run it on the weights, use examples/run_on_weights.py.</p>${traceTable(r, 300)}`);
    } finally {
      btn.disabled = false;
      btn.textContent = prev;
      if (sampleBtn) sampleBtn.disabled = false;
    }
    scrollSmall($("#own-out"));
  }
  $("#own-run").onclick = () => runOwn();
  $("#own-sample").onclick = () => { $("#own-code").value = SAMPLE_BYTECODE; runOwn(); };

  // ---------- judgement filter ----------
  const ago = (d) => d >= 86400 ? `${Math.round(d / 86400)}d ago` : d >= 3600 ? `${Math.round(d / 3600)}h ago` : `${Math.round(d / 60)}m ago`;
  $("#scen").innerHTML = D.judge.map((s, i) => `<div class="scard" data-i="${i}"><h4>${esc(s.title)}</h4>
      <div class="d">${esc(s.contract)}.${esc(s.tx.call)}, from ${esc(s.tx.sender.slice(0, 10))}... to ${esc(s.tx.to.slice(0, 10))}...</div>
      <div class="hist">${s.ctx.history.length ? s.ctx.history.map((h) => `${ago(s.tx.t - h.t)} ${esc(h.call)} to ${esc(h.to.slice(0, 10))}...`).join("<br>") : "no history"}</div>
      <button type="button" class="btn small" style="margin-top:8px" data-label="Run VM, then review">Run VM, then review</button><div class="out"></div></div>`).join("");
  let judgeToken = 0;
  function scrollInto(el) {
    if (!el) return;
    const header = document.querySelector("header.top");
    const gap = (header ? header.offsetHeight : 0) + 8;
    const rect = el.getBoundingClientRect();
    if (rect.top >= gap && rect.bottom <= window.innerHeight && rect.top < window.innerHeight * 0.55) return;
    window.scrollTo(0, Math.max(0, rect.top + window.scrollY - gap));
  }
  async function runScenario(i, sourceBtn) {
    const token = ++judgeToken;
    const card = document.querySelector(`.scard[data-i="${i}"]`);
    const cardBtn = card.querySelector("button");
    const buttons = sourceBtn && sourceBtn !== cardBtn ? [sourceBtn, cardBtn] : [cardBtn];
    buttons.forEach((b) => { b.disabled = true; b.textContent = "Running..."; });
    const out = card.querySelector(".out");
    const view = $("#prompt-view");
    out.innerHTML = `<p class="ran-line">Running...</p>`;
    view.innerHTML = `<p class="ran-line">Running...</p>`;
    await paint();
    await delay(120);
    if (token !== judgeToken) return;
    try {
      const s = D.judge[i];
      const r = E.run({ code: E.hexToBytes(s.code), calldata: E.hexToBytes(s.calldata), storage: s.storage, gas: 1000000 }, { maxTrace: 0 });
      const note = describe(r.ret, r.status, s.rtype || "uint256");
      const rulesV = J.rules(s.tx, r.status, s.ctx, note);
      const vm = { status: r.status, gas_used: r.gasUsed, return_value: note, logs: [] };
      const prompt = J.buildPrompt(s.tx, vm, s.ctx, rulesV);
      const sim = J.simulatedReply(s.id);
      const finalV = await J.review(s.tx, r.status, s.ctx, note, sim ? J.simulatedComplete(s.id) : null, vm);
      const same = rulesV.decision === s.verdict.decision && rulesV.score === s.verdict.score && rulesV.reasons.join("|") === s.verdict.reasons.join("|");
      const vmMatch = r.status === s.vm.status && r.gasUsed === s.vm.gas_used;
      const when = clock();
      const finalNote = finalV.source === "llm"
        ? "The simulated answer was stricter, so it stands."
        : rulesV.source === "vm"
          ? "The VM already blocked this. The simulated answer is shown and cannot loosen that block."
          : "The simulated answer is not stricter than the rule check, so the rule decision stands.";
      out.innerHTML = `<div class="ran-line">Reviewed just now at ${when}</div>
        <div><b>1. VM (exact):</b> ${esc(note)}, gas ${n(r.gasUsed)} ${vmMatch ? "<span class=ok>(matches the recorded run)</span>" : ""}</div>
        <div><b>2. Review (rules, heuristic outside the weights):</b> <span class="v ${rulesV.decision}">${rulesV.decision}</span> score ${rulesV.score}${rulesV.source === "vm" ? " (decided by the VM)" : ""}<ul>${rulesV.reasons.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>
        <div><b>3. Simulated model output:</b> <span class="v ${sim.decision}">${sim.decision}</span> ${esc(sim.reason)}</div>
        <div class="note">${esc(sim.label)}. The full prompt is below. Recorded rule result: ${esc(s.verdict.decision)} ${s.verdict.score} ${same ? "<span class=ok>(same)</span>" : "<span class=bad>(different)</span>"}.</div>`;
      view.innerHTML = `<div class="ran-line">Reviewed just now at ${when}</div>
        <h3>Prompt</h3>
        <p class="note">Built from the live VM result and this scenario's history. The addresses are placeholders.</p>
        <pre class="prompt-text"></pre>
        <h3>Simulated model output</h3>
        <p class="sim-banner"></p>
        <pre class="sim-json"></pre>
        <p><b>Rule check (heuristic outside the weights):</b> <span class="v ${rulesV.decision}">${esc(rulesV.decision)}</span> score ${rulesV.score}</p>
        <p><b>Final decision:</b> <span class="v ${finalV.decision}">${esc(finalV.decision)}</span> ${esc(finalNote)}</p>`;
      view.querySelector(".prompt-text").textContent = prompt;
      view.querySelector(".sim-banner").textContent = sim.label;
      view.querySelector(".sim-json").textContent = sim.json;
      flash(out);
      flash(view);
    } catch (e) {
      out.innerHTML = `<p class="bad">${esc(e.message || String(e))}</p>`;
      view.innerHTML = `<p class="bad">${esc(e.message || String(e))}</p>`;
    } finally {
      if (token === judgeToken) buttons.forEach((b) => { b.disabled = false; b.textContent = b.dataset.label; });
    }
    if (token === judgeToken) scrollInto(view);
  }
  document.querySelectorAll(".scard").forEach((el) => {
    el.querySelector("button").onclick = () => runScenario(+el.dataset.i, el.querySelector("button"));
  });
  const runExample = (id, btn) => {
    const i = D.judge.findIndex((s) => s.id === id);
    if (i >= 0) runScenario(i, btn);
  };
  $("#ex-call").onclick = () => runExample(J.EXAMPLE_CALL, $("#ex-call"));
  $("#ex-withdraw").onclick = () => runExample(J.EXAMPLE_WITHDRAW, $("#ex-withdraw"));

  // ---------- results ----------
  const rows = [["Demo runs (23 inputs, 10 contracts), firmware vs py-evm", `${st.demos_ok}/${st.demos_n}`, "status, return data, gas, storage"],
    ["Random programs (fuzz), firmware vs py-evm", `${n(st.fuzz)}/${n(st.fuzz_n)}`, "generated stack, memory, storage, control-flow programs"],
    ["256-bit DIV/MOD vs Python bigints", st.div_n ? `${st.div}/${st.div_n}` : "", "Knuth algorithm D on 32-bit limbs"]];
  if (st.js_vm_n) rows.push(["Browser VM (evm.js) vs py-evm corpus", `${n(st.js_vm)}/${n(st.js_vm_n)}`, "demos plus fuzz cases, exported from py-evm"]);
  rows.push(["Runs on the compiled weights (fastexec, CPU)", st.circuit_runs ? `${st.circuit_match}/${st.circuit_runs}` : "pending", st.circuit_runs ? `${n(st.circuit_tokens)} generated tokens, every one equal to the reference` : "see README"]);
  $("#results-table").innerHTML = `<table><tr><th>Test</th><th>Pass</th><th>What is compared</th></tr>${rows.map((r) => `<tr><td>${r[0]}</td><td><b>${r[1]}</b></td><td>${r[2]}</td></tr>`).join("")}</table>` +
    (D.circuit_runs && D.circuit_runs.length ? `<h3 style="margin-top:18px">Runs on the weights</h3><table><tr><th>Contract</th><th>Input</th><th>Tokens</th><th>Result</th></tr>${D.circuit_runs.map((r) => `<tr><td>${esc(r.contract)}</td><td>${esc(r.input)}</td><td>${n(r.tokens)}</td><td>${r.match ? "<span class=ok>exact match</span>" : `<span class=bad>first mismatch at ${r.first_mismatch}</span>`}</td></tr>`).join("")}</table>` : "");

  // ---------- code section ----------
  $("#code-py").textContent = `# examples/run_on_weights.py (abridged). Qwenputer's compiler sets every weight;
# nothing here is trained. The EVM is C firmware running on that CPU.
import numpy as np, torch
from chipformer.rv import core, fastexec, spec as S
from evmllm.demos import jobs
from evmllm.host import machine, run_ref

*_, job = list(jobs())[10]                 # GCD: gcd(1071, 462)
ref = run_ref(job)                         # bit-exact reference, ${n(xp.firmware.tokens)} tokens

c = core.build()                           # 11 chip layers: attention fetch/decode,
                                           # FFN ALU, Gated DeltaNet RAM
fr = fastexec.FreeRun(c, 1, "cpu", False, sparse=True)
core.snapshot(c, machine(job))             # firmware ROM + job block -> RAM state
fr.load(0, S.tok("GO"))
out = fr.run(len(ref._toks) - 1)[0]        # autoregressive: output -> next input
assert (np.asarray(out) == np.asarray(ref._toks[1:])).all()`;
  $("#code-sh").textContent = `git clone https://github.com/Verafyai/evmLLM && cd evmLLM
# Qwenputer (MIT) at the tested commit, with a small CPU patch
git clone https://github.com/divagr18/qwenputer /tmp/qwp
git -C /tmp/qwp checkout f07a0b2 && git -C /tmp/qwp apply $PWD/qwenputer-cpu.patch
sudo apt install gcc-riscv64-unknown-elf
python3 -m venv .venv && . .venv/bin/activate
python3 -m pip install -r requirements.txt -e /tmp/qwp/experimental/riscv
./firmware/build.sh                       # C EVM -> RV32IM ELF
PYTHONPATH=. python tests/demos_ref.py    # 23 demo runs vs py-evm
PYTHONPATH=. python tests/fuzz.py 2000    # random programs vs py-evm
PYTHONPATH=. python examples/run_on_weights.py   # on the weights`;
})();
