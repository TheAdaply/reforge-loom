// Render the dashboard's own JS against real /state payloads under a stub DOM, and dump
// every DOM mutation it makes. Two builds of the page must produce byte-identical dumps.
const fs = require("fs");
const [pagePath, statesPath] = process.argv.slice(2);
const html = fs.readFileSync(pagePath, "utf8");
const script = html.split("<script>\n")[1].split("\n</script>")[0];
const states = JSON.parse(fs.readFileSync(statesPath, "utf8"));

const store = {};
function fakeEl(id) {
  return store[id] || (store[id] = {
    id, _innerHTML: "", _text: "", style: {}, dataset: {},
    classList: { _s: new Set(), add(c){this._s.add(c);}, remove(c){this._s.delete(c);} },
    addEventListener() {},
    set innerHTML(v) { this._innerHTML = v; }, get innerHTML() { return this._innerHTML; },
    set textContent(v) { this._text = String(v); }, get textContent() { return this._text; },
  });
}
global.document = { getElementById: fakeEl, addEventListener() {}, hidden: true };
global.window = global; global.innerWidth = 1400;
global.setInterval = () => 0;
global.fetch = async () => { throw new Error("no network in this harness"); };

const mod = new Function(script + "\n;return {render, fabricSVG, colorOf, fmtTTL, agentSlot};")();

const out = [];
for (const name of Object.keys(states)) {
  for (const k of Object.keys(store)) delete store[k];
  mod.agentSlot.clear();                       // colors are first-seen; reset per scenario
  mod.render(states[name]);
  out.push("##### " + name);
  for (const id of Object.keys(store).sort()) {
    const e = store[id];
    out.push(`[${id}] text=${JSON.stringify(e._text)} cls=${[...e.classList._s].sort()} disp=${e.style.display}`);
    if (e._innerHTML) out.push(`[${id}] html=${e._innerHTML}`);
  }
}
console.log(out.join("\n"));
