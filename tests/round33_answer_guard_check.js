

const path = require("path");
const fs = require("fs");
const guard = require(path.join(__dirname, "..", "app", "pgstatic", "answer-guard.js"));
const data = JSON.parse(fs.readFileSync(path.join(__dirname, "data", "round33_leak_samples.json"), "utf8"));
const LEAK = /<\/?tool_call|<\|[^|\n]{0,40}\|>|<｜|DSML|<function|<invoke|<arg_key|\[TOOL_|<tool_response|_report>|<web_search>|<docs_read>|<\/think>|<\/?tool|<\/?web_|<\/?docs_|<\|/;
const out = {};
for (const sample of data.samples) {
  const chunks = sample.chunks || [sample.text];
  const full = chunks.join("");
  const bad = [];
  let raw = "";
  for (const chunk of chunks) {

    for (const ch of Array.from(chunk)) {
      raw += ch;
      if (sample.inline_reasoning && !raw.includes("</think>")) continue;
      const shown = guard.clean(raw, { partial: true });
      const prose = guard.segments(shown).filter((s) => !s.code).map((s) => s.text).join("");
      if (LEAK.test(prose) && sample.name !== "code-example-kept" && !sample.prose) bad.push(shown.slice(-80));
    }
  }
  const extracted = guard.extractCalls(full);
  out[sample.name] = { visible: guard.clean(full).trim(), calls: extracted.calls, rest: extracted.text,
                       stream_bad: bad.slice(0, 3) };
}
process.stdout.write(JSON.stringify(out));
