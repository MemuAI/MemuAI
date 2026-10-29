

const MemuAnswerGuard = (() => {

  const TOOL_TAG = /^(?:web_[a-z_]+|docs_[a-z_]+|work_[a-z_]+|code_[a-z_]+|panel_[a-z_]+|agent_[a-z_]+|mcp_[\w-]+|github_[a-z_]+|gmail_[a-z_]+|gdrive_[a-z_]+|drive_[a-z_]+|gcal_[a-z_]+|calendar_[a-z_]+|notion_[a-z_]+|product_lookup|current_time|system_info|working_directory|disk_space|open_apps|process_list|environment|project_tree|file_exists|search_files|network_status|app_version|memory_[a-z_]+|image_[a-z_]+|take_screenshot|current_page|read_page|search|browse|fetch|python|code_interpreter)$/i;
  const NAME_RE = /^[A-Za-z][\w.-]{0,63}$/;

  const ENVELOPES = "local-tool_report|terminal-command_report|browser_report|computer-control_report|agent_report|supplemental_context|session_summary|context_safety|attached_files|image_description|video_analysis|generated_images|tool_response|tool_result|tool_results|tool_output|function_results|function_result|ds_safety";
  const BLOCKS = [

    { open: /<tool_call\b[^>]*>/iy, close: /<\/tool_call>/i, call: true },
    { open: /<tool_calls?>/iy, close: /<\/tool_calls?>/i, call: true },
    { open: /<function_calls>/iy, close: /<\/function_calls>/i, call: true },
    { open: /<invoke\s+name\s*=\s*["'][^"']+["'][^>]*>/iy, close: /<\/invoke>/i, call: true },
    { open: /<function\s*=\s*[\w.-]+\s*>/iy, close: /<\/function>/i, call: true },
    { open: /<[｜|]\s*DSML\s*[｜|]\s*(?:function_calls|calls)\s*>/iy, close: /<\/[｜|]\s*DSML\s*[｜|]\s*(?:function_calls|calls)\s*>/i, call: true },
    { open: /<[｜|]\s*DSML\s*[｜|]\s*invoke\s+name\s*=\s*["'][^"']+["'][^>]*>/iy, close: /<\/[｜|]\s*DSML\s*[｜|]\s*invoke\s*>/i, call: true },
    { open: /<[｜|]tool[▁_ ]calls[▁_ ]begin[｜|]>/iy, close: /<[｜|]tool[▁_ ]calls[▁_ ]end[｜|]>/i, call: true },
    { open: /<\|tool_calls_section_begin\|>/iy, close: /<\|tool_calls_section_end\|>/i, call: true },
    { open: /<\|tool_call_begin\|>/iy, close: /<\|tool_call_end\|>/i, call: true },
    { open: /<\|channel\|>\s*commentary\s+to=[^<]*?(?:<\|constrain\|>\s*\w+\s*)?<\|message\|>/iy, close: /<\|call\|>/i, call: true },
    { open: /<\|channel\|>\s*analysis\s*<\|message\|>/iy, close: /<\|end\|>/i },
    { open: /\[TOOL_CALLS\]/y, close: null, call: true, line: true },
    { open: /<\|python_tag\|>/iy, close: /<\|eom_id\|>|<\|eot_id\|>/i, call: true, line: true },
    { open: new RegExp(`<(${ENVELOPES})\\b[^>]*>`, "iy"), close: (m) => new RegExp(`</${m[1].replace(/[-]/g, "\\-")}>`, "i") },
    { open: /\[TOOL_RESULTS?\]/y, close: /\[\/TOOL_RESULTS?\]/ },
    { open: /◁think▷/y, close: /◁\/think▷/ },
  ];

  const TOKENS = /<\|(?:im_start\|>\s*(?:assistant|user|system)?|start\|>\s*assistant|start_header_id\|>\s*\w+\s*<\|end_header_id\|>|[a-z][a-z0-9_]{1,40}\|>)|<｜[^｜<>\n]{1,40}｜>|<\|(?:channel)\|>\s*final\s*<\|message\|>|<\/?s>(?=\s*$)|\[(?:\/?INST|\/?TOOL_CALLS|AVAILABLE_TOOLS|\/AVAILABLE_TOOLS)\]/gi;

  const OPENER_WORDS = ["<tool_call>", "<tool_calls>", "<function_calls>", "<function=", "<invoke name=", "<|", "<｜",
    "[TOOL_CALLS]", "[TOOL_RESULT]", "<think>", "</think>", "<thinking>", "</thinking>", "◁think▷", "◁/think▷",
    ...ENVELOPES.split("|").map((name) => `<${name}>`)];

  function segments(text) {
    const source = String(text || "");
    const out = [];
    const re = /(^|\n)([ \t]*)(`{3,}|~{3,})[^\n]*(?:\n|$)|`+/g;
    let last = 0, match;
    while ((match = re.exec(source))) {
      if (match[3]) {
        const start = match.index + match[1].length;
        const fence = match[3];
        const closeRe = new RegExp(`\\n[ \\t]*${fence[0] === "`" ? "`" : "~"}{${fence.length},}[ \\t]*(?=\\n|$)`, "g");
        closeRe.lastIndex = match.index + match[0].length - (match[0].endsWith("\n") ? 1 : 0);
        const close = closeRe.exec(source);
        const end = close ? close.index + close[0].length : source.length;
        if (start > last) out.push({ code: false, text: source.slice(last, start) });
        out.push({ code: true, text: source.slice(start, end) });
        last = end; re.lastIndex = end;
      } else {
        const ticks = match[0];
        const close = source.indexOf(ticks, match.index + ticks.length);

        if (close < 0 || /\n\s*\n/.test(source.slice(match.index, close))) continue;
        if (match.index > last) out.push({ code: false, text: source.slice(last, match.index) });
        out.push({ code: true, text: source.slice(match.index, close + ticks.length) });
        last = close + ticks.length; re.lastIndex = last;
      }
    }
    if (last < source.length) out.push({ code: false, text: source.slice(last) });
    return out;
  }

  const ANY_OPEN = new RegExp(BLOCKS.map((rule) => `(?:${rule.open.source})`).join("|"), "gi");

  const CALL_BODY = /^\s*(?:[\[{]|<(?:invoke|function|arg_key|parameter|tool_call|[｜|])|[A-Za-z][\w.:-]*\s*(?:\(|<arg_key>|\n\s*<arg_key>|\n\s*[\[{]|<\|tool_call_argument_begin))/i;

  const CALL_PREFIX = /^\s*<[a-z_]*=?$|^\s*(?:[A-Za-z][\w.:-]*\s*(?:\([\s\S]*|<[a-z_]*>?[\s\S]*|\{[\s\S]*)?)?$/;
  const PROSE_RULE = /^(?:<tool_call|<tool_calls?>|<function_calls>|<invoke|<function\s*=|\[TOOL_|<(?:local-tool|terminal-command|browser|computer-control|agent)_report|<(?:supplemental_context|session_summary|context_safety|attached_files|image_description|video_analysis|generated_images|tool_response|tool_result|tool_results|tool_output|function_results|function_result|ds_safety)\b)/i;
  function nextBlock(text, from, partial = false) {
    let best = null;
    ANY_OPEN.lastIndex = from;
    let first;
    while (!best && (first = ANY_OPEN.exec(text))) {
      const i = first.index;
      for (const rule of BLOCKS) {
        rule.open.lastIndex = i;
        const m = rule.open.exec(text);
        if (!m) continue;
        const bodyStart = i + m[0].length;
        const closeRe = typeof rule.close === "function" ? rule.close(m) : rule.close;
        let end = text.length, inner = text.slice(bodyStart), closed = false;
        if (closeRe) {
          const rest = text.slice(bodyStart);
          const c = rest.match(closeRe);
          if (c) { inner = rest.slice(0, c.index); end = bodyStart + c.index + c[0].length; closed = true; }
        }
        if (!closed && rule.line) {

          const span = scanJson(text, bodyStart);
          if (span) { inner = text.slice(bodyStart, span.end); end = span.end; closed = true; }
        }
        const proseTag = PROSE_RULE.test(m[0]);
        if (proseTag && rule.call && !/^\[TOOL_CALLS\]/i.test(m[0])) {

          const looksLikeCall = CALL_BODY.test(inner) || (/^<function\s*=|^<invoke/i.test(m[0]) && /^\s*(?:<parameter|[\[{]|$)/.test(inner));
          if (!looksLikeCall && !(partial && !closed && CALL_PREFIX.test(inner))) break;
        }
        if (!closed && rule.call && /^<tool_call\b/i.test(m[0])) {

          const next = text.slice(bodyStart).search(/<tool_call\b|\n[ \t]*\n/i);
          if (next >= 0) { inner = text.slice(bodyStart, bodyStart + next); end = bodyStart + next; closed = "soft"; }
        }

        if (!closed && !partial && proseTag && !rule.call) break;
        if (!closed && !partial && /^\[TOOL_CALLS\]/i.test(m[0])) break;
        best = { start: i, end, inner, rule, open: m[0], closed };
        break;
      }
      if (!best) ANY_OPEN.lastIndex = i + 1;
    }

    const tagRe = /<([A-Za-z][\w.-]{1,63})>/g;
    tagRe.lastIndex = from;
    let t;
    while ((t = tagRe.exec(text)) && (!best || t.index < best.start)) {
      if (!TOOL_TAG.test(t[1])) continue;
      const bodyStart = t.index + t[0].length;
      const closeTag = `</${t[1]}>`;
      const closeAt = text.indexOf(closeTag, bodyStart);
      const body = text.slice(bodyStart, closeAt >= 0 ? closeAt : text.length);
      if (!/^\s*[\[{]/.test(body) && closeAt < 0 && !(partial && /^\s*(?:<\w*)?$/.test(body))) continue;
      if (closeAt >= 0 && !/^\s*[\[{]/.test(body) && !/^\s*<(?:parameter|arg)/i.test(body)) continue;
      best = { start: t.index, end: closeAt >= 0 ? closeAt + closeTag.length : text.length, inner: body,
               rule: { call: true, tag: t[1] }, open: t[0], closed: closeAt >= 0 };
      break;
    }
    return best;
  }

  function scanJson(text, from) {
    let i = from;
    while (i < text.length && /\s/.test(text[i])) i += 1;
    if (text[i] !== "[" && text[i] !== "{") return null;
    const start = i;
    let depth = 0, inString = false, escaped = false;
    for (; i < text.length; i += 1) {
      const ch = text[i];
      if (inString) {
        if (escaped) escaped = false; else if (ch === "\\") escaped = true; else if (ch === '"') inString = false;
        continue;
      }
      if (ch === '"') inString = true;
      else if (ch === "[" || ch === "{") depth += 1;
      else if (ch === "]" || ch === "}") { depth -= 1; if (!depth) return { start, end: i + 1 }; }
    }
    return null;
  }

  function cleanProse(text, partial, isLast) {
    let out = "", pos = 0, block;
    while ((block = nextBlock(text, pos, partial))) {
      out += text.slice(pos, block.start);
      if (!block.closed && !partial && block.rule.tag) { out += text.slice(block.start, block.end); pos = block.end; continue; }
      pos = block.end;
    }
    out += text.slice(pos);
    out = out.replace(TOKENS, "");
    if (partial && isLast) out = holdBack(out);
    return out;
  }

  function holdBack(text) {
    const tailStart = Math.max(text.lastIndexOf("<"), text.lastIndexOf("["), text.lastIndexOf("◁"));
    if (tailStart < 0 || text.length - tailStart > 40) return text;
    const tail = text.slice(tailStart);
    if (/[\s]/.test(tail.slice(1)) && !/^<invoke /i.test(tail) && !/^<\|channel\|>/i.test(tail)
        && !/^<[｜|]\s*DSML\b/i.test(tail)) return text;
    const low = tail.toLowerCase();
    const possible = OPENER_WORDS.some((word) => word.toLowerCase().startsWith(low) || (low.startsWith(word.toLowerCase().replace(/>$/, "")) && !low.includes(">")))
      || /^<\|[\w|]*$/.test(tail) || /^<｜[^｜]*$/.test(tail)
      || (/^<[A-Za-z][\w.-]*$/.test(tail) && [...tail.slice(1)].length >= 1 && couldBeToolTag(tail.slice(1)));
    return possible ? text.slice(0, tailStart) : text;
  }
  function couldBeToolTag(name) {
    return /^(?:w(?:e(?:b(?:_\w*)?)?)?|d(?:o(?:c(?:s(?:_\w*)?)?)?)?|t(?:o(?:o(?:l\w*)?)?)?|f(?:u(?:n\w*)?)?|i(?:n(?:v\w*)?)?|th\w*|c(?:o(?:d(?:e(?:_\w*)?)?)?)?|p(?:a(?:n(?:e(?:l(?:_\w*)?)?)?)?)?|m(?:c(?:p\w*)?)?)$/i.test(name)
      || TOOL_TAG.test(name);
  }

  function hasToolTag(text) {
    for (const m of String(text).matchAll(/<([A-Za-z][\w.-]{1,63})>/g)) if (TOOL_TAG.test(m[1])) return true;
    return false;
  }

  function splitThink(text) {
    const source = String(text || "");
    const re = /<\/(?:think|thinking|reasoning)>|◁\/think▷/gi;
    let match, last = null;
    while ((match = re.exec(source))) last = match;
    if (!last) return { content: source, reasoning: "" };
    const before = source.slice(0, last.index);

    if (/<(?:think|thinking|reasoning)\b[^>]*>|◁think▷/i.test(before)) return { content: source, reasoning: "" };
    return { content: source.slice(last.index + last[0].length).replace(/^\s+/, ""), reasoning: before.trim() };
  }

  const TOKEN_BLOCK = /<[｜|](?:\s*DSML\s*[｜|]\s*(?:(?:function_calls|calls)\s*>|invoke\s+name\s*=)|tool[▁_ ]calls[▁_ ]begin[｜|]>|tool_calls_section_begin\|>)/i;
  function tokenBlocks(text, onBlock) {
    if (!TOKEN_BLOCK.test(text)) return text;
    let out = "", pos = 0;
    const protectedSpans = [];
    let offset = 0, codeIndex = 0;
    for (const segment of segments(text)) {
      if (segment.code) protectedSpans.push([offset, offset + segment.text.length]);
      offset += segment.text.length;
    }
    for (;;) {
      const rest = text.slice(pos);
      const m = rest.match(TOKEN_BLOCK);
      if (!m) break;
      const start = pos + m.index;
      while (codeIndex < protectedSpans.length && protectedSpans[codeIndex][1] <= start) codeIndex++;
      if (codeIndex < protectedSpans.length && protectedSpans[codeIndex][0] <= start && start < protectedSpans[codeIndex][1]) {
        const end = protectedSpans[codeIndex][1];
        out += text.slice(pos, end);
        pos = end;
        continue;
      }
      const block = nextBlock(text, start);
      if (!block || block.start !== start) { out += text.slice(pos, start + 1); pos = start + 1; continue; }
      out += text.slice(pos, start);
      if (onBlock) onBlock(block);
      pos = block.end;
    }
    return out + text.slice(pos);
  }

  function clean(text, { partial = false } = {}) {
    let source = String(text || "");
    if (!source) return source;
    source = tokenBlocks(splitThink(source).content);
    ANY_OPEN.lastIndex = 0;
    TOKENS.lastIndex = 0;
    if (!ANY_OPEN.test(source) && !TOKENS.test(source) && !hasToolTag(source) && (!partial || holdBack(source) === source)) {
      TOKENS.lastIndex = 0;
      return source;
    }
    TOKENS.lastIndex = 0;
    const parts = segments(source);
    const out = parts.map((seg, index) => seg.code ? seg.text : cleanProse(seg.text, partial, index === parts.length - 1));

    return out.join("").replace(/[ \t]+\n/g, "\n").replace(/\n{3,}/g, "\n\n");
  }

  function normName(raw) {
    let name = String(raw || "").trim().replace(/^functions[.:]/i, "").replace(/:\d+$/, "").replace(/^["']|["']$/g, "");
    if (!NAME_RE.test(name)) return "";
    return name.toLowerCase().startsWith("mcp_") ? "mcp_" + name.slice(4) : name.toLowerCase();
  }
  function parseJsonLoose(raw) {
    const value = String(raw || "").trim().replace(/^```(?:json)?\s*|\s*```$/g, "");
    try { return JSON.parse(value); } catch (e) {  }
    const repaired = value.replace(/("(?:[^"\\]|\\.)*")|,\s*(?=[\]}])/g, (match, quoted) => quoted || "");
    try { return JSON.parse(repaired); } catch (e) {  }
    const escaped = repaired.replace(/\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4})|\\/g, match => match === "\\" ? "\\\\" : match);
    try { return JSON.parse(escaped); } catch (e) { return undefined; }
  }
  function argsOf(value) {
    if (typeof value === "string") { const parsed = parseJsonLoose(value); value = parsed === undefined ? { value } : parsed; }
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  }
  function fromObject(row) {
    if (!row || typeof row !== "object" || Array.isArray(row)) return null;
    const fn = row.function && typeof row.function === "object" ? row.function : null;
    const name = normName(row.name || row.tool || row.tool_name || row.recipient_name || fn?.name || "");
    if (!name) return null;
    return { tool: name, args: argsOf(row.arguments ?? row.args ?? row.parameters ?? row.input ?? fn?.arguments ?? {}) };
  }
  function jsonObjects(text) {

    const rows = [];
    let pos = 0, span;
    while ((span = scanJson(text, pos))) {
      const data = parseJsonLoose(text.slice(span.start, span.end));
      if (data !== undefined) rows.push(...(Array.isArray(data) ? data : [data]));
      pos = span.end;
    }
    return rows;
  }
  function scalar(raw) {
    const value = String(raw ?? "").trim();
    if (/^-?\d+(?:\.\d+)?$/.test(value)) return Number(value);
    if (/^(?:true|false)$/i.test(value)) return value.toLowerCase() === "true";
    const parsed = /^[\[{"]/.test(value) ? parseJsonLoose(value) : undefined;
    return parsed === undefined ? value.replace(/^["']|["']$/g, "") : parsed;
  }
  function callExpr(body) {

    const m = String(body || "").trim().match(/^([A-Za-z][\w.-]*)\s*\(([\s\S]*)\)\s*$/);
    if (!m) return null;
    const name = normName(m[1]);
    if (!name) return null;
    const inside = m[2].trim();
    if (/^[\[{]/.test(inside)) return { tool: name, args: argsOf(inside) };
    const args = {};
    const re = /([A-Za-z_][\w]*)\s*[:=]\s*("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|\[[^\]]*\]|\{[^}]*\}|[^,]+)\s*(?:,|$)/g;
    let a;
    while ((a = re.exec(inside))) {
      const raw = a[2].trim();
      args[a[1]] = /^"/.test(raw) ? (parseJsonLoose(raw) ?? raw.slice(1, -1)) : /^'/.test(raw) ? raw.slice(1, -1) : scalar(raw);
    }
    return { tool: name, args };
  }
  function xmlParams(body) {
    const args = {};
    const re = /<parameter\s*(?:=\s*|name\s*=\s*["'])([\w.-]+)["']?[^>]*>([\s\S]*?)<\/parameter>|<arg_key>([\s\S]*?)<\/arg_key>\s*<arg_value>([\s\S]*?)<\/arg_value>|<[｜|]\s*DSML\s*[｜|]\s*parameter\s+name\s*=\s*["']([^"']+)["'][^>]*>([\s\S]*?)<\/[｜|]\s*DSML\s*[｜|]\s*parameter\s*>/gi;
    let m, found = false;
    while ((m = re.exec(body))) {
      found = true;
      const key = (m[1] || m[3] || m[5] || "").trim();
      const value = m[1] ? m[2] : m[3] !== undefined ? m[4] : m[6];
      if (key) args[key] = /\bstring\s*=\s*["']true["']/i.test(m[0].split(">", 1)[0]) ? value : scalar(value);
    }
    return found ? args : null;
  }
  function invokeArgs(body, dsml = false) {
    let args = xmlParams(body);
    const wrapped = args !== null;
    if (args === null) {
      const span = scanJson(body, 0);
      args = span ? argsOf(body.slice(span.start, span.end)) : {};
    }
    const keys = Object.keys(args);
    // Named object parameters are only transport holders in DSML XML.
    if (dsml && wrapped && keys.length === 1 && ["args", "arguments", "parameters", "input"].includes(keys[0])) {
      const value = args[keys[0]];
      if (value && typeof value === "object" && !Array.isArray(value)) return value;
    }
    return args;
  }
  function callsFromBlock(block) {
    const inner = block.inner || "";
    const open = block.open || "";
    const out = [];
    const dsml = /^<[｜|]\s*DSML\s*[｜|]/i.test(open);
    if (block.rule.tag) {
      const name = normName(block.rule.tag);
      const params = xmlParams(inner);
      if (name) out.push({ tool: name, args: params || argsOf(inner.trim()) });
      return out;
    }
    let m;
    if ((m = open.match(/<function\s*=\s*([\w.-]+)/i))) {
      const name = normName(m[1]);
      if (name) out.push({ tool: name, args: xmlParams(inner) || argsOf(inner.trim()) });
      return out;
    }
    if ((m = open.match(/<(?:[｜|]\s*DSML\s*[｜|]\s*)?invoke\s+name\s*=\s*["']([^"']+)/i))) {
      const name = normName(m[1]);
      if (name && block.closed) out.push({ tool: name, args: invokeArgs(inner, dsml) });
      return out;
    }
    if ((m = open.match(/commentary\s+to=([\w.:-]+)/i))) {
      const name = normName(m[1]);
      if (name) out.push({ tool: name, args: argsOf(inner.trim()) });
      return out;
    }

    const invokeRe = /<(?:[｜|]\s*DSML\s*[｜|]\s*)?invoke\s+name\s*=\s*["']([^"']+)["'][^>]*>([\s\S]*?)<\/(?:[｜|]\s*DSML\s*[｜|]\s*)?invoke\s*>/gi;
    while ((m = invokeRe.exec(inner))) {
      const name = normName(m[1]);
      if (name) out.push({ tool: name, args: invokeArgs(m[2], dsml || /^<[｜|]\s*DSML\s*[｜|]/i.test(m[0])) });
    }
    if (out.length) return out;

    const kimiRe = /(?:<\|tool_call_begin\|>)?\s*([\w.:-]+)\s*<\|tool_call_argument_begin\|>\s*([\s\S]*?)\s*(?:<\|tool_call_end\|>|$)/gi;
    while ((m = kimiRe.exec(inner))) {
      const name = normName(m[1]);
      if (name) out.push({ tool: name, args: argsOf(m[2]) });
    }
    if (out.length) return out;

    const dsRe = /(?:function)?\s*<[｜|]tool[▁_ ]sep[｜|]>\s*([\w.-]+)\s*(?:```(?:json)?\s*)?([\s\S]*?)(?:```)?\s*(?:<[｜|]tool[▁_ ]call[▁_ ]end[｜|]>|$)/gi;
    while ((m = dsRe.exec(inner))) {
      const name = normName(m[1]);
      if (name) out.push({ tool: name, args: argsOf(m[2]) });
    }
    if (out.length) return out;

    const glm = inner.match(/^\s*([A-Za-z][\w.-]*)\s*(?=<arg_key>)/);
    if (glm) {
      const name = normName(glm[1]);
      if (name) out.push({ tool: name, args: xmlParams(inner) || {} });
      return out;
    }
    const expr = callExpr(inner);
    if (expr) return [expr];
    for (const row of jsonObjects(inner)) {
      const call = fromObject(row);
      if (call) out.push(call);
    }
    return out;
  }

  function extractCalls(text) {
    const source = String(text || "");
    const calls = [];
    const seen = new Set();
    const add = (call) => {
      const key = call.tool + JSON.stringify(call.args);
      if (calls.length < 12 && !seen.has(key)) { seen.add(key); calls.push(call); }
    };

    const answer = tokenBlocks(splitThink(source).content, (block) => callsFromBlock(block).forEach(add));
    const parts = segments(answer).map((seg) => {
      if (seg.code) return seg.text;
      let out = "", pos = 0, block;
      while ((block = nextBlock(seg.text, pos))) {
        out += seg.text.slice(pos, block.start);
        pos = block.end;
        if (block.rule.call && (block.closed || !block.rule.tag)) {

          if (block.closed === "soft" && !/^\s*(?:[A-Za-z][\w.-]*\s*\(|[\[{]|[A-Za-z][\w.-]*\s*<arg_key>)/.test(block.inner)) continue;
          callsFromBlock(block).forEach(add);
        }
      }
      return out + seg.text.slice(pos);
    });
    return { text: calls.length ? clean(parts.join("")).trim() : source, calls };
  }

  return { clean, extractCalls, splitThink, segments };
})();
if (typeof module !== "undefined" && module.exports) module.exports = MemuAnswerGuard;
