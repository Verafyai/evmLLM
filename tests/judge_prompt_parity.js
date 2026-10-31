// Print prompt text, simulated replies, and rule verdicts for the five judge scenarios.
// tests/test_judge_prompt.py compares this with evmllm/judge.py. Do not edit by hand to "fix" a mismatch.
const fs = require("fs");
const path = require("path");
const J = require("../site/judge.js");
const cases = JSON.parse(fs.readFileSync(path.join(__dirname, "../results/judge.json"), "utf8"));
const out = cases.map((s) => {
  const vm = { status: s.vm.status, gas_used: s.vm.gas_used, return_value: s.vm.outcome, logs: [] };
  const rules = J.rules(s.tx, s.vm.status, s.ctx, s.vm.outcome);
  return {
    id: s.id,
    prompt: J.buildPrompt(s.tx, vm, s.ctx, s.verdict),
    promptFromRules: J.buildPrompt(s.tx, vm, s.ctx, rules),
    simulated: J.simulatedReply(s.id),
    rules,
  };
});
process.stdout.write(JSON.stringify(out));
