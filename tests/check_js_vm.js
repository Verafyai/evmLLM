// Check the site's browser reference VM (site/evm.js) against py-evm results.
const E = require(process.argv[2] || "../site/evm.js");
const cases = require("../results/pyevm_corpus.json");
let ok = 0; const bad = [];
for (const c of cases) {
  const r = E.run({ code: E.hexToBytes(c.code), calldata: E.hexToBytes(c.calldata), gas: c.gas, storage: c.storage, maxTrace: 0 }, { maxTrace: 0 });
  const norm = (o) => JSON.stringify(Object.entries(o).map(([k, v]) => [BigInt(k).toString(16), BigInt(v).toString(16)]).sort());
  const same = r.status === c.status && r.gasUsed === c.gas_used && r.ret === c.ret && norm(r.storage) === norm(c.changed);
  if (same) ok++; else bad.push([c.label, r.status, c.status, r.gasUsed, c.gas_used]);
}
console.log(`browser VM vs py-evm: ${ok}/${cases.length}`); if (bad.length) console.log(bad.slice(0, 10));
require("fs").writeFileSync(require("path").join(__dirname, "../results/js_vm.json"), JSON.stringify({ n: cases.length, pass: ok }) + "\n");
