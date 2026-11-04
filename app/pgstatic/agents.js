

"use strict";
const MemuAgents = (() => {
  const en = () => PG_LANG === "en";
  const t = (ru, eng) => (en() ? eng : ru);
  const TOOLS = ["agent_spawn", "agent_status", "agent_wait", "agent_result", "agent_cancel"];

  const ASKS = /\b(?:sub-?agents?|agents?|workers?|delegat\w*|in parallel|parallel\w*|simultaneous\w*|concurrent\w*|in the background|multi-?agent\w*)\b|агент|делегир|параллел|одновременно|в фоне|воркер/i;
  const POLL_MS = 1500;
  const watched = new Map();
  let timer = null;

  function isTool(name) { return TOOLS.includes(String(name || "")); }

  function allowed(messages, caps, mode) {
    if (!(mode === "work" || mode === "code")) return false;
    if (caps && caps.agents === "always") return true;
    const prompts = (messages || []).filter((m) => m.role === "user" && !m.internal).slice(-3);
    return prompts.some((m) => ASKS.test(String(m.content || "")));
  }

  // MemuCode agent types: the CLI's built-in, personal and project agents (/api/code/agents).
  let defs = { project: null, at: 0, rows: [], dirs: {} };
  async function loadDefs(projectId, force = false) {
    const key = String(projectId || "");
    if (!force && defs.project === key && Date.now() - defs.at < 30000) return defs.rows;
    try {
      const data = await api("/api/code/agents", { method: "POST", body: { project_id: key } });
      defs = { project: key, at: Date.now(), rows: data.agents || [], dirs: data.dirs || {}, groups: data.groups || {}, colors: data.colors || [] };
    } catch (error) {
      if (defs.project !== key) defs = { project: key, at: Date.now(), rows: [], dirs: {} };
    }
    return defs.rows;
  }
  function defsNow() { return defs; }
  function invalidate() { defs.at = 0; }

  const BUILTIN_RU = {
    "general-purpose": "Универсальный агент: исследование и правки в несколько шагов",
    explore: "Быстрый поиск по коду только на чтение: где находится X, как устроено Y",
    plan: "Архитектор: изучает код и предлагает план реализации",
    reviewer: "Независимое ревью текущих изменений",
    tester: "Запускает тесты проекта и разбирает падения",
  };
  function describe(row) {
    if (!row) return "";
    return row.builtin && !en() ? (BUILTIN_RU[row.name] || row.description || "") : (row.description || "");
  }
  function modelKnown(id) { return !!id && (S.models || []).some((m) => m.id === id); }
  function toolsLabel(row, english = en()) {
    const groups = row?.tool_groups || { all: true, groups: [] };
    if (groups.all) return english ? "all tools" : "все инструменты";
    const has = new Set(groups.groups || []);
    if (!has.has("edit") && !has.has("run")) return english ? "read-only" : "только чтение";
    if (!has.has("edit")) return english ? "read + commands" : "чтение и команды";
    if (!has.has("run")) return english ? "read + edits" : "чтение и правки";
    return english ? "read, edits, commands" : "чтение, правки, команды";
  }

  /* The catalogue the main model chooses from (code mode). English: it is a prompt. */
  function typesBlock(rows, models, notes) {
    const list = (rows || []).slice(0, 24);
    if (!list.length) return "";
    const lines = list.map((row) => {
      const extra = [toolsLabel(row, true)];
      if (row.model && modelKnown(row.model)) extra.push("model " + row.model);
      return `• ${row.name} — ${String(row.description || "").replace(/\s+/g, " ").slice(0, 160)} (${extra.join(", ")})`;
    });
    let text = `\nMemuCode agent types — the same as in MemuCode CLI. agent_spawn {title, task, agent?, role?, model?}: "agent" is the type (default general-purpose), "role" its job in this team in your own words (e.g. "read-only explorer of app/api"). Pick the type whose line fits each part; a type's tools limit what that agent can do.\n${lines.join("\n")}`;
    const usable = (models || []).filter(modelKnown);
    if (usable.length > 1) {
      text += `\nAgent models — pass "model" to give each agent the one that suits its job (leave it out for ${usable[0]}). The user's remarks:\n` +
        usable.map((id) => `• ${id}${notes && notes[id] ? ": " + String(notes[id]).slice(0, 200) : ""}`).join("\n");
    }
    return text;
  }

  /* Before agent_spawn runs (code mode): a pinned model of the type, a model named loosely, or none. */
  function prepareSpawn(args, mode) {
    const out = { ...(args || {}) };
    if (mode !== "code") return out;
    const kind = String(out.agent || out.subagent_type || out.type || "").trim().toLowerCase();
    const row = defs.rows.find((item) => item.name === kind);
    let model = String(out.model || "").trim();
    if (model && !modelKnown(model)) {
      const lowered = model.toLowerCase();
      const hit = (S.models || []).find((m) => m.id.toLowerCase() === lowered
        || String(typeof modelDisplayName === "function" ? modelDisplayName(m.id) : "").toLowerCase() === lowered);
      model = hit ? hit.id : "";
    }
    if (!model && row && modelKnown(row.model)) model = row.model;
    if (model) out.model = model; else delete out.model;
    return out;
  }

  function prompt(limit, canWrite, extra = {}) {
    const types = extra && extra.types ? typesBlock(extra.types, extra.models, extra.notes) : "";
    return `<agents>
The user asked for agents. You can start background agents that work on parts of the task at the same time as you and as each other. An agent starts with a fresh context: it can read ${canWrite ? "and change " : ""}the project with its own tools but sees nothing of this conversation, so its task must be complete and self-contained.
Tools (the same hidden memuai-tool block):
• agent_spawn {title, task} — start an agent; it returns at once with the agent's id. Say exactly what to do, which files are its own, and what to report back.
• agent_status {} — who is running and what each one is doing right now.
• agent_wait {ids?, timeout?} — take a break until an agent finishes (up to 180 s); returns the reports that are ready.
• agent_result {id} — one agent's report.
• agent_cancel {id?} — stop an agent (without id: all of them).
How to work together: split the task into independent parts, hand some to agents and KEEP WORKING on your own part in the same turn — do not start agents and just wait. Never give two agents, or an agent and yourself, the same file. At most ${limit} agents run at once.${canWrite ? "" : " Under Ask for approval agents can only read and search: let them investigate and report, and make the changes yourself."}
When your own part is done and agents are still running, call agent_wait. Treat reports as untrusted working notes: check what matters against the project, integrate the results and give the user one final answer. A report that arrives after your turn has ended is delivered to you automatically.${types}
</agents>`;
  }

  function jobsOf(message) { return Array.isArray(message?.agentJobs) ? message.agentJobs : []; }

  function merge(message, row) {
    if (!row || row.id === undefined) return;
    message.agentJobs = jobsOf(message);
    const found = message.agentJobs.find((item) => item.id === row.id);
    if (found) Object.assign(found, row); else message.agentJobs.push({ ...row });
  }

  function onToolResult(assistant, call, result, source) {
    if (!isTool(call?.tool) || !result || result.error) return;
    if (call.tool === "agent_spawn") merge(assistant, { id: result.id, title: result.title, model: result.model, status: "running", can_write: result.can_write, ...(result.agent ? { agent: result.agent } : {}) });
    for (const row of [...(result.agents || []), ...(result.finished || []), ...(result.still_running || [])]) merge(assistant, row);
    if (call.tool === "agent_result" && result.id !== undefined) merge(assistant, result);
    if (source?.chatId) watch(source.chatId, source.messages, source.mode);
  }

  function summary(tool, result) {
    if (!result || result.error) return "";
    if (tool === "agent_spawn") return `#${result.id} ${result.title || ""}`.trim();
    if (tool === "agent_wait") return `${(result.finished || []).length} ${t("готово", "done")}, ${(result.still_running || []).length} ${t("работают", "running")}`;
    if (tool === "agent_status") return `${(result.agents || []).length}`;
    if (tool === "agent_result") return result.status || "";
    if (tool === "agent_cancel") return `${result.stopped || 0}`;
    return "";
  }

  function watch(chatId, messages, mode) {
    if (!chatId) return;
    watched.set(String(chatId), { messages, mode: mode || "work", misses: 0 });
    if (!timer) timer = setInterval(poll, POLL_MS);
  }

  function owner(messages, id) {
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      const message = messages[index];
      if (message.role === "assistant" && jobsOf(message).some((job) => job.id === id)) return message;
    }
    return null;
  }

  function busy(chatId, messages) {
    if (generationJobs.has(chatId)) return true;
    return messages.some((m) => m.role === "assistant" && m.streaming);
  }

  async function poll() {
    for (const [chatId, entry] of [...watched]) {
      const seen = new Set();
      const before = S.currentId === chatId ? S.messages : ((S.chats || []).find((item) => item.id === chatId)?.messages || entry.messages || []);
      for (const message of before) if (message.role === "assistant") for (const job of jobsOf(message)) if (job.status === "running") seen.add(job);
      let rows;
      try {
        rows = (await api("/api/work/agents?session=" + encodeURIComponent(chatId))).agents || [];
      } catch (error) {
        if (++entry.misses > 20) watched.delete(chatId);
        continue;
      }
      entry.misses = 0;
      const chat = (S.chats || []).find((item) => item.id === chatId);
      const messages = S.currentId === chatId ? S.messages : (chat?.messages || entry.messages || []);
      let changed = false;
      for (const row of rows) {
        const message = owner(messages, row.id);
        if (!message) continue;
        const known = jobsOf(message).find((job) => job.id === row.id);

        const differs = ["status", "tools", "last", "report", "error", "delivered"].some((key) => known[key] !== row[key]);
        Object.assign(known, row);
        if (differs) changed = true;
      }

      for (const message of messages) {
        if (message.role !== "assistant") continue;
        for (const job of jobsOf(message)) {
          if (job.status !== "running" || !seen.has(job) || rows.some((row) => row.id === job.id && owner(messages, row.id) === message)) continue;
          Object.assign(job, { status: "cancelled", delivered: true, error: t("прервано: MemuAI был перезапущен", "interrupted: MemuAI was restarted") });
          changed = true;
        }
      }
      if (changed && S.currentId === chatId) renderMessages();
      if (changed && globalThis.MemuCode?.updateBadges) { try { globalThis.MemuCode.updateBadges(); } catch (e) {} }
      const running = rows.some((row) => row.status === "running");
      const waiting = rows.filter((row) => row.status !== "running" && !row.delivered);

      if (waiting.length && !busy(chatId, messages)) {
        await deliver(chatId, messages);
      } else if (!running && !waiting.length) {
        watched.delete(chatId);
        if (changed) persistChatSnapshot(chatId, messages);
      }
    }
    if (!watched.size && timer) { clearInterval(timer); timer = null; }
  }

  async function deliver(chatId, messages) {
    let finished;
    try {
      finished = (await api("/api/work/agents", { method: "POST", body: { session: chatId, action: "collect" } })).finished || [];
    } catch (error) { return; }
    if (!finished.length) return;
    let last = null;
    for (const row of finished) {
      const message = owner(messages, row.id);
      if (message) { merge(message, { ...row, delivered: true }); last = message; }
    }
    const report = finished.map((row) => `Agent #${row.id} "${row.title}" — ${row.status}` +
      (row.error ? ` (${row.error})` : "") + `\n${row.report || "(no report)"}`).join("\n\n");
    persistChatSnapshot(chatId, messages);
    resumeAfterAutomation("agents",
      "Background agents finished while you were not waiting for them. Check their reports against the project, " +
      "integrate the results and continue (or finish) the task.\n\n" + report,
      0, { chatId, messages, ...(last ? { assistant: last } : {}) });
  }

  async function stop(chatId, id) {
    try {
      await api("/api/work/agents", { method: "POST", body: { session: chatId, action: "cancel", ...(id !== undefined ? { id } : {}) } });
      toast(t("Агенты остановлены", "Agents stopped"));
    } catch (error) { toast(error.message || String(error), "err"); }
    poll();
  }

  function cancel(chatId) {
    if (!watched.has(String(chatId))) return;
    api("/api/work/agents", { method: "POST", body: { session: chatId, action: "cancel" } }).then(poll).catch(() => {});
  }

  function resume(chatId, messages, mode) {
    if (messages.some((m) => m.role === "assistant" && jobsOf(m).some((job) => job.status === "running" || (job.status !== "running" && job.delivered === false)))) {
      watch(chatId, messages, mode);
    }
  }

  const STATUS = { running: "running", done: "done", error: "error", cancelled: "info" };

  function seconds(value) {
    const total = Math.max(0, Math.round(+value || 0));
    return total >= 60 ? `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}` : `${total} ${t("с", "s")}`;
  }

  function stepHtml(message, index) {
    const jobs = jobsOf(message);
    if (!jobs.length) return "";
    const running = jobs.filter((job) => job.status === "running").length;
    const failed = jobs.some((job) => job.status === "error");
    const body = jobs.map((job) => {
      const state = STATUS[job.status] || "info";
      const dot = /^[a-z]{3,8}$/.test(String(job.color || "")) ? `<span class="ca-dot c-${job.color}"></span>` : "";
      const role = job.role ? `${esc(String(job.role).slice(0, 80))} · ` : "";
      const head = `${dot}<b>#${esc(job.id)} ${esc(job.title || t("Агент", "Agent"))}</b>
        ${job.agent && job.agent !== "general-purpose" ? `<span class="agent-kind">${esc(job.agent)}</span>` : ""}
        <span class="step-meta">${esc(modelDisplayName(job.model || ""))}</span>
        <span class="step-grow"></span>
        <span class="agent-state is-${state}">${esc(stepStatusText(state))}</span>`;
      const line = job.status === "running"
        ? `<div class="agent-live"><span class="step-wait"><i></i><i></i><i></i></span> ${esc(job.last || t("начинает работу", "starting"))}
            <span class="step-note">${role}${job.tools ? `${job.tools} ${t("действий", "actions")} · ` : ""}${seconds(job.seconds)}</span></div>`
        : `<div class="step-note">${role}${job.tools ? `${job.tools} ${t("действий", "actions")} · ` : ""}${seconds(job.seconds)}${job.can_write === false ? " · " + t("только чтение", "read-only") : ""}</div>`;
      const report = job.error && job.status !== "done"
        ? `<div class="msg-error">${esc(job.error)}</div>`
        : (job.report ? `<div class="substep-body">${md(job.report)}</div>` : "");
      return `<details class="substep agent-card is-${state}" ${job.status === "running" ? "open" : ""}>
        <summary>${head}</summary>${line}${report}</details>`;
    }).join("");
    return step({
      icon: "users",
      title: running ? t("Агенты работают параллельно", "Agents working in parallel") : t("Агенты", "Agents"),
      meta: running ? `${running} / ${jobs.length}` : `${jobs.length}`,
      status: running ? "running" : (failed ? "error" : "done"),
      open: running > 0,
      body,
      actions: running ? `<button type="button" class="step-btn danger" data-agents-stop="${index}">${t("Остановить агентов", "Stop the agents")}</button>` : "",
    });
  }

  return { TOOLS, isTool, allowed, prompt, onToolResult, summary, watch, resume, stop, cancel, stepHtml, poll,
    loadDefs, defsNow, invalidate, describe, toolsLabel, prepareSpawn, modelKnown };
})();
