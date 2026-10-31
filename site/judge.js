// EVMLLM judgement filter, rule-based (a JavaScript port of evmllm/judge.py).
// This review runs AFTER the VM. It is ordinary code, NOT part of the transformer weights.
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

  // Pluggable LLM judge. Pass any async function prompt -> "allow|hold|block: reason".
  // The default stub abstains (returns null), so the rules decide.
  function llmPrompt(tx, vmStatus, ctx, prior) {
    const hist = (ctx.history || []).slice(-20).map((h) => `- ${h.call} to ${h.to} at t=${h.t}`).join("\n") || "- none";
    return `You review a transaction AFTER an exact EVM execution.\nVM status: ${VM_STATUS[vmStatus]}\nCall: ${tx.call} on ${tx.contract} from ${tx.sender} to ${tx.to}\nHistory:\n${hist}\nRule-based verdict: ${prior.decision} (${prior.reasons.join("; ")})\nAnswer 'allow', 'hold' or 'block', a colon, then one sentence of reasoning.`;
  }
  async function review(tx, vmStatus, ctx, vmNote, llm) {
    const v = rules(tx, vmStatus, ctx, vmNote);
    if (!llm || v.source === "vm") return v;
    const text = (await llm(llmPrompt(tx, vmStatus, ctx, v)) || "").trim();
    const d = text.split(":")[0].trim().toLowerCase(), order = ["allow", "hold", "block"];
    if (order.includes(d) && order.indexOf(d) > order.indexOf(v.decision)) return { decision: d, reasons: [text.split(":").slice(1).join(":").trim()], score: v.score, source: "llm" };
    return v; // an LLM may only make the decision stricter, never looser
  }
  const stubLLM = null;
  const api = { rules, review, llmPrompt, stubLLM, VM_STATUS };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.Judge = api;
})(typeof self !== "undefined" ? self : this);
