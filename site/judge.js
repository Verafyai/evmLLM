// EVMLLM judgement filter, a JavaScript port of evmllm/judge.py.
// Rule review runs AFTER the VM. It is ordinary code, NOT part of the transformer weights.
// buildPrompt and simulatedReply must stay character-identical to the Python versions.
// tests/test_judge_prompt.py checks that.
(function (root) {
  "use strict";
  const VM_STATUS = ["success", "revert", "out of gas", "invalid opcode", "stack error", "bad jump", "build limit"];
  const fmt = (x) => x >= 1e9 ? (x / 1e9).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + "B"
    : x >= 1e6 ? (x / 1e6).toLocaleString("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 }) + "M"
    : Number(x.toPrecision(4)).toLocaleString("en-US", { maximumFractionDigits: 4 });
  const median = (a) => { const s = [...a].sort((x, y) => x - y), m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };

  function rules(tx, vmStatus, ctx, vmNote) {
    if (vmStatus !== 0) return { decision: "block", reasons: [`VM: ${vmNote || VM_STATUS[vmStatus]}; the code itself rejected it, nothing to review`], score: 100, source: "vm" };
    const reasons = []; let score = 0;
    const hist = ctx.history || [], amounts = hist.map((h) => h.amount);
    const typical = amounts.length ? median(amounts) : null;
    const known = new Set(ctx.known || []);
    const newDest = !known.has(tx.to) && hist.every((h) => h.to !== tx.to);
    if (typical) {
      const ratio = tx.amount / typical;
      const r0 = Math.round(ratio).toLocaleString("en-US");
      if (ratio >= 100) { score += 45; reasons.push(`${r0}x the usual size (median ${fmt(typical)} ${tx.unit})`); }
      else if (ratio >= 20) { score += 30; reasons.push(`${r0}x the usual size (median ${fmt(typical)} ${tx.unit})`); }
      else if (ratio >= 5) { score += 10; reasons.push(`${ratio.toFixed(1)}x the usual size`); }
    } else if (tx.amount > 0) { score += 10; reasons.push("no history for this account"); }
    if (newDest) { score += 20; reasons.push(`destination ${tx.to} never seen before`); }
    const win = hist.filter((h) => tx.t - h.t <= 600);
    if (win.length >= 3) {
      const moved = win.reduce((s, h) => s + h.amount, 0) + tx.amount;
      score += 25; reasons.push(`${win.length + 1} withdrawals in 10 minutes (${fmt(moved)} ${tx.unit} total)`);
      if (ctx.balance && moved >= 0.5 * (ctx.balance + win.reduce((s, h) => s + h.amount, 0))) { score += 25; reasons.push("pattern would drain over half the balance"); }
    }
    if (ctx.cap && tx.amount >= 0.9 * ctx.cap) { score += 20; reasons.push(`${Math.round(100 * tx.amount / ctx.cap)}% of the contract cap in one call`); }
    if (ctx.balance && tx.amount >= 0.9 * ctx.balance) { score += 15; reasons.push("empties 90%+ of the balance"); }
    const decision = score >= 60 ? "block" : score >= 30 ? "hold" : "allow";
    if (!reasons.length) reasons.push("in line with history, known destination");
    return { decision, reasons, score: Math.min(score, 100), source: "rules" };
  }

  // Obvious stand-in contract addresses (not real accounts). Same map as evmllm/judge.py.
  const CONTRACT_ADDRESS = {
    Vault: "0xc0ffee0000000000000000000000000000c0ffee",
    Token: "0xc0de00000000000000000000000000000000c0de",
    SpendCap: "0xca9000000000000000000000000000000000ca90",
  };
  const CONTRACT_ADDRESS_FALLBACK = "0xc0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0";
  const SYSTEM_ROLE = "You are the transaction reviewer for a wallet. The VM has already run the contract exactly. Decide allow, hold or block based on everything you know about the sender and receiver and the run rules. You may only make the decision stricter, never looser than the rule verdict.";
  const RULES_TEXT = "Compare the amount with the usual size (the median of this sender's past calls). 100 times the usual size or more adds 45. 20 times or more adds 30. 5 times or more adds 10. No history adds 10.\nA destination that has never been seen adds 20.\n4 or more withdrawals within 10 minutes adds 25. If that burst would drain over half the balance, add 25 more.\n90 percent or more of the contract cap in one call adds 20.\nEmptying 90 percent or more of the balance adds 15.\nA score of 60 or more blocks. A score of 30 or more holds. Otherwise the rules allow.\nIf the VM status is not success, the decision is block, because the code itself rejected the call.\nThe reviewer may only make the decision stricter, never looser than the rule verdict.";
  const REPLY_FORMAT = 'Reply with JSON only, one object: {"decision": "allow" or "hold" or "block", "reason": "one sentence"}';
  const SIMULATED_LABEL = "SIMULATED model output, no real LLM was called";
  const EXAMPLE_CALL = "known-transfer";
  const EXAMPLE_WITHDRAW = "whale-new-dest";
  const SIMULATED_REPLIES = {
    "whale-new-dest": { decision: "block", reason: "Usual withdrawals from this sender are about 4 ETH to one known address. This call withdraws 450 ETH, about 111 times that size, and the destination has never been seen, so it should be blocked." },
    "known-transfer": { decision: "allow", reason: "This sender has transferred about 2 to 4 tokens to this same destination several times. A transfer of 3 tokens matches that history, and the run rules allow it." },
    "rapid-drain": { decision: "block", reason: "Three withdrawals of 490 ETH already happened in the last few minutes, and this call takes another 490 ETH. Together that is 1,960 ETH and would drain most of the balance, so it should be blocked." },
    "near-cap": { decision: "hold", reason: "Past spends were about 35 to 42 million USD. This call spends 950 million USD, about 24 times the usual size and 95 percent of the cap, so it should be held for a person to review." },
    "over-cap": { decision: "block", reason: "The VM result is a revert because the call exceeds the cap. The code itself rejected it, so the decision is block." },
  };

  function shortAddr(addr) {
    const a = String(addr).trim();
    if (a.length <= 12) return a;
    return a.slice(0, 6) + "..." + a.slice(-4);
  }
  function relTime(seconds) {
    seconds = Math.abs(Math.trunc(Number(seconds)));
    if (seconds >= 86400) return Math.floor((seconds + 43200) / 86400) + "d earlier";
    if (seconds >= 3600) return Math.floor((seconds + 1800) / 3600) + "h earlier";
    return Math.floor((seconds + 30) / 60) + "m earlier";
  }
  function contractAddress(name) { return CONTRACT_ADDRESS[String(name)] || CONTRACT_ADDRESS_FALLBACK; }
  function dumpsReply(decision, reason) {
    return '{"decision": "' + decision + '", "reason": "' + reason + '"}';
  }
  function money(value, unit) {
    if (value === null || value === undefined) return "not recorded";
    return fmt(value) + " " + unit;
  }
  function histBlock(rows, txT) {
    if (!rows || !rows.length) return "- none";
    return rows.map((h) => `- ${h.call} from ${h.sender} to ${h.to}, ${fmt(h.amount)} ${h.unit}, ${relTime(txT - h.t)}`).join("\n");
  }
  function buildPrompt(tx, vm, ctx, prior) {
    const call = String(tx.call || "");
    const kind = call.split("(")[0].trim().toLowerCase();
    const caddr = contractAddress(tx.contract);
    const wallet = shortAddr(tx.sender);
    const line1 = `Wallet ${wallet} has requested calling contract ${shortAddr(caddr)} function ${call}.`;
    const line2 = kind === "withdraw"
      ? `Wallet ${wallet} would like to withdraw ${fmt(tx.amount)} ${tx.unit}. Based on everything you know about the context and the run rules, should that be allowed?`
      : `Wallet ${wallet} has requested calling this smart contract. Based on everything you know about the context and the run rules, is this allowed?`;
    const status = Number(vm.status);
    const statusWord = status >= 0 && status < VM_STATUS.length ? VM_STATUS[status] : String(status);
    const ret = vm.return_value || "none";
    const gasS = vm.gas_used === null || vm.gas_used === undefined ? "not recorded" : Number(vm.gas_used).toLocaleString("en-US");
    const logs = vm.logs || [];
    const logsS = logs.length ? logs.map((item) => "- " + item).join("\n") : "none";
    const hist = ctx.history || [];
    const senderRows = hist.filter((h) => String(h.sender) === String(tx.sender));
    const receiverRows = hist.filter((h) => String(h.to) === String(tx.to));
    const known = [...(ctx.known || [])].map(String).sort();
    const reasons = prior.reasons || [];
    const reasonText = reasons.length ? reasons.join("; ") : "none";
    const lines = [
      "Role",
      SYSTEM_ROLE,
      "",
      "Transaction request",
      line1,
      line2,
      "",
      "VM result (exact)",
      "The VM circuits already ran this contract. This result is exact. It is handed over here, into this prompt.",
      "status: " + statusWord,
      "return value: " + ret,
      "gas: " + gasS,
      "logs: " + logsS,
      "",
      "Sender and receiver",
      "sender: " + tx.sender,
      "receiver: " + tx.to,
      "contract: " + tx.contract + " at " + caddr,
      "",
      "Sender history",
      histBlock(senderRows.length ? senderRows : hist, tx.t),
      "",
      "Receiver history",
      histBlock(receiverRows, tx.t),
      "",
      "Known destinations: " + (known.length ? known.join(", ") : "none"),
      "Balance: " + money(ctx.balance, tx.unit || ""),
      "Cap: " + money(ctx.cap, tx.unit || ""),
      "",
      "Run rules",
      RULES_TEXT,
      "Rule verdict: " + prior.decision + " (score " + Number(prior.score) + ")",
      "Reasons: " + reasonText,
      "",
      "Reply format",
      REPLY_FORMAT,
    ];
    return lines.join("\n") + "\n";
  }
  function simulatedReply(scenarioId) {
    const row = SIMULATED_REPLIES[scenarioId];
    if (!row) return null;
    return { label: SIMULATED_LABEL, json: dumpsReply(row.decision, row.reason), decision: row.decision, reason: row.reason };
  }
  function simulatedComplete(scenarioId) {
    const row = simulatedReply(scenarioId);
    if (!row) return null;
    return function () { return row.json; };
  }
  function parseReply(text) {
    let raw = String(text || "").trim();
    if (raw.startsWith(SIMULATED_LABEL)) raw = raw.slice(SIMULATED_LABEL.length).trim();
    let data = null;
    try { data = JSON.parse(raw); }
    catch (e) {
      const i = raw.indexOf("{"), j = raw.lastIndexOf("}");
      if (i >= 0 && j > i) { try { data = JSON.parse(raw.slice(i, j + 1)); } catch (e2) { data = null; } }
    }
    if (data && typeof data === "object") {
      const d = String(data.decision || "").trim().toLowerCase();
      if (d === "allow" || d === "hold" || d === "block") return { decision: d, reason: String(data.reason || "").trim() };
    }
    const colon = raw.indexOf(":");
    const head = (colon >= 0 ? raw.slice(0, colon) : "").trim().toLowerCase();
    if (colon >= 0 && (head === "allow" || head === "hold" || head === "block")) {
      return { decision: head, reason: raw.slice(colon + 1).trim() };
    }
    return null;
  }
  // `llm` is any function prompt -> text (JSON {decision, reason}, or "decision: reason").
  // Pass simulatedComplete(id) for the canned stand-in. A real model uses the same shape.
  // It may only make the decision stricter, never looser. null abstains.
  function llmPrompt(tx, vmStatus, ctx, prior, vm) {
    return buildPrompt(tx, vm || { status: vmStatus, gas_used: null, return_value: "", logs: [] }, ctx, prior);
  }
  async function review(tx, vmStatus, ctx, vmNote, llm, vm) {
    const v = rules(tx, vmStatus, ctx, vmNote);
    if (!llm || v.source === "vm") return v;
    const promptVm = vm || { status: vmStatus, gas_used: null, return_value: vmNote || "", logs: [] };
    if (promptVm.status === undefined || promptVm.status === null) promptVm.status = vmStatus;
    const text = (await llm(buildPrompt(tx, promptVm, ctx, v)) || "").trim();
    const parsed = parseReply(text);
    const order = ["allow", "hold", "block"];
    if (parsed && order.indexOf(parsed.decision) > order.indexOf(v.decision)) {
      return { decision: parsed.decision, reasons: [parsed.reason || "no reason given"], score: v.score, source: "llm" };
    }
    return v;
  }
  const stubLLM = null;
  const api = {
    rules, review, llmPrompt, stubLLM, VM_STATUS, buildPrompt, simulatedReply, simulatedComplete,
    parseReply, SIMULATED_LABEL, SYSTEM_ROLE, EXAMPLE_CALL, EXAMPLE_WITHDRAW, fmt, shortAddr,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.Judge = api;
})(typeof self !== "undefined" ? self : this);
